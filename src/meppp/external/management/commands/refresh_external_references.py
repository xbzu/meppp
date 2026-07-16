from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from meppp.external.models import ExternalReference
from meppp.external.oembed import OEmbedClient
from meppp.external.services import refresh_external_reference


class Command(BaseCommand):
    help = "Refresh a small due batch of attributed X/YouTube metadata"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options):
        del args
        limit = options["limit"]
        if limit < 1 or limit > 100:
            raise CommandError("--limit must be between 1 and 100")
        now = timezone.now()
        references = list(
            ExternalReference.objects.filter(
                Q(expires_at__isnull=True) | Q(expires_at__lte=now)
            ).order_by("expires_at", "created_at", "pk")[:limit]
        )
        client = OEmbedClient()
        counts: dict[str, int] = {}
        for reference in references:
            refreshed = refresh_external_reference(reference, client=client, now=now)
            counts[refreshed.metadata_status] = counts.get(refreshed.metadata_status, 0) + 1
        summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        self.stdout.write(
            self.style.SUCCESS(f"external references processed={len(references)} {summary}".strip())
        )
