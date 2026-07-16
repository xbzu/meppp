from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ExternalReference, MetadataStatus
from .oembed import ExternalMetadataError, ExternalSourceUnavailable, OEmbedClient
from .parsing import ParsedExternalURL, parse_external_url

ERROR_RETRY_AFTER = timedelta(minutes=15)
UNAVAILABLE_RETRY_AFTER = timedelta(hours=6)


def _parsed_from_reference(reference: ExternalReference) -> ParsedExternalURL:
    parsed = parse_external_url(reference.canonical_url)
    if parsed.provider != reference.provider or parsed.external_id != reference.external_id:
        raise ValidationError("外部来源记录与规范 URL 不一致")
    return ParsedExternalURL(
        provider=reference.provider,
        external_id=reference.external_id,
        canonical_url=parsed.canonical_url,
    )


def _save_reference(reference: ExternalReference, fields: list[str]) -> ExternalReference:
    reference.full_clean()
    reference.save(update_fields=[*fields, "updated_at"])
    return reference


def refresh_external_reference(
    reference: ExternalReference,
    *,
    client: OEmbedClient | None = None,
    now=None,
) -> ExternalReference:
    if reference.pk is None or reference._state.adding:
        raise ValidationError("外部来源必须先保存再刷新")
    now = now or timezone.now()
    client = client or OEmbedClient()
    parsed = _parsed_from_reference(reference)
    try:
        metadata = client.fetch(parsed)
    except ExternalSourceUnavailable:
        reference.author_name = ""
        reference.author_url = ""
        reference.title = ""
        reference.excerpt = ""
        reference.metadata_status = MetadataStatus.UNAVAILABLE
        reference.fetched_at = now
        reference.expires_at = now + UNAVAILABLE_RETRY_AFTER
        return _save_reference(
            reference,
            [
                "author_name",
                "author_url",
                "title",
                "excerpt",
                "metadata_status",
                "fetched_at",
                "expires_at",
            ],
        )
    except ExternalMetadataError:
        reference.metadata_status = MetadataStatus.ERROR
        reference.expires_at = now + ERROR_RETRY_AFTER
        return _save_reference(reference, ["metadata_status", "expires_at"])

    reference.canonical_url = metadata.canonical_url
    reference.author_name = metadata.author_name
    reference.author_url = metadata.author_url
    reference.title = metadata.title
    reference.excerpt = metadata.excerpt
    reference.metadata_status = MetadataStatus.READY
    reference.fetched_at = now
    reference.expires_at = now + metadata.expires_after
    return _save_reference(
        reference,
        [
            "canonical_url",
            "author_name",
            "author_url",
            "title",
            "excerpt",
            "metadata_status",
            "fetched_at",
            "expires_at",
        ],
    )


def create_external_reference(
    *,
    entry,
    source_url: str,
    client: OEmbedClient | None = None,
    refresh: bool = True,
) -> ExternalReference:
    if entry is None or entry.pk is None or entry._state.adding:
        raise ValidationError("外部来源必须关联已保存的内容")
    parsed = parse_external_url(source_url)
    with transaction.atomic():
        reference = ExternalReference.objects.select_for_update().filter(entry=entry).first()
        if reference is None:
            reference = ExternalReference(entry=entry)
        source_changed = (
            reference.provider != parsed.provider or reference.external_id != parsed.external_id
        )
        if source_changed:
            reference.provider = parsed.provider
            reference.external_id = parsed.external_id
            reference.canonical_url = parsed.canonical_url
            reference.author_name = ""
            reference.author_url = ""
            reference.title = ""
            reference.excerpt = ""
            reference.fetched_at = None
            reference.expires_at = None
            reference.metadata_status = MetadataStatus.PENDING
            reference.full_clean()
            reference.save()
    if refresh:
        return refresh_external_reference(reference, client=client)
    return reference
