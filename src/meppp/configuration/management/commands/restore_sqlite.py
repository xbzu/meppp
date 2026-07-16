from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from meppp.common.sqlite_backup import SQLiteBackupError, restore_to_new_path


class Command(BaseCommand):
    help = (
        "Verify a backup by restoring it to a fresh temporary database. "
        "This command never overwrites the live database."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("backup", type=Path)
        parser.add_argument("--manifest", type=Path)
        parser.add_argument(
            "--drill-root",
            type=Path,
            default=Path(settings.DATA_DIR) / "restore-drills",
        )
        parser.add_argument("--keep-drill", action="store_true")

    def handle(self, *args, **options) -> None:
        drill_root = options["drill_root"]
        drill_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        drill_dir = Path(tempfile.mkdtemp(prefix="meppp-restore-drill-", dir=str(drill_root)))
        destination = drill_dir / "restored.sqlite3"
        try:
            result = restore_to_new_path(
                options["backup"], destination, manifest_path=options["manifest"]
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "SQLite restore drill passed: "
                    f"temporary_path={result.destination} sha256={result.sha256} "
                    "integrity_check=ok live_database_untouched=yes"
                )
            )
        except SQLiteBackupError as error:
            raise CommandError(str(error)) from error
        finally:
            if not options["keep_drill"]:
                shutil.rmtree(drill_dir, ignore_errors=True)
