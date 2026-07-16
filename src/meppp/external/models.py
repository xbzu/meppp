from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models

from meppp.common.models import PublicModel

from .parsing import (
    PROVIDER_X,
    PROVIDER_YOUTUBE,
    ExternalURLValidationError,
    parse_external_url,
)


class ExternalProvider(models.TextChoices):
    X = PROVIDER_X, "X"
    YOUTUBE = PROVIDER_YOUTUBE, "YouTube"


class MetadataStatus(models.TextChoices):
    PENDING = "pending", "等待获取"
    READY = "ready", "可用"
    UNAVAILABLE = "unavailable", "来源不可用"
    ERROR = "error", "获取失败"


class ExternalReference(PublicModel):
    entry = models.OneToOneField(
        "publishing.Entry",
        on_delete=models.CASCADE,
        related_name="external_reference",
    )
    provider = models.CharField(max_length=16, choices=ExternalProvider)
    external_id = models.CharField(max_length=64)
    canonical_url = models.URLField(max_length=2_048)
    author_name = models.CharField(max_length=200, blank=True)
    author_url = models.URLField(max_length=2_048, blank=True)
    title = models.CharField(max_length=500, blank=True)
    excerpt = models.TextField(blank=True, validators=[MaxLengthValidator(1_000)])
    metadata_status = models.CharField(
        max_length=16,
        choices=MetadataStatus,
        default=MetadataStatus.PENDING,
    )
    fetched_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "external"
        ordering = ["-created_at", "-pk"]
        verbose_name = "外部来源"
        verbose_name_plural = "外部来源"
        indexes = [
            models.Index(
                fields=["provider", "external_id"],
                name="external_ex_provid_2355f2_idx",
            ),
            models.Index(
                fields=["metadata_status", "expires_at"],
                name="external_ex_metadat_1a66a9_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        try:
            parsed = parse_external_url(self.canonical_url)
        except ExternalURLValidationError as error:
            raise ValidationError({"canonical_url": str(error)}) from error
        errors: dict[str, str] = {}
        if parsed.provider != self.provider:
            errors["provider"] = "来源平台与规范 URL 不一致"
        if parsed.external_id != self.external_id:
            errors["external_id"] = "来源 ID 与规范 URL 不一致"
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.get_provider_display()} · {self.external_id}"
