from __future__ import annotations

import json
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import ModerationMode, RegistrationMode, SiteConfiguration
from meppp.external.models import ExternalReference
from meppp.publishing.models import Attachment, ContentState, Entry, Topic


def image_upload(*, name="inline.jpg", color="navy"):
    content = BytesIO()
    Image.new("RGB", (80, 60), color).save(content, format="JPEG")
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


class HomeInlineComposerTests(TestCase):
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
        self.configuration = SiteConfiguration.objects.create(
            pk=1,
            registration_mode=RegistrationMode.OPEN,
        )
        self.member = User.objects.create_user(username="inline", password="password")
        self.topic = Topic.objects.create(slug="inline", label="本页发布")

    def login(self):
        self.client.force_login(self.member)

    def home_nonce(self) -> str:
        response = self.client.get(reverse("web:home"))
        return response.context["composer_form"]["nonce"].value()

    def test_guest_sees_join_prompt_and_cannot_post_to_home(self):
        response = self.client.get(reverse("web:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-publishing-form")
        self.assertContains(response, "免费注册")

        response = self.client.post(reverse("web:home"), {"body": "blocked"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("web:login")))
        self.assertEqual(Entry.objects.count(), 0)

    def test_member_gets_one_real_home_form_and_write_fallback_still_exists(self):
        self.login()

        response = self.client.get(reverse("web:home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b"data-publishing-form"), 1)
        self.assertContains(response, 'action="/"')
        self.assertContains(response, "data-inline-composer")
        self.assertContains(response, 'aria-label="发布图片"')
        self.assertContains(response, 'aria-label="发布视频"')
        self.assertContains(response, 'aria-label="分享 X 来源"')
        self.assertContains(response, 'aria-label="分享 YouTube 来源"')
        self.assertContains(response, 'href="/#home-composer"')
        self.assertNotContains(response, 'href="/write/"')
        self.assertContains(response, 'role="button" href="#composer-images"')
        dashboard = self.client.get(reverse("web:member-dashboard"))
        self.assertContains(dashboard, 'href="/#home-composer"')
        self.assertEqual(self.client.get(reverse("web:entry-create")).status_code, 200)

    def test_text_and_topic_publish_redirect_back_to_home(self):
        self.login()
        response = self.client.post(
            reverse("web:home"),
            {
                "body": "  首页直接发布  ",
                "topics": [self.topic.pk],
                "nonce": self.home_nonce(),
                "author": "forged",
                "state": ContentState.HIDDEN,
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("web:home"))
        entry = Entry.objects.get(body="首页直接发布")
        self.assertEqual(entry.author, self.member)
        self.assertEqual(entry.state, ContentState.PUBLISHED)
        self.assertEqual(list(entry.topics.all()), [self.topic])
        self.assertContains(response, "内容已经发布")
        self.assertContains(response, "首页直接发布")

    def test_invalid_post_stays_on_home_with_feed_and_reusable_nonce(self):
        self.login()
        existing = Entry.objects.create(author=self.member, body="原来的信息流")
        nonce = self.home_nonce()

        response = self.client.post(
            reverse("web:home"),
            {"body": "", "nonce": nonce},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["composer_form"]["nonce"].value(), nonce)
        self.assertContains(response, "请填写正文，或添加图片")
        self.assertContains(response, existing.body)
        self.assertContains(response, "data-inline-composer")

        response = self.client.post(
            reverse("web:home"),
            {"body": "修正后成功", "nonce": nonce},
        )
        self.assertRedirects(response, reverse("web:home"))
        self.assertTrue(Entry.objects.filter(body="修正后成功").exists())

    def test_home_and_write_share_the_same_nonce_and_submission_claim(self):
        self.login()
        nonce = self.home_nonce()
        payload = {"body": "跨入口只创建一次", "nonce": nonce}

        self.client.post(reverse("web:home"), payload)
        response = self.client.post(reverse("web:entry-create"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已经处理过")
        self.assertEqual(Entry.objects.filter(body="跨入口只创建一次").count(), 1)

    def test_premoderated_home_post_returns_home_without_public_card(self):
        self.configuration.moderation_mode = ModerationMode.PREMODERATION
        self.configuration.save()
        self.login()

        response = self.client.post(
            reverse("web:home"),
            {"body": "首页待审核", "nonce": self.home_nonce()},
            follow=True,
        )

        entry = Entry.objects.get(body="首页待审核")
        self.assertEqual(entry.state, ContentState.PENDING)
        self.assertRedirects(response, reverse("web:home"))
        self.assertContains(response, "已提交审核")
        self.assertNotContains(response, "首页待审核")

    def test_safe_image_publishes_inline_and_invalid_image_leaves_no_file(self):
        self.login()
        response = self.client.post(
            reverse("web:home"),
            {
                "body": "首页图片",
                "images": [image_upload()],
                "image_alt_texts": json.dumps(["蓝色方块"]),
                "nonce": self.home_nonce(),
            },
        )

        self.assertRedirects(response, reverse("web:home"))
        attachment = Attachment.objects.get(entry__body="首页图片")
        self.assertEqual(attachment.alt_text, "蓝色方块")
        self.assertTrue(Path(attachment.file.path).is_file())

        nonce = self.home_nonce()
        response = self.client.post(
            reverse("web:home"),
            {
                "body": "伪装图片",
                "images": [
                    SimpleUploadedFile(
                        "fake.jpg",
                        b"<script>alert(1)</script>",
                        content_type="image/jpeg",
                    )
                ],
                "image_alt_texts": "[]",
                "nonce": nonce,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "文件不是可安全处理")
        self.assertFalse(Entry.objects.filter(body="伪装图片").exists())

    @patch("meppp.web.views.refresh_external_reference")
    def test_x_and_youtube_source_cards_publish_from_home(self, refresh):
        self.login()
        for source_url, expected_provider, expected_body in (
            ("https://x.com/i/status/20", "x", "分享了一条 X 动态"),
            (
                "https://youtu.be/dQw4w9WgXcQ",
                "youtube",
                "分享了一个 YouTube 视频",
            ),
        ):
            response = self.client.post(
                reverse("web:home"),
                {"body": "", "source_url": source_url, "nonce": self.home_nonce()},
            )
            self.assertRedirects(response, reverse("web:home"))
            reference = ExternalReference.objects.get(provider=expected_provider)
            self.assertEqual(reference.entry.body, expected_body)
        self.assertEqual(refresh.call_count, 2)

    def test_feature_switches_remove_disabled_home_tools(self):
        self.configuration.max_images_per_post = 0
        self.configuration.video_uploads_enabled = False
        self.configuration.x_references_enabled = False
        self.configuration.youtube_references_enabled = False
        self.configuration.save()
        self.login()

        response = self.client.get(reverse("web:home"))

        self.assertNotContains(response, 'aria-label="发布图片"')
        self.assertNotContains(response, 'aria-label="发布视频"')
        self.assertNotContains(response, 'aria-label="分享 X 来源"')
        self.assertNotContains(response, 'aria-label="分享 YouTube 来源"')
        self.assertNotContains(response, 'name="source_url"')

    def test_home_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.member)

        response = csrf_client.post(reverse("web:home"), {"body": "blocked"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Entry.objects.count(), 0)
