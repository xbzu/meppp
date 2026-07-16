from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import ModerationMode, SiteConfiguration
from meppp.web.models import SubmissionClaim
from meppp.web.services import DuplicateSubmission, publish_entry_once

from .image_processing import ProcessedImage, process_image_upload
from .models import Attachment, ContentState, Entry


def processed_image(*, color="green", alt_text="记录现场") -> ProcessedImage:
    source = BytesIO()
    Image.new("RGB", (64, 40), color).save(source, format="JPEG")
    return process_image_upload(
        upload=SimpleUploadedFile("../../private.php.jpg", source.getvalue()),
        max_bytes=5 * 1024 * 1024,
        alt_text=alt_text,
    )


class ImagePublishingTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.media_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.author = User.objects.create_user(username="author", password="password")
        self.other = User.objects.create_user(username="other", password="password")
        SiteConfiguration.objects.create(pk=1)

    def publish(self, *, token="nonce-1", images=None):
        return publish_entry_once(
            author=self.author,
            body="带安全图片的内容",
            topics=(),
            purpose="entry:create",
            token=token,
            images=images or [processed_image()],
        )

    def test_publish_persists_only_canonical_server_generated_webp(self):
        entry = self.publish()
        attachment = entry.attachments.get()

        self.assertEqual(attachment.mime_type, "image/webp")
        self.assertEqual(attachment.byte_size, attachment.file.size)
        self.assertEqual((attachment.width, attachment.height), (64, 40))
        self.assertEqual(
            attachment.file.name,
            f"entries/{entry.public_id}/{attachment.public_id}.webp",
        )
        self.assertNotIn("private", attachment.file.name)
        self.assertEqual(os.stat(attachment.file.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(Path(attachment.file.path).parent).st_mode & 0o777, 0o700)

    def test_duplicate_nonce_creates_one_entry_and_one_file(self):
        self.publish(token="same")
        with self.assertRaises(DuplicateSubmission):
            self.publish(token="same")

        self.assertEqual(Entry.objects.count(), 1)
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(len(list(Path(self.media_directory.name).rglob("*.webp"))), 1)

    def test_database_failure_removes_every_written_file_and_nonce_claim(self):
        original_save = Attachment.save
        calls = 0

        def fail_second_save(instance, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntegrityError("simulated attachment failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(Attachment, "save", new=fail_second_save):
            with self.assertRaises(IntegrityError):
                self.publish(images=[processed_image(), processed_image(color="blue")])

        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(SubmissionClaim.objects.count(), 0)
        self.assertEqual(list(Path(self.media_directory.name).rglob("*.webp")), [])
        self.assertEqual(list(Path(self.media_directory.name).rglob(".meppp-upload-*")), [])

    def test_latest_configuration_is_rechecked_before_writing(self):
        image = processed_image()
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.max_images_per_post = 0
        configuration.save()

        with self.assertRaisesMessage(ValidationError, "图片数量"):
            self.publish(images=[image])

        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(list(Path(self.media_directory.name).rglob("*.webp")), [])

    def test_runtime_hard_caps_survive_an_invalid_in_memory_configuration(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.max_images_per_post = 10
        configuration.upload_max_bytes = 20 * 1024 * 1024
        oversized = replace(
            processed_image(),
            source_byte_size=5 * 1024 * 1024 + 1,
        )

        with patch(
            "meppp.publishing.services._configuration_for_write",
            return_value=configuration,
        ):
            with self.assertRaisesMessage(ValidationError, "图片大小"):
                self.publish(images=[oversized])
            with self.assertRaisesMessage(ValidationError, "图片数量"):
                self.publish(
                    token="nonce-2",
                    images=[processed_image() for _ in range(5)],
                )

        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(list(Path(self.media_directory.name).rglob("*.webp")), [])

    def test_atomic_storage_removes_partial_temporary_file(self):
        class ExplodingContent(ContentFile):
            def chunks(self, chunk_size=None):
                yield b"first"
                raise OSError("simulated stream failure")

        field = Attachment._meta.get_field("file")
        with self.assertRaises(OSError):
            field.storage.save("entries/test/image.webp", ExplodingContent(b"ignored"))

        self.assertEqual(list(Path(self.media_directory.name).rglob("*upload*")), [])
        self.assertFalse(Path(self.media_directory.name, "entries/test/image.webp").exists())

    def test_atomic_storage_removes_final_link_when_directory_sync_fails(self):
        real_fsync = os.fsync
        sync_calls = 0

        def fail_first_directory_sync(descriptor):
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 2:
                raise OSError("simulated directory sync failure")
            return real_fsync(descriptor)

        field = Attachment._meta.get_field("file")
        with (
            patch("meppp.publishing.storage.os.fsync", side_effect=fail_first_directory_sync),
            self.assertRaises(OSError),
        ):
            field.storage.save("entries/test/image.webp", ContentFile(b"complete"))

        self.assertEqual(list(Path(self.media_directory.name).rglob("*upload*")), [])
        self.assertFalse(Path(self.media_directory.name, "entries/test/image.webp").exists())

    def test_media_route_rejects_noncanonical_paths_and_size_drift(self):
        first_entry = self.publish()
        first_attachment = first_entry.attachments.get()
        first_url = reverse("web:attachment-file", args=[first_attachment.public_id])
        Attachment.objects.filter(pk=first_attachment.pk).update(file="entries/tampered.webp")
        self.assertEqual(self.client.get(first_url).status_code, 404)

        second_entry = self.publish(token="nonce-2")
        second_attachment = second_entry.attachments.get()
        second_url = reverse("web:attachment-file", args=[second_attachment.public_id])
        with open(second_attachment.file.path, "ab") as media_file:
            media_file.write(b"tampered")
        self.assertEqual(self.client.get(second_url).status_code, 404)

    def test_media_route_follows_entry_state_author_and_reviewer_permission(self):
        entry = self.publish()
        attachment = entry.attachments.get()
        url = reverse("web:attachment-file", args=[attachment.public_id])

        public_response = self.client.get(url)
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response.headers["Content-Type"], "image/webp")
        self.assertIn("private", public_response.headers["Cache-Control"])
        self.assertIn("no-store", public_response.headers["Cache-Control"])
        self.assertEqual(public_response.headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(b"".join(public_response.streaming_content), attachment.file.read())

        entry.state = ContentState.PENDING
        entry.save(update_fields=("state", "updated_at"))
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(url).status_code, 200)

        staff_without_permission = User.objects.create_user(
            username="unprivileged-staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(staff_without_permission)
        self.assertEqual(self.client.get(url).status_code, 404)

        reviewer = User.objects.create_user(
            username="reviewer",
            password="password",
            is_staff=True,
        )
        reviewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="publishing", codename="change_entry")
        )
        self.client.force_login(reviewer)
        self.assertEqual(self.client.get(url).status_code, 200)

        entry.state = ContentState.HIDDEN
        entry.save(update_fields=("state", "updated_at"))
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(reviewer)
        self.assertEqual(self.client.get(url).status_code, 200)

        entry.state = ContentState.PUBLISHED
        entry.save(update_fields=("state", "updated_at"))
        self.author.is_active = False
        self.author.save(update_fields=("is_active",))
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)

        self.assertEqual(
            self.client.get(f"/media/{attachment.file.name}").status_code,
            404,
        )

    def test_pending_review_page_displays_processed_image_to_reviewer(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.moderation_mode = ModerationMode.PREMODERATION
        configuration.save()
        entry = self.publish()
        attachment = entry.attachments.get()
        reviewer = User.objects.create_user(
            username="reviewer",
            password="password",
            is_staff=True,
        )
        reviewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="publishing", codename="change_entry")
        )
        self.client.force_login(reviewer)

        response = self.client.get(reverse("admin:publishing_pendingentry_review", args=[entry.pk]))

        self.assertContains(response, reverse("web:attachment-file", args=[attachment.public_id]))
        self.assertContains(response, "记录现场")

    def test_alt_text_is_escaped_and_empty_alt_stays_empty(self):
        first = processed_image(alt_text='<script>alert("x")</script>')
        second = processed_image(color="blue", alt_text="")
        self.publish(images=[first, second])

        response = self.client.get(reverse("web:home"))

        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;alert(&quot;")
        self.assertContains(response, 'alt=""', count=1)
