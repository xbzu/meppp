from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from meppp.accounts.member_services import process_member_avatar, update_member_profile
from meppp.accounts.models import Profile, User
from meppp.audit.models import AuditEvent
from meppp.configuration.models import SiteConfiguration


def avatar_upload(*, name="portrait.jpg", size=(900, 500), color="navy"):
    content = BytesIO()
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010E] = "private source metadata"
    image.save(content, format="JPEG", exif=exif)
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


class AvatarUploadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=Path(self.media_directory.name),
            MEDIA_MIN_FREE_BYTES=0,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.member = User.objects.create_user(username="member", password="password")
        SiteConfiguration.objects.create(pk=1)
        self.client.force_login(self.member)

    def settings_payload(self, **extra):
        return {"display_name": "成员", "bio": "个人简介", **extra}

    def upload_avatar(self, **upload_options):
        return self.client.post(
            reverse("web:member-settings"),
            self.settings_payload(avatar_upload=avatar_upload(**upload_options)),
        )

    def test_upload_is_square_metadata_free_webp_and_is_audited(self):
        response = self.upload_avatar()

        self.assertRedirects(response, reverse("web:member-settings"))
        profile = Profile.objects.get(user=self.member)
        self.assertEqual(
            profile.avatar.name,
            f"avatars/{profile.public_id}/{profile.avatar_version}.webp",
        )
        self.assertNotIn("portrait", profile.avatar.name)
        self.assertEqual((profile.avatar_width, profile.avatar_height), (512, 512))
        self.assertEqual(profile.avatar_byte_size, Path(profile.avatar.path).stat().st_size)
        with Image.open(profile.avatar.path, formats=("WEBP",)) as image:
            image.load()
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (512, 512))
            self.assertFalse(image.getexif())
            self.assertEqual(getattr(image, "n_frames", 1), 1)
        event = AuditEvent.objects.get(action="account.profile.updated")
        self.assertEqual(event.metadata["changed_fields"], ["display_name", "bio", "avatar"])
        self.assertNotIn("portrait", str(event.metadata))
        self.assertNotIn(profile.avatar.name, str(event.metadata))

    def test_replacement_and_removal_keep_old_revisions_for_backup_window(self):
        self.upload_avatar(color="navy")
        profile = Profile.objects.get(user=self.member)
        first_path = Path(profile.avatar.path)
        first_name = profile.avatar.name

        self.upload_avatar(name="replacement.png", color="gold")
        profile.refresh_from_db()
        second_path = Path(profile.avatar.path)
        self.assertNotEqual(profile.avatar.name, first_name)
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

        response = self.client.post(
            reverse("web:member-settings"),
            self.settings_payload(remove_avatar="on"),
        )

        self.assertRedirects(response, reverse("web:member-settings"))
        profile.refresh_from_db()
        self.assertFalse(profile.avatar)
        self.assertIsNone(profile.avatar_version)
        self.assertIsNone(profile.avatar_byte_size)
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_invalid_upload_and_conflicting_remove_preserve_current_avatar(self):
        self.upload_avatar()
        profile = Profile.objects.get(user=self.member)
        original_name = profile.avatar.name

        response = self.client.post(
            reverse("web:member-settings"),
            self.settings_payload(
                avatar_upload=SimpleUploadedFile(
                    "fake.jpg",
                    b"<svg onload=alert(1)>",
                    content_type="image/jpeg",
                )
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "文件不是可安全处理")
        profile.refresh_from_db()
        self.assertEqual(profile.avatar.name, original_name)

        response = self.client.post(
            reverse("web:member-settings"),
            self.settings_payload(
                avatar_upload=avatar_upload(),
                remove_avatar="on",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不能同时选择")
        profile.refresh_from_db()
        self.assertEqual(profile.avatar.name, original_name)

    def test_database_failure_cleans_new_file_and_preserves_old_pointer(self):
        self.upload_avatar()
        profile = Profile.objects.get(user=self.member)
        original_name = profile.avatar.name
        original_files = set(Path(self.media_directory.name, "avatars").rglob("*.webp"))
        processed = process_member_avatar(upload=avatar_upload(color="purple"))

        with patch(
            "meppp.accounts.member_services.record_event",
            side_effect=RuntimeError("audit unavailable"),
        ), self.assertRaisesMessage(RuntimeError, "audit unavailable"):
            update_member_profile(
                member=self.member,
                display_name=profile.display_name,
                bio=profile.bio,
                avatar=processed,
            )

        profile.refresh_from_db()
        self.assertEqual(profile.avatar.name, original_name)
        self.assertEqual(
            set(Path(self.media_directory.name, "avatars").rglob("*.webp")),
            original_files,
        )

    def test_avatar_switch_blocks_new_upload_but_keeps_removal_available(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.avatar_uploads_enabled = False
        configuration.save()

        response = self.client.get(reverse("web:member-settings"))
        self.assertNotContains(response, 'name="avatar_upload"')

        with self.assertRaisesMessage(ValidationError, "关闭了新头像上传"):
            process_member_avatar(upload=avatar_upload())


class AvatarFileRouteTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=Path(self.media_directory.name),
            MEDIA_MIN_FREE_BYTES=0,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.member = User.objects.create_user(username="reader", password="password")
        SiteConfiguration.objects.create(pk=1)
        processed = process_member_avatar(upload=avatar_upload())
        self.profile = update_member_profile(
            member=self.member,
            display_name="Reader",
            bio="",
            avatar=processed,
        )
        self.url = reverse("web:avatar-file", args=[self.member.public_id])

    def test_active_member_avatar_is_served_with_controlled_headers(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/webp")
        self.assertEqual(
            int(response.headers["Content-Length"]),
            self.profile.avatar_byte_size,
        )
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(
            response.headers["Cross-Origin-Resource-Policy"],
            "same-origin",
        )
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(len(b"".join(response.streaming_content)), self.profile.avatar_byte_size)
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_missing_inactive_and_drifted_avatar_are_not_served(self):
        self.member.is_active = False
        self.member.save(update_fields=("is_active",))
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.member.is_active = True
        self.member.save(update_fields=("is_active",))

        Profile.objects.filter(pk=self.profile.pk).update(
            avatar_byte_size=self.profile.avatar_byte_size + 1
        )
        self.assertEqual(self.client.get(self.url).status_code, 404)

        Profile.objects.filter(pk=self.profile.pk).update(
            avatar_byte_size=self.profile.avatar_byte_size
        )
        Path(self.profile.avatar.path).unlink()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_avatar_is_rendered_on_all_public_identity_surfaces(self):
        from meppp.publishing.models import Comment, Entry

        entry = Entry.objects.create(author=self.member, body="avatar surface")
        Comment.objects.create(entry=entry, author=self.member, body="comment avatar")
        expected_url = self.url

        for url in (
            reverse("web:home"),
            reverse("web:entry-detail", args=[entry.public_id]),
            reverse("web:member-profile", args=[self.member.public_id]),
        ):
            response = self.client.get(url)
            self.assertContains(response, expected_url)
