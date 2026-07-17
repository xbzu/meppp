from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from meppp.accounts.models import Profile
from meppp.publishing.models import Attachment, VideoAsset

GENERATED_MEDIA_SUFFIXES = frozenset({".webp", ".mp4", ".webm"})


class Command(BaseCommand):
    help = "Report old unreferenced generated media; delete only with --delete."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true")
        parser.add_argument("--minimum-age-hours", type=int, default=48)

    def handle(self, *args, **options):
        minimum_age_hours = options["minimum_age_hours"]
        if minimum_age_hours < 1:
            raise CommandError("--minimum-age-hours must be at least 1")

        media_root = Path(settings.MEDIA_ROOT).resolve()
        if not media_root.is_dir():
            raise CommandError("media root does not exist")
        referenced = set(Attachment.objects.values_list("file", flat=True))
        referenced.update(VideoAsset.objects.values_list("file", flat=True))
        referenced.update(VideoAsset.objects.values_list("poster", flat=True))
        referenced.update(Profile.objects.exclude(avatar="").values_list("avatar", flat=True))
        cutoff = time.time() - minimum_age_hours * 3600
        candidates: list[tuple[Path, str]] = []

        for generated_root in (media_root / "entries", media_root / "avatars"):
            if not generated_root.exists():
                continue
            for candidate in generated_root.rglob("*"):
                if (
                    candidate.suffix.lower() not in GENERATED_MEDIA_SUFFIXES
                    or candidate.is_symlink()
                    or not candidate.is_file()
                ):
                    continue
                relative_name = candidate.relative_to(media_root).as_posix()
                if relative_name not in referenced and candidate.stat().st_mtime <= cutoff:
                    candidates.append((candidate, relative_name))

        if options["delete"]:
            for candidate, _ in candidates:
                candidate.unlink()
            action = "deleted"
        else:
            action = "would_delete"

        for _, relative_name in candidates:
            self.stdout.write(f"{action}={relative_name}")
        self.stdout.write(
            self.style.SUCCESS(
                f"media_reconcile={action} candidates={len(candidates)} "
                f"minimum_age_hours={minimum_age_hours}"
            )
        )
