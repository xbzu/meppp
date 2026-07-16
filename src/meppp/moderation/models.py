from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from meppp.common.models import AppendOnlyPublicModel, PublicModel


class SubjectType(models.TextChoices):
    USER = "user", "用户"
    ENTRY = "entry", "内容"
    COMMENT = "comment", "评论"


class ReportReason(models.TextChoices):
    SPAM = "spam", "垃圾信息"
    HARASSMENT = "harassment", "骚扰"
    ILLEGAL = "illegal", "违法风险"
    OTHER = "other", "其他"


class ReportStatus(models.TextChoices):
    OPEN = "open", "待处理"
    TRIAGED = "triaged", "处理中"
    RESOLVED = "resolved", "已处理"
    REJECTED = "rejected", "已驳回"


class ModerationAction(models.TextChoices):
    NO_ACTION = "none", "不处置"
    HIDE_ENTRY = "entry.hide", "隐藏内容"
    RESTORE_ENTRY = "entry.restore", "恢复内容"
    HIDE_COMMENT = "comment.hide", "隐藏评论"
    RESTORE_COMMENT = "comment.restore", "恢复评论"
    SUSPEND_USER = "user.suspend", "停用用户"
    RESTORE_USER = "user.restore", "恢复用户"


REPORT_EVIDENCE_FIELDS = (
    "reporter_id",
    "subject_type",
    "subject_public_id",
    "reason",
    "details",
)
REPORT_EVIDENCE_UPDATE_KEYS = frozenset({*REPORT_EVIDENCE_FIELDS, "reporter"})


class ReportQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if REPORT_EVIDENCE_UPDATE_KEYS.intersection(kwargs):
            raise ValidationError("Report evidence cannot be changed")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if REPORT_EVIDENCE_UPDATE_KEYS.intersection(fields):
            raise ValidationError("Report evidence cannot be changed")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_conflicts and REPORT_EVIDENCE_UPDATE_KEYS.intersection(update_fields or ()):
            raise ValidationError("Report evidence cannot be changed")
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def delete(self):
        raise ValidationError("Reports cannot be deleted")

    def _raw_delete(self, using):
        raise ValidationError("Reports cannot be deleted")


class Report(PublicModel):
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_reports",
    )
    subject_type = models.CharField(max_length=20, choices=SubjectType)
    subject_public_id = models.UUIDField()
    reason = models.CharField(max_length=20, choices=ReportReason)
    details = models.CharField(max_length=1_000, blank=True)
    status = models.CharField(max_length=20, choices=ReportStatus, default=ReportStatus.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_reports",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_reports",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    objects = ReportQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["created_at"]
        verbose_name = "举报"
        verbose_name_plural = "举报"
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "subject_type", "subject_public_id"],
                condition=models.Q(status__in=[ReportStatus.OPEN, ReportStatus.TRIAGED]),
                name="moderation_unique_active_report",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=[ReportStatus.OPEN, ReportStatus.TRIAGED],
                        resolved_by__isnull=True,
                        resolved_at__isnull=True,
                    )
                    | models.Q(
                        status__in=[ReportStatus.RESOLVED, ReportStatus.REJECTED],
                        resolved_by__isnull=False,
                        resolved_at__isnull=False,
                    )
                ),
                name="moderation_resolution_fields_match_status",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["subject_type", "subject_public_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.subject_type}:{self.subject_public_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(*REPORT_EVIDENCE_FIELDS).first()
            if previous is not None:
                current = {field: getattr(self, field) for field in REPORT_EVIDENCE_FIELDS}
                if any(previous[field] != current[field] for field in REPORT_EVIDENCE_FIELDS):
                    raise ValidationError("Report evidence cannot be changed")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Reports cannot be deleted")


class ModerationDecision(AppendOnlyPublicModel):
    report = models.ForeignKey(Report, on_delete=models.PROTECT, related_name="decisions")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderation_decisions",
    )
    action = models.CharField(max_length=80, choices=ModerationAction)
    reason = models.CharField(max_length=1_000)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["created_at"]
        verbose_name = "审核决定"
        verbose_name_plural = "审核决定"

    def __str__(self) -> str:
        return f"{self.report_id}:{self.action}"
