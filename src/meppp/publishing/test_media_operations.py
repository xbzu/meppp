from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from io import BytesIO, StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image

from meppp.accounts.models import User
from meppp.configuration.models import SiteConfiguration
from meppp.web.services import publish_entry_once

from .image_processing import process_image_upload


def make_processed_image():
    source = BytesIO()
    Image.new("RGB", (32, 24), "navy").save(source, format="PNG")
    return process_image_upload(
        upload=SimpleUploadedFile("source.png", source.getvalue()),
        max_bytes=5 * 1024 * 1024,
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

    def create_snapshot_database(self) -> Path:
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
