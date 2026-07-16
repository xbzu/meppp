from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from meppp.common.sqlite_backup import (
    MIN_DAILY_BACKUPS,
    MIN_WEEKLY_BACKUPS,
    SQLiteBackupError,
    backup_database,
)


def _environment_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as error:
        raise CommandError(f"{name} must be an integer") from error


class Command(BaseCommand):
    help = "Create a verified online SQLite backup and apply daily/weekly retention."

    def add_arguments(self, parser: CommandParser) -> None:
        database_path = Path(settings.DATABASES["default"]["NAME"])
        parser.add_argument("--database", type=Path, default=database_path)
        parser.add_argument(
            "--backup-dir",
            type=Path,
            default=Path(os.getenv("MEPPP_BACKUP_DIR", settings.DATA_DIR / "backups/sqlite")),
        )
        parser.add_argument(
            "--daily",
            type=int,
            default=_environment_int("MEPPP_BACKUP_DAILY", MIN_DAILY_BACKUPS),
        )
        parser.add_argument(
            "--weekly",
            type=int,
            default=_environment_int("MEPPP_BACKUP_WEEKLY", MIN_WEEKLY_BACKUPS),
        )

    def handle(self, *args, **options) -> None:
        try:
            artifact = backup_database(
                options["database"],
                options["backup_dir"],
                daily=options["daily"],
                weekly=options["weekly"],
            )
        except SQLiteBackupError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                "SQLite backup complete: "
                f"path={artifact.path} sha256={artifact.sha256} size={artifact.size} "
                f"pruned={len(artifact.deleted_paths) // 2}"
            )
        )
