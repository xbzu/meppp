from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image


class Command(BaseCommand):
    help = "Verify that every attachment in a SQLite database has a matching safe WebP file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default=str(settings.DATABASES["default"]["NAME"]),
        )
        parser.add_argument("--media-root", default=str(settings.MEDIA_ROOT))

    def handle(self, *args, **options):
        database_path = Path(options["database"]).resolve()
        media_root = Path(options["media_root"]).resolve()
        if not database_path.is_file():
            raise CommandError("database does not exist")
        if not media_root.is_dir():
            raise CommandError("media root does not exist")

        uri = f"file:{database_path.as_posix()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as database:
                rows = database.execute(
                    "SELECT file, mime_type, byte_size, width, height "
                    "FROM publishing_attachment ORDER BY id"
                ).fetchall()
        except sqlite3.Error as error:
            raise CommandError("attachment records could not be read") from error

        for file_name, mime_type, byte_size, width, height in rows:
            relative_path = PurePosixPath(file_name)
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or relative_path.parts[:1] != ("entries",)
                or relative_path.suffix != ".webp"
                or mime_type != "image/webp"
            ):
                raise CommandError("attachment metadata is not canonical")
            candidate = media_root.joinpath(*relative_path.parts)
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(media_root)
            except (FileNotFoundError, OSError, ValueError) as error:
                raise CommandError("attachment file is missing or outside media root") from error
            if candidate.is_symlink() or resolved.stat().st_size != byte_size:
                raise CommandError("attachment file size or path is invalid")
            try:
                with Image.open(resolved, formats=("WEBP",)) as image:
                    image.load()
                    valid = (
                        image.format == "WEBP"
                        and image.size == (width, height)
                        and getattr(image, "n_frames", 1) == 1
                        and not image.getexif()
                    )
            except (OSError, SyntaxError, ValueError) as error:
                raise CommandError("attachment file cannot be decoded") from error
            if not valid:
                raise CommandError("attachment file does not match database metadata")

        self.stdout.write(self.style.SUCCESS(f"media_verification=passed attachments={len(rows)}"))
