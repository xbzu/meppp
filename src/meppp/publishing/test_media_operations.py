from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import SiteConfiguration
from meppp.web.services import publish_entry_once

from .image_processing import process_image_upload
from .models import VideoMimeType
from .services import publish_entry
from .video_processing import ProcessedVideo, VideoProbe


def make_processed_image():
    source = BytesIO()
    Image.new("RGB", (32, 24), "navy").save(source, format="PNG")
    return process_image_upload(
        upload=SimpleUploadedFile("source.png", source.getvalue()),
        max_bytes=5 * 1024 * 1024,
    )


def make_processed_video():
    poster = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(poster, format="WEBP")
    content = b"verified-video-content"
    return ProcessedVideo(
        content=content,
        poster_content=poster.getvalue(),
        source_byte_size=len(content),
        byte_size=len(content),
        duration_ms=1250,
        width=64,
        height=48,
        mime_type=VideoMimeType.MP4,
    )


class MediaOperationTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.media_directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.author = User.objects.create_user(username="author", password="password")
        SiteConfiguration.objects.create(pk=1)
        self.entry = publish_entry_once(
            author=self.author,
            body="媒体备份校验",
            topics=(),
            purpose="entry:create",
            token="media-ops",
            images=[make_processed_image()],
        )
        self.attachment = self.entry.attachments.get()

    def create_snapshot_database(self, *, video_asset=None) -> Path:
        database_path = Path(self.media_directory.name, "snapshot.sqlite3")
        with sqlite3.connect(database_path) as database:
            database.execute(
                "CREATE TABLE publishing_attachment "
                "(id integer primary key, file text, mime_type text, byte_size integer, "
                "width integer, height integer)"
            )
            database.execute(
                "INSERT INTO publishing_attachment "
                "(file, mime_type, byte_size, width, height) VALUES (?, ?, ?, ?, ?)",
                (
                    self.attachment.file.name,
                    self.attachment.mime_type,
                    self.attachment.byte_size,
                    self.attachment.width,
                    self.attachment.height,
                ),
            )
            if video_asset is not None:
                database.execute(
                    "CREATE TABLE publishing_entry (id integer primary key, public_id text)"
                )
                database.execute(
                    "CREATE TABLE publishing_videoasset "
                    "(id integer primary key, public_id text, entry_id integer, file text, "
                    "poster text, mime_type text, byte_size integer, duration_ms integer, "
                    "poster_byte_size integer, width integer, height integer)"
                )
                database.execute(
                    "INSERT INTO publishing_entry (id, public_id) VALUES (?, ?)",
                    (video_asset.entry_id, str(video_asset.entry.public_id)),
                )
                database.execute(
                    "INSERT INTO publishing_videoasset "
                    "(public_id, entry_id, file, poster, mime_type, byte_size, duration_ms, "
                    "poster_byte_size, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(video_asset.public_id),
                        video_asset.entry_id,
                        video_asset.file.name,
                        video_asset.poster.name,
                        video_asset.mime_type,
                        video_asset.byte_size,
                        video_asset.duration_ms,
                        video_asset.poster_byte_size,
                        video_asset.width,
                        video_asset.height,
                    ),
                )
        return database_path

    def test_verify_media_accepts_matching_snapshot_and_rejects_tampering(self):
        database_path = self.create_snapshot_database()
        output = StringIO()

        call_command(
            "verify_media",
            database=str(database_path),
            media_root=self.media_directory.name,
            stdout=output,
        )
        self.assertIn("media_verification=passed attachments=1", output.getvalue())

        with open(self.attachment.file.path, "ab") as media_file:
            media_file.write(b"tampered")
        with self.assertRaisesMessage(CommandError, "size or path"):
            call_command(
                "verify_media",
                database=str(database_path),
                media_root=self.media_directory.name,
            )

    def test_reconcile_media_is_dry_run_by_default_and_preserves_referenced_files(self):
        orphan = Path(self.media_directory.name, "entries/orphan/orphan.webp")
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(self.attachment.file.read())
        old_time = time.time() - 48 * 3600
        os.utime(orphan, (old_time, old_time))

        output = StringIO()
        call_command("reconcile_media", stdout=output)
        self.assertTrue(orphan.exists())
        self.assertTrue(Path(self.attachment.file.path).exists())
        self.assertIn("would_delete=entries/orphan/orphan.webp", output.getvalue())

        call_command("reconcile_media", delete=True, stdout=StringIO())
        self.assertFalse(orphan.exists())
        self.assertTrue(Path(self.attachment.file.path).exists())

    def test_reconcile_media_never_deletes_recent_orphan(self):
        recent = Path(self.media_directory.name, "entries/orphan/recent.webp")
        recent.parent.mkdir(parents=True)
        recent.write_bytes(self.attachment.file.read())

        call_command("reconcile_media", delete=True, stdout=StringIO())

        self.assertTrue(recent.exists())

    def test_verify_media_accepts_canonical_video_and_poster_and_rejects_drift(self):
        entry = publish_entry(
            author=self.author,
            body="视频备份校验",
            video=make_processed_video(),
        )
        asset = entry.video
        database_path = self.create_snapshot_database(video_asset=asset)
        matching_probe = VideoProbe(
            mime_type=asset.mime_type,
            duration_ms=asset.duration_ms,
            width=asset.width,
            height=asset.height,
            video_codec="h264",
            audio_codec="aac",
        )
        output = StringIO()

        with patch(
            "meppp.publishing.management.commands.verify_media.probe_video_path",
            return_value=matching_probe,
        ):
            call_command(
                "verify_media",
                database=str(database_path),
                media_root=self.media_directory.name,
                stdout=output,
            )
        self.assertIn("media_verification=passed attachments=1 videos=1", output.getvalue())

        with open(asset.file.path, "ab") as media_file:
            media_file.write(b"tampered")
        with self.assertRaisesMessage(CommandError, "video file size"):
            call_command(
                "verify_media",
                database=str(database_path),
                media_root=self.media_directory.name,
            )

    def test_reconcile_media_handles_video_and_poster_but_ignores_unknown_files(self):
        entry = publish_entry(
            author=self.author,
            body="视频孤儿清理",
            video=make_processed_video(),
        )
        asset = entry.video
        orphan_root = Path(self.media_directory.name, "entries/orphan-video")
        orphan_root.mkdir(parents=True)
        orphans = [
            orphan_root / "orphan.mp4",
            orphan_root / "orphan.webm",
            orphan_root / "orphan-poster.webp",
        ]
        for orphan in orphans:
            orphan.write_bytes(b"old-generated-media")
            old_time = time.time() - 48 * 3600
            os.utime(orphan, (old_time, old_time))
        unknown = orphan_root / "operator-note.txt"
        unknown.write_text("keep", encoding="utf-8")
        old_time = time.time() - 48 * 3600
        os.utime(unknown, (old_time, old_time))

        call_command("reconcile_media", delete=True, stdout=StringIO())

        self.assertTrue(Path(asset.file.path).exists())
        self.assertTrue(Path(asset.poster.path).exists())
        self.assertTrue(all(not orphan.exists() for orphan in orphans))
        self.assertTrue(unknown.exists())
