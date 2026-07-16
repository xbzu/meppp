from __future__ import annotations

import json
import tempfile
from io import BytesIO
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import SiteConfiguration
from meppp.publishing.models import Attachment, Entry

from .forms import EntryForm
from .models import SubmissionClaim


def upload_image(*, name="source.jpg", image_format="JPEG", color="green"):
    content = BytesIO()
    Image.new("RGB", (70, 50), color).save(content, format=image_format)
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


class MemberImageUploadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.media_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.member = User.objects.create_user(username="member", password="password")
        SiteConfiguration.objects.create(pk=1)
        self.client.force_login(self.member)

    def nonce(self):
        response = self.client.get(reverse("web:entry-create"))
        return response.context["form"]["nonce"].value()

    def test_member_can_publish_four_images_with_ordered_alt_text(self):
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "四张图片按选择顺序保存",
                "images": [
                    upload_image(name="one.jpg", color="red"),
                    upload_image(name="two.jpg", color="blue"),
                    upload_image(name="three.jpg", color="yellow"),
                    upload_image(name="four.jpg", color="purple"),
                ],
                "image_alt_texts": json.dumps(["第一张", "第二张", "", "第四张"]),
                "nonce": self.nonce(),
            },
        )

        entry = Entry.objects.get(author=self.member)
        self.assertRedirects(response, reverse("web:entry-detail", args=[entry.public_id]))
        self.assertEqual(
            list(entry.attachments.values_list("position", "alt_text")),
            [(0, "第一张"), (1, "第二张"), (2, ""), (3, "第四张")],
        )
        self.assertTrue(
            all(name.endswith(".webp") for name in entry.attachments.values_list("file", flat=True))
        )

    def test_invalid_image_does_not_consume_nonce_or_create_files(self):
        token = self.nonce()
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "伪装图片",
                "images": SimpleUploadedFile(
                    "attack.jpg",
                    b"<html><script>attack</script></html>",
                    content_type="image/jpeg",
                ),
                "image_alt_texts": "[]",
                "nonce": token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不是可安全处理")
        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(SubmissionClaim.objects.count(), 0)
        self.assertEqual(list(Path(self.media_directory.name).rglob("*.webp")), [])

    def test_oversized_file_is_rejected_before_image_decoding(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.upload_max_bytes = 128 * 1024
        configuration.save()
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "超大文件",
                "images": SimpleUploadedFile("large.jpg", b"x" * (128 * 1024 + 1)),
                "image_alt_texts": "[]",
                "nonce": self.nonce(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "每张图片不能超过")
        self.assertEqual(Entry.objects.count(), 0)

    def test_form_exposes_server_hard_caps_even_if_configuration_object_drifted(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.max_images_per_post = 10
        configuration.upload_max_bytes = 20 * 1024 * 1024

        form = EntryForm(configuration=configuration)

        self.assertEqual(form.maximum_images, 4)
        self.assertEqual(form.maximum_image_bytes, 5 * 1024 * 1024)
        self.assertEqual(form.fields["images"].widget.attrs["data-max-images"], "4")
        self.assertEqual(
            form.fields["images"].widget.attrs["data-max-bytes"],
            str(5 * 1024 * 1024),
        )

    def test_form_hides_media_and_source_fields_disabled_by_site_configuration(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.max_images_per_post = 0
        configuration.video_uploads_enabled = False
        configuration.x_references_enabled = False
        configuration.youtube_references_enabled = False

        form = EntryForm(configuration=configuration)

        self.assertNotIn("images", form.fields)
        self.assertNotIn("video", form.fields)
        self.assertNotIn("source_url", form.fields)

    def test_form_rejects_only_the_disabled_external_provider(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.x_references_enabled = False
        x_form = EntryForm(
            data={
                "body": "",
                "source_url": "https://x.com/i/status/20",
                "nonce": "x-disabled",
            },
            configuration=configuration,
        )
        youtube_form = EntryForm(
            data={
                "body": "",
                "source_url": "https://youtu.be/dQw4w9WgXcQ",
                "nonce": "youtube-enabled",
            },
            configuration=configuration,
        )

        self.assertFalse(x_form.is_valid())
        self.assertIn("source_url", x_form.errors)
        self.assertTrue(youtube_form.is_valid(), youtube_form.errors)

        configuration.x_references_enabled = True
        configuration.youtube_references_enabled = False
        youtube_form = EntryForm(
            data={
                "body": "",
                "source_url": "https://youtu.be/dQw4w9WgXcQ",
                "nonce": "youtube-disabled",
            },
            configuration=configuration,
        )
        x_form = EntryForm(
            data={
                "body": "",
                "source_url": "https://x.com/i/status/20",
                "nonce": "x-enabled",
            },
            configuration=configuration,
        )

        self.assertFalse(youtube_form.is_valid())
        self.assertIn("source_url", youtube_form.errors)
        self.assertTrue(x_form.is_valid(), x_form.errors)

    def test_image_only_and_video_only_forms_allow_an_empty_body(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        image_form = EntryForm(
            data={"body": "", "nonce": "image-only"},
            files={"images": upload_image()},
            configuration=configuration,
        )
        video_form = EntryForm(
            data={"body": "", "nonce": "video-only"},
            files={
                "video": SimpleUploadedFile(
                    "clip.mp4",
                    b"non-empty-video-placeholder",
                    content_type="video/mp4",
                )
            },
            configuration=configuration,
        )

        self.assertTrue(image_form.is_valid(), image_form.errors)
        self.assertTrue(video_form.is_valid(), video_form.errors)

    def test_malformed_alt_text_state_fails_closed(self):
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "说明字段被篡改",
                "images": upload_image(),
                "image_alt_texts": '{"not": "a list"}',
                "nonce": self.nonce(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "图片说明格式无效")
        self.assertEqual(Entry.objects.count(), 0)

    def test_text_only_post_remains_supported(self):
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "仍然可以只发文字",
                "image_alt_texts": "[]",
                "nonce": self.nonce(),
            },
        )

        entry = Entry.objects.get(author=self.member)
        self.assertRedirects(response, reverse("web:entry-detail", args=[entry.public_id]))
        self.assertFalse(entry.attachments.exists())
