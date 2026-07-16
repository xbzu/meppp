from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from meppp.audit.services import record_event

from .models import ConfigurationRevision, SiteConfiguration

EDITABLE_FIELDS = frozenset(
    {
        "site_name",
        "tagline",
        "registration_mode",
        "post_max_length",
        "comment_max_length",
        "max_images_per_post",
        "upload_max_bytes",
        "moderation_mode",
        "comments_enabled",
        "video_uploads_enabled",
        "x_references_enabled",
        "youtube_references_enabled",
    }
)


@transaction.atomic
def update_configuration(*, actor, changes: dict[str, Any], reason: str = "") -> SiteConfiguration:
    unknown_fields = changes.keys() - EDITABLE_FIELDS
    if unknown_fields:
        raise ValidationError(f"Unsupported configuration fields: {sorted(unknown_fields)}")

    configuration, created = SiteConfiguration.objects.get_or_create(pk=1)
    if created:
        ConfigurationRevision.objects.create(
            version=configuration.version,
            snapshot=configuration.snapshot(),
            actor=None,
            reason="Initial default configuration",
        )
    else:
        configuration = SiteConfiguration.objects.select_for_update().get(pk=1)
    before = configuration.snapshot()
    for field_name, value in changes.items():
        setattr(configuration, field_name, value)
    after = configuration.snapshot()
    if before == after:
        return configuration

    configuration.version += 1
    configuration.save()

    ConfigurationRevision.objects.create(
        version=configuration.version,
        snapshot=after,
        actor=actor,
        reason=reason,
    )
    record_event(
        actor=actor,
        action="configuration.updated",
        target_type="site_configuration",
        reason=reason,
        metadata={"before": before, "after": after, "version": configuration.version},
    )
    return configuration
