from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxLengthValidator, MinLengthValidator
from django.db import models

from meppp.common.models import AppendOnlyPublicModel, PublicModel


class ContentState(models.TextChoices):
    PENDING = "pending", "待审核"
    PUBLISHED = "published", "已发布"
    HIDDEN = "hidden", "已隐藏"
    DELETED = "deleted", "已删除"


class ContentReviewOutcome(models.TextChoices):
    APPROVE = "approve", "批准公开"
    REJECT = "reject", "驳回并隐藏"


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
        ordering = ["-created_at", "-pk"]
        verbose_name = "内容"
        verbose_name_plural = "内容"
        indexes = [
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["author", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.body[:60]

    def delete(self, *args, **kwargs):
        raise ValidationError("Entries must use lifecycle states, not physical deletion")


class PendingEntry(Entry):
    class Meta:
        proxy = True
        verbose_name = "待审内容"
        verbose_name_plural = "待审内容"


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
        ordering = ["created_at", "pk"]
        verbose_name = "评论"
        verbose_name_plural = "评论"
        indexes = [models.Index(fields=["entry", "state", "created_at"])]

    def __str__(self) -> str:
        return self.body[:60]

    def delete(self, *args, **kwargs):
        raise ValidationError("Comments must use lifecycle states, not physical deletion")


class PendingComment(Comment):
    class Meta:
        proxy = True
        verbose_name = "待审评论"
        verbose_name_plural = "待审评论"


class ContentReviewDecision(AppendOnlyPublicModel):
    entry = models.ForeignKey(
        Entry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_decisions",
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_decisions",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="content_review_decisions",
    )
    outcome = models.CharField(max_length=16, choices=ContentReviewOutcome)
    reason = models.CharField(max_length=500)
    before_state = models.CharField(max_length=12, choices=ContentState)
    after_state = models.CharField(max_length=12, choices=ContentState)

    class Meta:
        base_manager_name = "objects"
        ordering = ["-created_at"]
        verbose_name = "内容审核决定"
        verbose_name_plural = "内容审核决定"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(entry__isnull=False, comment__isnull=True)
                    | models.Q(entry__isnull=True, comment__isnull=False)
                ),
                name="publishing_review_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=models.Q(before_state=ContentState.PENDING),
                name="publishing_review_starts_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(after_state__in=[ContentState.PUBLISHED, ContentState.HIDDEN]),
                name="publishing_review_valid_final_state",
            ),
            models.UniqueConstraint(
                fields=["entry"],
                condition=models.Q(entry__isnull=False),
                name="publishing_one_review_per_entry",
            ),
            models.UniqueConstraint(
                fields=["comment"],
                condition=models.Q(comment__isnull=False),
                name="publishing_one_review_per_comment",
            ),
        ]
        indexes = [
            models.Index(fields=["outcome", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    @property
    def target(self):
        return self.entry or self.comment

    @property
    def target_type(self) -> str:
        return "entry" if self.entry_id is not None else "comment"

    def __str__(self) -> str:
        target = self.target
        return f"{self.get_outcome_display()} · {target.public_id if target else 'missing'}"


class Topic(PublicModel):
    slug = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=80)
    entries = models.ManyToManyField(Entry, through="EntryTopic", related_name="topics")

    class Meta:
        ordering = ["slug"]
        verbose_name = "话题"
        verbose_name_plural = "话题"

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
        verbose_name = "内容话题"
        verbose_name_plural = "内容话题"
        constraints = [
            models.UniqueConstraint(fields=["entry", "topic"], name="publishing_unique_entry_topic")
        ]


def attachment_upload_path(instance, filename: str) -> str:
    del filename
    return f"entries/{instance.entry.public_id}/{instance.public_id}.webp"


class AttachmentQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Attachments must follow the entry lifecycle")

    def _raw_delete(self, using):
        raise ValidationError("Attachments must follow the entry lifecycle")


class Attachment(PublicModel):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        upload_to=attachment_upload_path,
        validators=[FileExtensionValidator(["webp"])],
    )
    mime_type = models.CharField(
        max_length=10,
        choices=(("image/webp", "WebP"),),
        default="image/webp",
        editable=False,
    )
    byte_size = models.PositiveIntegerField(editable=False)
    alt_text = models.CharField(max_length=240, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0, editable=False)

    objects = AttachmentQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["position", "created_at"]
        verbose_name = "图片附件"
        verbose_name_plural = "图片附件"
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
            models.CheckConstraint(
                condition=models.Q(mime_type="image/webp"),
                name="publishing_attachment_mime_webp",
            ),
            models.CheckConstraint(
                condition=models.Q(width__isnull=False, width__gt=0),
                name="publishing_attachment_width_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(height__isnull=False, height__gt=0),
                name="publishing_attachment_height_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(file__endswith=".webp"),
                name="publishing_attachment_file_webp",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}:{self.position}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Attachments must follow the entry lifecycle")
