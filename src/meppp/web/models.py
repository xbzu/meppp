from django.conf import settings
from django.db import models


class SubmissionClaim(models.Model):
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submission_claims",
    )
    purpose = models.CharField(max_length=200)
    token_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "提交凭证"
        verbose_name_plural = "提交凭证"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "purpose", "token_digest"],
                name="web_unique_submission_claim",
            )
        ]

    def __str__(self) -> str:
        return f"{self.member_id}:{self.purpose}"
