from __future__ import annotations

import io
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from meppp.common import sqlite_backup
from meppp.common.sqlite_backup import (
    SQLiteBackupError,
    backup_database,
    manifest_path_for,
    prune_backups,
    restore_to_new_path,
    sha256_file,
    verify_manifest,
)


class SQLiteBackupTests(SimpleTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "live.sqlite3"
        self.backup_dir = self.root / "backups"
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            connection.execute("INSERT INTO notes (body) VALUES ('committed in WAL')")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_online_backup_has_checksum_and_restores_to_a_fresh_path(self):
        artifact = backup_database(self.database, self.backup_dir)

        self.assertEqual(verify_manifest(artifact.path), artifact.sha256)
        self.assertEqual(
            stat.S_IMODE(artifact.path.stat().st_mode),
            0o600,
        )
        with sqlite3.connect(artifact.path) as connection:
            self.assertEqual(
                connection.execute("SELECT body FROM notes").fetchone(),
                ("committed in WAL",),
            )

        destination = self.root / "drill" / "restored.sqlite3"
        result = restore_to_new_path(artifact.path, destination)
        self.assertEqual(result.destination, destination.resolve())
        with sqlite3.connect(destination) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))

    def test_restore_refuses_to_overwrite_an_existing_database(self):
        artifact = backup_database(self.database, self.backup_dir)
        existing = self.root / "must-remain.sqlite3"
        existing.write_bytes(b"must remain unchanged")

        with self.assertRaisesRegex(SQLiteBackupError, "already exists"):
            restore_to_new_path(artifact.path, existing)

        self.assertEqual(existing.read_bytes(), b"must remain unchanged")

    def test_restore_rejects_a_tampered_backup_before_creating_destination(self):
        artifact = backup_database(self.database, self.backup_dir)
        with artifact.path.open("ab") as handle:
            handle.write(b"tampered")
        destination = self.root / "restore.sqlite3"

        with self.assertRaisesRegex(SQLiteBackupError, "checksum mismatch"):
            restore_to_new_path(artifact.path, destination)

        self.assertFalse(destination.exists())

    def test_manifest_atomic_replace_failure_does_not_mask_the_original_error(self):
        backup_path = self.backup_dir / "meppp-20260101T000000.000000Z.sqlite3"
        backup_path.parent.mkdir(parents=True)
        backup_path.write_bytes(b"backup")

        with (
            patch.object(sqlite_backup.os, "replace", side_effect=OSError("replace failed")),
            self.assertRaisesRegex(OSError, "replace failed"),
        ):
            sqlite_backup._write_manifest(backup_path, "0" * 64)

        self.assertFalse(manifest_path_for(backup_path).exists())
        self.assertEqual(
            list(self.backup_dir.glob(f".{backup_path.name}.sha256.*.tmp")),
            [],
        )

    def test_retention_preserves_seven_daily_and_four_weekly_buckets(self):
        start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        for offset in range(36):
            backup_database(
                self.database,
                self.backup_dir,
                timestamp=start + timedelta(days=offset),
            )

        retained = sorted(self.backup_dir.glob("meppp-*.sqlite3"))
        retained_timestamps = [
            datetime.strptime(path.name[6:-8], "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
            for path in retained
        ]
        retained_days = {timestamp.date() for timestamp in retained_timestamps}
        retained_weeks = {timestamp.isocalendar()[:2] for timestamp in retained_timestamps}
        expected_days = {(start + timedelta(days=offset)).date() for offset in range(29, 36)}
        all_weeks = [
            (start + timedelta(days=offset)).isocalendar()[:2] for offset in range(35, -1, -1)
        ]
        expected_weeks = []
        for bucket in all_weeks:
            if bucket not in expected_weeks:
                expected_weeks.append(bucket)
            if len(expected_weeks) == 4:
                break

        self.assertTrue(expected_days.issubset(retained_days))
        self.assertTrue(set(expected_weeks).issubset(retained_weeks))
        self.assertTrue(all(manifest_path_for(path).is_file() for path in retained))

    def test_corrupt_newer_backup_does_not_displace_verified_retention_buckets(self):
        start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        verified_paths = []
        for offset in range(7):
            artifact = backup_database(
                self.database,
                self.backup_dir,
                timestamp=start + timedelta(days=offset),
            )
            verified_paths.append(artifact.path)
        corrupt_path = self.backup_dir / "meppp-20260208T000000.000000Z.sqlite3"
        corrupt_path.write_bytes(verified_paths[-1].read_bytes())
        manifest_path_for(corrupt_path).write_text(
            f"{sha256_file(corrupt_path)}  {corrupt_path.name}\n",
            encoding="ascii",
        )
        with corrupt_path.open("ab") as handle:
            handle.write(b"damaged after manifest creation")

        deleted = prune_backups(self.backup_dir)

        self.assertEqual(deleted, ())
        self.assertTrue(corrupt_path.exists())
        self.assertTrue(all(path.exists() for path in verified_paths))

    def test_management_command_defaults_to_non_destructive_restore_drill(self):
        artifact = backup_database(self.database, self.backup_dir)
        original_digest = self.database.read_bytes()
        output = io.StringIO()

        call_command(
            "restore_sqlite",
            artifact.path,
            "--drill-root",
            self.root / "restore-drills",
            stdout=output,
        )

        self.assertEqual(self.database.read_bytes(), original_digest)
        self.assertIn("integrity_check=ok", output.getvalue())
        self.assertIn("live_database_untouched=yes", output.getvalue())

    def test_backup_management_command_creates_a_verified_artifact(self):
        command_backup_dir = self.root / "command-backups"
        output = io.StringIO()

        call_command(
            "backup_sqlite",
            "--database",
            self.database,
            "--backup-dir",
            command_backup_dir,
            stdout=output,
        )

        backups = list(command_backup_dir.glob("meppp-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        verify_manifest(backups[0])
        self.assertIn("SQLite backup complete", output.getvalue())

    def test_restore_drill_can_keep_a_verified_staging_database(self):
        artifact = backup_database(self.database, self.backup_dir)
        drill_root = self.root / "kept-restore-drills"
        output = io.StringIO()

        call_command(
            "restore_sqlite",
            artifact.path,
            "--drill-root",
            drill_root,
            "--keep-drill",
            stdout=output,
        )

        kept_databases = list(drill_root.glob("*/restored.sqlite3"))
        self.assertEqual(len(kept_databases), 1)
        with sqlite3.connect(kept_databases[0]) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertIn(str(kept_databases[0].resolve()), output.getvalue())
