from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from meppp.publishing.models import VideoMimeType
from meppp.publishing.video_processing import probe_video_path, verify_poster_path


def _table_exists(database: sqlite3.Connection, table_name: str) -> bool:
    return (
        database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _column_names(database: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in database.execute(f'PRAGMA table_info("{table_name}")').fetchall()}


def _canonical_uuid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise CommandError("media metadata is not canonical") from error


def _resolve_media_file(
    media_root: Path,
    relative_path: PurePosixPath,
    *,
    missing_message: str,
) -> Path:
    candidate = media_root.joinpath(*relative_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(media_root)
        current = media_root
        for part in relative_path.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("symlinked media path")
        if not resolved.is_file():
            raise ValueError("media path is not a regular file")
    except (FileNotFoundError, OSError, ValueError) as error:
        raise CommandError(missing_message) from error
    return resolved


class Command(BaseCommand):
    help = "Verify generated images, videos, and posters against a SQLite database snapshot."

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
                attachment_rows = database.execute(
                    "SELECT file, mime_type, byte_size, width, height "
                    "FROM publishing_attachment ORDER BY id"
                ).fetchall()
                video_rows = []
                if _table_exists(database, "publishing_videoasset"):
                    video_rows = database.execute(
                        "SELECT video.public_id, entry.public_id, video.file, video.poster, "
                        "video.mime_type, video.byte_size, video.poster_byte_size, "
                        "video.duration_ms, video.width, video.height "
                        "FROM publishing_videoasset AS video "
                        "JOIN publishing_entry AS entry ON entry.id = video.entry_id "
                        "ORDER BY video.id"
                    ).fetchall()
                avatar_rows = []
                if _table_exists(database, "accounts_profile"):
                    profile_columns = _column_names(database, "accounts_profile")
                    avatar_columns = {
                        "public_id",
                        "avatar",
                        "avatar_version",
                        "avatar_byte_size",
                        "avatar_width",
                        "avatar_height",
                    }
                    if avatar_columns.issubset(profile_columns):
                        avatar_rows = database.execute(
                            "SELECT public_id, avatar, avatar_version, avatar_byte_size, "
                            "avatar_width, avatar_height FROM accounts_profile "
                            "WHERE avatar != '' ORDER BY id"
                        ).fetchall()
                    elif "avatar" in profile_columns:
                        legacy_avatar = database.execute(
                            "SELECT 1 FROM accounts_profile WHERE avatar != '' LIMIT 1"
                        ).fetchone()
                        if legacy_avatar is not None:
                            raise CommandError("legacy avatar exists without canonical metadata")
        except sqlite3.Error as error:
            raise CommandError("media records could not be read") from error

        for file_name, mime_type, byte_size, width, height in attachment_rows:
            if not isinstance(file_name, str):
                raise CommandError("attachment metadata is not canonical")
            relative_path = PurePosixPath(file_name)
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or relative_path.parts[:1] != ("entries",)
                or relative_path.suffix != ".webp"
                or mime_type != "image/webp"
            ):
                raise CommandError("attachment metadata is not canonical")
            resolved = _resolve_media_file(
                media_root,
                relative_path,
                missing_message="attachment file is missing or outside media root",
            )
            if resolved.stat().st_size != byte_size:
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

        for (
            raw_profile_public_id,
            file_name,
            raw_avatar_version,
            byte_size,
            width,
            height,
        ) in avatar_rows:
            profile_public_id = _canonical_uuid(raw_profile_public_id)
            avatar_version = _canonical_uuid(raw_avatar_version)
            if not isinstance(file_name, str):
                raise CommandError("avatar metadata is not canonical")
            relative_path = PurePosixPath(file_name)
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or relative_path.parts[:-1] != ("avatars", profile_public_id)
                or relative_path.name != f"{avatar_version}.webp"
                or not isinstance(byte_size, int)
                or byte_size <= 0
                or not isinstance(width, int)
                or width <= 0
                or not isinstance(height, int)
                or height <= 0
            ):
                raise CommandError("avatar metadata is not canonical")
            resolved = _resolve_media_file(
                media_root,
                relative_path,
                missing_message="avatar file is missing or outside media root",
            )
            if resolved.stat().st_size != byte_size:
                raise CommandError("avatar file size or path is invalid")
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
                raise CommandError("avatar file cannot be decoded") from error
            if not valid:
                raise CommandError("avatar file does not match database metadata")

        for (
            raw_video_public_id,
            raw_entry_public_id,
            file_name,
            poster_name,
            mime_type,
            byte_size,
            poster_byte_size,
            duration_ms,
            width,
            height,
        ) in video_rows:
            video_public_id = _canonical_uuid(raw_video_public_id)
            entry_public_id = _canonical_uuid(raw_entry_public_id)
            if not isinstance(file_name, str) or not isinstance(poster_name, str):
                raise CommandError("video metadata is not canonical")
            extension = (
                ".mp4"
                if mime_type == VideoMimeType.MP4
                else ".webm"
                if mime_type == VideoMimeType.WEBM
                else None
            )
            relative_video = PurePosixPath(file_name)
            relative_poster = PurePosixPath(poster_name)
            expected_parent = ("entries", entry_public_id)
            if (
                extension is None
                or relative_video.is_absolute()
                or relative_poster.is_absolute()
                or ".." in relative_video.parts
                or ".." in relative_poster.parts
                or relative_video.parts[:-1] != expected_parent
                or relative_poster.parts[:-1] != expected_parent
                or relative_video.name != f"{video_public_id}{extension}"
                or relative_poster.name != f"{video_public_id}-poster.webp"
            ):
                raise CommandError("video metadata is not canonical")

            video_path = _resolve_media_file(
                media_root,
                relative_video,
                missing_message="video file is missing or outside media root",
            )
            poster_path = _resolve_media_file(
                media_root,
                relative_poster,
                missing_message="video poster is missing or outside media root",
            )
            if video_path.stat().st_size != byte_size:
                raise CommandError("video file size or path is invalid")
            if poster_path.stat().st_size != poster_byte_size:
                raise CommandError("video poster size or path is invalid")
            try:
                probe = probe_video_path(video_path, expected_mime_type=mime_type)
                verify_poster_path(poster_path)
            except ValidationError as error:
                raise CommandError("video or poster cannot be safely decoded") from error
            if (
                probe.mime_type != mime_type
                or probe.duration_ms != duration_ms
                or probe.width != width
                or probe.height != height
            ):
                raise CommandError("video file does not match database metadata")

        self.stdout.write(
            self.style.SUCCESS(
                f"media_verification=passed attachments={len(attachment_rows)} "
                f"avatars={len(avatar_rows)} videos={len(video_rows)}"
            )
        )
