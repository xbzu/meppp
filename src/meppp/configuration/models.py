from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from meppp.common.models import AppendOnlyPublicModel, TimeStampedModel


class RegistrationMode(models.TextChoices):
    OPEN = "open", "开放注册"
    INVITE = "invite", "仅限邀请"
    CLOSED = "closed", "关闭注册"


class ModerationMode(models.TextChoices):
    POSTMODERATION = "post", "发布后审核"
    PREMODERATION = "pre", "发布前审核"


class SiteConfiguration(TimeStampedModel):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    site_name = models.CharField(max_length=80, default="MEPPP")
    tagline = models.CharField(max_length=160, blank=True)
    registration_mode = models.CharField(
        max_length=10,
        choices=RegistrationMode,
        default=RegistrationMode.CLOSED,
    )
    post_max_length = models.PositiveIntegerField(
        default=2_000,
        validators=[MinValueValidator(100), MaxValueValidator(5_000)],
    )
    comment_max_length = models.PositiveIntegerField(
        default=500,
        validators=[MinValueValidator(20), MaxValueValidator(2_000)],
    )
    max_images_per_post = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(0), MaxValueValidator(4)],
    )
    upload_max_bytes = models.PositiveIntegerField(
        default=5 * 1024 * 1024,
        validators=[MinValueValidator(128 * 1024), MaxValueValidator(20 * 1024 * 1024)],
    )
    moderation_mode = models.CharField(
        max_length=10,
        choices=ModerationMode,
        default=ModerationMode.POSTMODERATION,
    )
    comments_enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "站点配置"
        verbose_name_plural = "站点配置"

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        super().save(*args, **kwargs)

    def snapshot(self) -> dict:
        return {
            "site_name": self.site_name,
            "tagline": self.tagline,
            "registration_mode": self.registration_mode,
            "post_max_length": self.post_max_length,
            "comment_max_length": self.comment_max_length,
            "max_images_per_post": self.max_images_per_post,
            "upload_max_bytes": self.upload_max_bytes,
            "moderation_mode": self.moderation_mode,
            "comments_enabled": self.comments_enabled,
        }

    def __str__(self) -> str:
        return self.site_name


class ConfigurationRevision(AppendOnlyPublicModel):
    version = models.PositiveIntegerField(unique=True)
    snapshot = models.JSONField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="configuration_revisions",
    )
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-version"]
        verbose_name = "配置历史"
        verbose_name_plural = "配置历史"

    def __str__(self) -> str:
        return f"Configuration v{self.version}"
