from django.conf import settings
from django.db import models

from meppp.common.models import PublicModel


class NotificationKind(models.TextChoices):
    FOLLOW = "follow", "新关注"
    LIKE = "like", "点赞"
    COMMENT = "comment", "评论"
    MODERATION = "moderation", "审核"
    SYSTEM = "system", "系统"


class Notification(PublicModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_notifications",
    )
    kind = models.CharField(max_length=20, choices=NotificationKind)
    target_type = models.CharField(max_length=80, blank=True)
    target_public_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id}:{self.kind}"
