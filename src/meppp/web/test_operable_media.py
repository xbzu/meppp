from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import ModerationMode, SiteConfiguration
from meppp.external.models import ExternalProvider, ExternalReference
from meppp.external.services import create_external_reference
from meppp.publishing.models import ContentState, Entry, VideoMimeType
from meppp.publishing.services import publish_entry
from meppp.publishing.video_processing import ProcessedVideo

from .rate_limit import RATE_LIMITS, RateLimit


def safe_processed_video() -> ProcessedVideo:
    poster = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(poster, format="WEBP", quality=82)
    content = b"server-remuxed-video"
    return ProcessedVideo(
        content=content,
        poster_content=poster.getvalue(),
        source_byte_size=len(content) + 5,
        byte_size=len(content),
        duration_ms=1_250,
        width=64,
        height=48,
        mime_type=VideoMimeType.MP4,
    )


class OperableMediaUiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.media_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        SiteConfiguration.objects.create(pk=1)
        self.member = User.objects.create_user(
            username="publisher",
            email="publisher@example.test",
            password="password",
        )
        self.client.force_login(self.member)

    def nonce(self) -> str:
        response = self.client.get(reverse("web:entry-create"))
        return response.context["form"]["nonce"].value()

    @patch("meppp.web.views.refresh_external_reference", autospec=True)
    def test_source_only_post_creates_attributed_youtube_card_without_downloading_media(
        self,
        refresh,
    ):
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "",
                "source_url": "https://youtu.be/dQw4w9WgXcQ",
                "nonce": self.nonce(),
            },
        )

        entry = Entry.objects.get(author=self.member)
        reference = ExternalReference.objects.get(entry=entry)
        self.assertRedirects(response, reverse("web:entry-detail", args=[entry.public_id]))
        self.assertEqual(entry.body, "分享了一个 YouTube 视频")
        self.assertEqual(reference.provider, ExternalProvider.YOUTUBE)
        self.assertEqual(reference.external_id, "dQw4w9WgXcQ")
        self.assertEqual(list(Path(self.media_directory.name).rglob("*.*")), [])
        refresh.assert_called_once()

        ExternalReference.objects.filter(pk=reference.pk).update(metadata_status="ready")
        detail = self.client.get(reverse("web:entry-detail", args=[entry.public_id]))
        self.assertContains(
            detail,
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        )
        self.assertContains(detail, "原始来源")

    def test_external_source_parser_rejects_arbitrary_hosts_and_mixed_local_media(self):
        bad_host = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "来源",
                "source_url": "https://example.test/watch?v=dQw4w9WgXcQ",
                "nonce": self.nonce(),
            },
        )
        mixed = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "混合媒体",
                "source_url": "https://x.com/i/status/20",
                "images": SimpleUploadedFile(
                    "one.jpg",
                    b"not-even-decoded-because-form-rejects-the-mix",
                    content_type="image/jpeg",
                ),
                "image_alt_texts": "[]",
                "nonce": self.nonce(),
            },
        )

        self.assertContains(bad_host, "当前只支持 X 和 YouTube")
        self.assertContains(mixed, "不能同时上传本地图片或视频")
        self.assertEqual(Entry.objects.count(), 0)

    @patch(
        "meppp.web.views.refresh_external_reference",
        side_effect=RuntimeError("simulated metadata outage"),
    )
    def test_metadata_outage_does_not_undo_a_confirmed_source_post(self, refresh):
        with self.assertLogs("meppp.web.views", level="ERROR"):
            response = self.client.post(
                reverse("web:entry-create"),
                {
                    "body": "稍后再补元数据",
                    "source_url": "https://x.com/i/status/20",
                    "nonce": self.nonce(),
                },
            )

        entry = Entry.objects.get(body="稍后再补元数据")
        self.assertRedirects(response, reverse("web:entry-detail", args=[entry.public_id]))
        self.assertEqual(entry.external_reference.metadata_status, "pending")
        refresh.assert_called_once()

    @patch("meppp.web.views.process_video_upload", return_value=safe_processed_video())
    def test_member_video_upload_is_persisted_and_supports_bounded_range_requests(self, process):
        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "自己的短视频",
                "video": SimpleUploadedFile(
                    "clip.mp4",
                    b"browser-upload-placeholder",
                    content_type="video/mp4",
                ),
                "nonce": self.nonce(),
            },
        )

        entry = Entry.objects.get(author=self.member)
        video = entry.video
        self.assertRedirects(response, reverse("web:entry-detail", args=[entry.public_id]))
        process.assert_called_once()

        ranged = self.client.get(
            reverse("web:video-file", args=[video.public_id]),
            HTTP_RANGE="bytes=2-6",
        )
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.headers["Content-Range"], f"bytes 2-6/{video.byte_size}")
        self.assertEqual(b"".join(ranged.streaming_content), b"rver-")
        rejected_range = self.client.get(
            reverse("web:video-file", args=[video.public_id]),
            HTTP_RANGE=f"bytes={'9' * 100}-",
        )
        self.assertEqual(rejected_range.status_code, 416)
        self.assertEqual(
            rejected_range.headers["Content-Range"],
            f"bytes */{video.byte_size}",
        )
        self.assertEqual(rejected_range.headers["Accept-Ranges"], "bytes")
        self.assertEqual(rejected_range.headers["Cache-Control"], "private, no-store")
        poster = self.client.get(reverse("web:video-poster-file", args=[video.public_id]))
        self.assertEqual(poster.status_code, 200)
        self.assertEqual(poster.headers["Content-Type"], "image/webp")
        b"".join(poster.streaming_content)
        with open(video.poster.path, "ab") as poster_file:
            poster_file.write(b"tampered")
        tampered_poster = self.client.get(reverse("web:video-poster-file", args=[video.public_id]))
        self.assertEqual(tampered_poster.status_code, 404)

    @override_settings(MEMBER_PENDING_ENTRY_LIMIT=1)
    @patch("meppp.web.views.process_video_upload", return_value=safe_processed_video())
    def test_pending_limit_is_checked_before_video_processing(self, process):
        SiteConfiguration.objects.filter(pk=1).update(moderation_mode=ModerationMode.PREMODERATION)
        Entry.objects.create(
            author=self.member,
            body="已经等待审核",
            state=ContentState.PENDING,
        )

        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "不应进入视频处理",
                "video": SimpleUploadedFile(
                    "clip.mp4",
                    b"not-processed",
                    content_type="video/mp4",
                ),
                "nonce": self.nonce(),
            },
        )

        self.assertContains(response, "待审核内容已达到上限")
        process.assert_not_called()

    @patch("meppp.web.views.process_video_upload", return_value=safe_processed_video())
    def test_video_processing_has_a_tighter_hourly_rate_limit(self, process):
        def payload():
            return {
                "body": "受限视频",
                "video": SimpleUploadedFile(
                    "clip.mp4",
                    b"video-placeholder",
                    content_type="video/mp4",
                ),
                "nonce": self.nonce(),
            }

        with patch.dict(RATE_LIMITS, {"video_process": RateLimit(1, 60)}):
            first = self.client.post(reverse("web:entry-create"), payload())
            second = self.client.post(reverse("web:entry-create"), payload())

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(process.call_count, 1)

    def test_pending_video_is_private_to_owner_and_reviewers(self):
        entry = publish_entry(
            author=self.member,
            body="等待审核的视频",
            video=safe_processed_video(),
        )
        Entry.objects.filter(pk=entry.pk).update(state=ContentState.PENDING)
        video_url = reverse("web:video-file", args=[entry.video.public_id])

        owner_response = self.client.head(video_url)
        self.client.logout()
        anonymous_response = self.client.head(video_url)

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(anonymous_response.status_code, 404)

    def test_operator_review_pages_show_video_and_external_source_evidence(self):
        video_entry = publish_entry(
            author=self.member,
            body="审核视频证据",
            video=safe_processed_video(),
        )
        source_entry = publish_entry(author=self.member, body="审核来源证据")
        create_external_reference(
            entry=source_entry,
            source_url="https://x.com/i/status/20",
            refresh=False,
        )
        Entry.objects.filter(pk__in=[video_entry.pk, source_entry.pk]).update(
            state=ContentState.PENDING
        )
        owner = User.objects.create_superuser(
            username="owner",
            email="owner@example.test",
            password="password",
        )
        self.client.force_login(owner)

        video_review = self.client.get(
            reverse("admin:publishing_pendingentry_review", args=[video_entry.pk])
        )
        source_review = self.client.get(
            reverse("admin:publishing_pendingentry_review", args=[source_entry.pk])
        )

        self.assertContains(
            video_review,
            reverse("web:video-file", args=[video_entry.video.public_id]),
        )
        self.assertContains(source_review, "https://x.com/i/status/20")
        self.assertContains(source_review, "在 X 核对原文")
