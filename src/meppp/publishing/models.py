import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxLengthValidator, MinLengthValidator
from django.db import models

from meppp.common.models import PublicModel


class ContentState(models.TextChoices):
    PUBLISHED = "published", "已发布"
    HIDDEN = "hidden", "已隐藏"
    DELETED = "deleted", "已删除"


class EntryQuerySet(models.QuerySet):
    def public(self):
        return self.filter(state=ContentState.PUBLISHED)

    def delete(self):
        raise ValidationError("Entries must use lifecycle states, not physical deletion")

    def _raw_delete(self, using):
        raise ValidationError("Entries must use lifecycle states, not physical deletion")


class Entry(PublicModel):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    body = models.TextField(validators=[MinLengthValidator(1), MaxLengthValidator(5_000)])
    state = models.CharField(
        max_length=12,
        choices=ContentState,
        default=ContentState.PUBLISHED,
    )
    edited_at = models.DateTimeField(null=True, blank=True)

    objects = EntryQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["author", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.body[:60]

    def delete(self, *args, **kwargs):
        raise ValidationError("Entries must use lifecycle states, not physical deletion")


class CommentQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Comments must use lifecycle states, not physical deletion")

    def _raw_delete(self, using):
        raise ValidationError("Comments must use lifecycle states, not physical deletion")


class Comment(PublicModel):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="comments",
    )
    body = models.TextField(validators=[MinLengthValidator(1), MaxLengthValidator(2_000)])
    state = models.CharField(
        max_length=12,
        choices=ContentState,
        default=ContentState.PUBLISHED,
    )

    objects = CommentQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["entry", "state", "created_at"])]

    def __str__(self) -> str:
        return self.body[:60]

    def delete(self, *args, **kwargs):
        raise ValidationError("Comments must use lifecycle states, not physical deletion")


class Topic(PublicModel):
    slug = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=80)
    entries = models.ManyToManyField(Entry, through="EntryTopic", related_name="topics")

    class Meta:
        ordering = ["slug"]

    def save(self, *args, **kwargs):
        self.slug = self.slug.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.label


class EntryTopic(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entry", "topic"], name="publishing_unique_entry_topic")
        ]


def attachment_upload_path(instance, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"entries/{instance.entry.public_id}/{uuid.uuid4().hex}{extension}"


class Attachment(PublicModel):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        upload_to=attachment_upload_path,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    mime_type = models.CharField(max_length=80)
    byte_size = models.PositiveIntegerField()
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "position"],
                name="publishing_unique_attachment_position",
            ),
            models.CheckConstraint(
                condition=models.Q(position__lte=3),
                name="publishing_attachment_position_lte_3",
            ),
            models.CheckConstraint(
                condition=models.Q(byte_size__gt=0),
                name="publishing_attachment_size_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}:{self.position}"
