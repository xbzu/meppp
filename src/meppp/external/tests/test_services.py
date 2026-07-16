from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from meppp.accounts.models import User
from meppp.publishing.models import Entry

from ..admin import ExternalReferenceAdmin
from ..models import ExternalReference, MetadataStatus
from ..oembed import ExternalMetadata, ExternalMetadataError, ExternalSourceUnavailable
from ..services import create_external_reference, refresh_external_reference


class _StaticClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def fetch(self, parsed):
        self.calls.append(parsed)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ExternalReferenceServiceTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="external-author")
        self.entry = Entry.objects.create(author=self.author, body="转发内容")

    def test_create_parses_source_and_builds_one_to_one_pending_reference(self):
        reference = create_external_reference(
            entry=self.entry,
            source_url="https://twitter.com/jack/status/20?s=20",
            refresh=False,
        )

        self.assertEqual(reference, self.entry.external_reference)
        self.assertEqual(reference.provider, "x")
        self.assertEqual(reference.external_id, "20")
        self.assertEqual(reference.canonical_url, "https://x.com/i/status/20")
        self.assertEqual(reference.metadata_status, MetadataStatus.PENDING)

    def test_refresh_stores_only_attribution_and_plain_text_metadata(self):
        reference = create_external_reference(
            entry=self.entry,
            source_url="https://x.com/jack/status/20",
            refresh=False,
        )
        now = timezone.now()
        metadata = ExternalMetadata(
            canonical_url="https://x.com/jack/status/20",
            author_name="jack",
            author_url="https://x.com/jack",
            title="",
            excerpt="plain post text",
            expires_after=timedelta(hours=4),
        )

        refreshed = refresh_external_reference(reference, client=_StaticClient(metadata), now=now)

        self.assertEqual(refreshed.metadata_status, MetadataStatus.READY)
        self.assertEqual(refreshed.canonical_url, metadata.canonical_url)
        self.assertEqual(refreshed.author_name, metadata.author_name)
        self.assertEqual(refreshed.excerpt, metadata.excerpt)
        self.assertEqual(refreshed.fetched_at, now)
        self.assertEqual(refreshed.expires_at, now + timedelta(hours=4))
        field_names = {field.name for field in ExternalReference._meta.concrete_fields}
        self.assertNotIn("html", field_names)
        self.assertNotIn("media", field_names)
        self.assertNotIn("thumbnail_url", field_names)

    def test_unavailable_source_clears_stale_metadata_and_remains_usable(self):
        reference = create_external_reference(
            entry=self.entry,
            source_url="https://youtu.be/lJIrF4YjHfQ",
            refresh=False,
        )
        reference.title = "stale title"
        reference.author_name = "stale author"
        reference.metadata_status = MetadataStatus.READY
        reference.save()
        now = timezone.now()

        refreshed = refresh_external_reference(
            reference,
            client=_StaticClient(ExternalSourceUnavailable("gone")),
            now=now,
        )

        self.assertEqual(refreshed.metadata_status, MetadataStatus.UNAVAILABLE)
        self.assertEqual(refreshed.title, "")
        self.assertEqual(refreshed.author_name, "")
        self.assertEqual(refreshed.fetched_at, now)
        self.assertGreater(refreshed.expires_at, now)

    def test_transient_failure_preserves_last_good_metadata(self):
        reference = create_external_reference(
            entry=self.entry,
            source_url="https://youtu.be/lJIrF4YjHfQ",
            refresh=False,
        )
        previous_fetch = timezone.now() - timedelta(hours=1)
        reference.title = "last good title"
        reference.author_name = "last good author"
        reference.metadata_status = MetadataStatus.READY
        reference.fetched_at = previous_fetch
        reference.save()
        now = timezone.now()

        refreshed = refresh_external_reference(
            reference,
            client=_StaticClient(ExternalMetadataError("timeout")),
            now=now,
        )

        self.assertEqual(refreshed.metadata_status, MetadataStatus.ERROR)
        self.assertEqual(refreshed.title, "last good title")
        self.assertEqual(refreshed.author_name, "last good author")
        self.assertEqual(refreshed.fetched_at, previous_fetch)
        self.assertGreater(refreshed.expires_at, now)

    def test_recreating_same_source_preserves_verified_canonical_metadata(self):
        reference = create_external_reference(
            entry=self.entry,
            source_url="https://x.com/jack/status/20",
            refresh=False,
        )
        reference.canonical_url = "https://x.com/jack/status/20"
        reference.author_name = "jack"
        reference.metadata_status = MetadataStatus.READY
        reference.save()

        unchanged = create_external_reference(
            entry=self.entry,
            source_url="https://twitter.com/jack/status/20?s=20",
            refresh=False,
        )

        self.assertEqual(unchanged.pk, reference.pk)
        self.assertEqual(unchanged.canonical_url, "https://x.com/jack/status/20")
        self.assertEqual(unchanged.author_name, "jack")
        self.assertEqual(unchanged.metadata_status, MetadataStatus.READY)

    def test_model_rejects_mismatched_provider_and_external_id(self):
        reference = ExternalReference(
            entry=self.entry,
            provider="youtube",
            external_id="different-id",
            canonical_url="https://x.com/jack/status/20",
        )

        with self.assertRaises(ValidationError):
            reference.full_clean()


class ExternalReferenceCommandTests(TestCase):
    def setUp(self):
        author = User.objects.create_user(username="external-command-author")
        now = timezone.now()
        self.due = []
        for position, expires_at in enumerate((None, now - timedelta(minutes=1))):
            entry = Entry.objects.create(author=author, body=f"due {position}")
            reference = create_external_reference(
                entry=entry,
                source_url=f"https://x.com/i/status/{20 + position}",
                refresh=False,
            )
            reference.expires_at = expires_at
            reference.save(update_fields=["expires_at", "updated_at"])
            self.due.append(reference)
        future_entry = Entry.objects.create(author=author, body="future")
        future = create_external_reference(
            entry=future_entry,
            source_url="https://x.com/i/status/99",
            refresh=False,
        )
        future.expires_at = now + timedelta(hours=1)
        future.save(update_fields=["expires_at", "updated_at"])

    def test_command_refreshes_only_due_small_batch(self):
        output = StringIO()
        command_target = (
            "meppp.external.management.commands.refresh_external_references."
            "refresh_external_reference"
        )
        with patch(command_target, side_effect=lambda reference, **kwargs: reference) as refresh:
            call_command("refresh_external_references", limit=2, stdout=output)

        self.assertEqual(refresh.call_count, 2)
        refreshed_ids = {call.args[0].pk for call in refresh.call_args_list}
        self.assertEqual(refreshed_ids, {reference.pk for reference in self.due})
        self.assertIn("processed=2", output.getvalue())

    def test_command_rejects_unbounded_limit(self):
        with self.assertRaises(CommandError):
            call_command("refresh_external_references", limit=101, stdout=StringIO())


class ExternalReferenceAdminTests(TestCase):
    def test_admin_requires_explicit_view_permission_and_is_read_only(self):
        admin = ExternalReferenceAdmin(ExternalReference, AdminSite())
        request = RequestFactory().get("/admin/external/externalreference/")
        request.user = User.objects.create_user(
            username="external-staff",
            is_staff=True,
        )

        self.assertFalse(admin.has_view_permission(request))

        permission = Permission.objects.get(
            codename="view_externalreference",
            content_type__app_label="external",
        )
        request.user.user_permissions.add(permission)
        request.user = User.objects.get(pk=request.user.pk)

        self.assertTrue(admin.has_view_permission(request))
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))
