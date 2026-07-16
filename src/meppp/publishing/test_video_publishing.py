from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import ModerationMode, SiteConfiguration

from .models import Entry, VideoAsset, VideoMimeType
from .services import publish_entry
from .video_processing import ProcessedVideo


def processed_video(*, mime_type=VideoMimeType.MP4) -> ProcessedVideo:
    poster = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(poster, format="WEBP", quality=82)
    content = b"server-remuxed-video"
    return ProcessedVideo(
        content=content,
        poster_content=poster.getvalue(),
        source_byte_size=len(content) + 5,
        byte_size=len(content),
        duration_ms=1250,
        width=64,
        height=48,
        mime_type=mime_type,
    )


class VideoPublishingTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.media_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.author = User.objects.create_user(username="video-author", password="password")
        SiteConfiguration.objects.create(pk=1)

    def publish(self, *, video=None):
        return publish_entry(
            author=self.author,
            body="带安全视频的内容",
            topics=(),
            images=(),
            video=video or processed_video(),
        )

    def test_publish_persists_canonical_video_and_poster_metadata(self):
        entry = self.publish()
        asset = entry.video

        self.assertEqual(asset.mime_type, VideoMimeType.MP4)
        self.assertEqual(asset.byte_size, asset.file.size)
        self.assertEqual(asset.poster_byte_size, asset.poster.size)
        self.assertEqual(asset.duration_ms, 1250)
        self.assertEqual((asset.width, asset.height), (64, 48))
        self.assertEqual(asset.file.name, f"entries/{entry.public_id}/{asset.public_id}.mp4")
        self.assertEqual(
            asset.poster.name,
            f"entries/{entry.public_id}/{asset.public_id}-poster.webp",
        )
        self.assertEqual(os.stat(asset.file.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(asset.poster.path).st_mode & 0o777, 0o600)

    def test_webm_uses_the_canonical_webm_extension(self):
        entry = self.publish(video=processed_video(mime_type=VideoMimeType.WEBM))

        self.assertTrue(entry.video.file.name.endswith(".webm"))

    def test_database_failure_rolls_back_records_and_removes_both_files(self):
        with (
            patch.object(VideoAsset, "save", side_effect=IntegrityError("simulated failure")),
            self.assertRaises(IntegrityError),
        ):
            self.publish()

        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(VideoAsset.objects.count(), 0)
        self.assertEqual(
            [path for path in Path(self.media_directory.name).rglob("*") if path.is_file()],
            [],
        )

    def test_rejects_forged_or_out_of_contract_processed_video_before_writing(self):
        invalid_videos = [
            object(),
            replace(processed_video(), byte_size=999),
            replace(processed_video(), duration_ms=5 * 60 * 1000 + 1),
            replace(processed_video(), width=0),
            replace(processed_video(), width=8193),
            replace(processed_video(), mime_type="video/quicktime"),
            replace(processed_video(), poster_content=b"not-webp"),
        ]

        for video in invalid_videos:
            with self.subTest(video=video), self.assertRaises(ValidationError):
                self.publish(video=video)

        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(VideoAsset.objects.count(), 0)

    def test_database_constraints_and_lifecycle_guards_remain_enforced(self):
        asset = self.publish().video

        with (
            transaction.atomic(),
            self.assertRaises(IntegrityError),
        ):
            VideoAsset.objects.filter(pk=asset.pk).update(duration_ms=0)
        with (
            transaction.atomic(),
            self.assertRaises(IntegrityError),
        ):
            VideoAsset.objects.filter(pk=asset.pk).update(poster_byte_size=0)
        with self.assertRaises(ValidationError):
            asset.delete()
        with self.assertRaises(ValidationError):
            VideoAsset.objects.all().delete()

    @override_settings(MEMBER_PENDING_ENTRY_LIMIT=1)
    def test_member_cannot_build_an_unbounded_pending_queue(self):
        SiteConfiguration.objects.filter(pk=1).update(moderation_mode=ModerationMode.PREMODERATION)
        self.publish()

        with self.assertRaisesMessage(ValidationError, "待审核内容已达到上限"):
            self.publish()

        self.assertEqual(Entry.objects.count(), 1)

    @override_settings(MEMBER_DAILY_MEDIA_BYTES=1)
    def test_daily_member_media_quota_rejects_before_files_are_persisted(self):
        with self.assertRaisesMessage(ValidationError, "账号限额"):
            self.publish()

        self.assertEqual(Entry.objects.count(), 0)
        self.assertEqual(
            [path for path in Path(self.media_directory.name).rglob("*") if path.is_file()],
            [],
        )

    @override_settings(MEDIA_MAX_TOTAL_BYTES=1)
    def test_site_media_hard_cap_rejects_new_uploads(self):
        with self.assertRaisesMessage(ValidationError, "站点媒体容量"):
            self.publish()

        self.assertEqual(Entry.objects.count(), 0)

    @override_settings(MEDIA_MIN_FREE_BYTES=100)
    @patch(
        "meppp.publishing.services.shutil.disk_usage",
        return_value=SimpleNamespace(free=100),
    )
    def test_reserved_free_space_is_enforced(self, disk_usage):
        with self.assertRaisesMessage(ValidationError, "存储空间不足"):
            self.publish()

        disk_usage.assert_called_once()
        self.assertEqual(Entry.objects.count(), 0)
