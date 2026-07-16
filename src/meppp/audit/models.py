from django.conf import settings
from django.db import models

from meppp.common.models import AppendOnlyPublicModel


class AuditEvent(AppendOnlyPublicModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=80)
    target_public_id = models.UUIDField(null=True, blank=True)
    request_id = models.UUIDField(null=True, blank=True, editable=False)
    reason = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-created_at"]
        verbose_name = "审计记录"
        verbose_name_plural = "审计记录"
        indexes = [
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["target_type", "target_public_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} · {self.target_type}"
