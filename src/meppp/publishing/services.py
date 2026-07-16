from __future__ import annotations

import logging
from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from meppp.audit.services import record_event
from meppp.configuration.models import (
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_IMAGES_PER_POST,
    ModerationMode,
    SiteConfiguration,
)
from meppp.configuration.selectors import get_site_configuration
from meppp.notifications.models import NotificationKind
from meppp.notifications.services import notify

from .image_processing import ProcessedImage
from .models import (
    Attachment,
    Comment,
    ContentReviewDecision,
    ContentReviewOutcome,
    ContentState,
    Entry,
    Topic,
)

MAX_CONTENT_REVIEW_REASON_LENGTH = 500
logger = logging.getLogger(__name__)


def _require_active_member(member) -> None:
    if not member or not member.is_authenticated or not member.is_active:
        raise ValidationError("需要有效的成员账号")


def _clean_body(value: str, *, maximum: int, label: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError(f"{label}不能为空")
    if len(value) > maximum:
        raise ValidationError(f"{label}不能超过 {maximum} 个字符")
    return value


def _configuration_for_write() -> SiteConfiguration:
    return (
        SiteConfiguration.objects.select_for_update().filter(pk=1).first()
        or get_site_configuration()
    )


def cleanup_stored_files(stored_files: list[tuple]) -> None:
    cleanup_failures = 0
    for storage, name in reversed(stored_files):
        try:
            storage.delete(name)
        except Exception:  # pragma: no cover - storage-specific last-resort path
            cleanup_failures += 1
    if cleanup_failures:
        logger.error("media cleanup failed for %d generated file(s)", cleanup_failures)


def create_entry_records(
    *,
    author,
    body: str,
    topics: Iterable[Topic] = (),
    images: Iterable[ProcessedImage] = (),
    stored_files: list[tuple],
) -> Entry:
    _require_active_member(author)
    configuration = _configuration_for_write()
    body = _clean_body(value=body, maximum=configuration.post_max_length, label="正文")
    topics = list(topics)
    images = list(images)
    if len(topics) > 3:
        raise ValidationError("每条内容最多选择 3 个话题")
    maximum_images = min(configuration.max_images_per_post, MAX_IMAGES_PER_POST)
    maximum_image_bytes = min(configuration.upload_max_bytes, MAX_IMAGE_UPLOAD_BYTES)
    if len(images) > maximum_images:
        raise ValidationError("图片数量超过当前站点限制")
    for image in images:
        if not isinstance(image, ProcessedImage):
            raise ValidationError("图片尚未完成安全处理")
        if image.source_byte_size > maximum_image_bytes or image.byte_size > maximum_image_bytes:
            raise ValidationError("图片大小超过当前站点限制")

    state = (
        ContentState.PENDING
        if configuration.moderation_mode == ModerationMode.PREMODERATION
        else ContentState.PUBLISHED
    )
    entry = Entry(author=author, body=body, state=state)
    entry.full_clean()
    entry.save()
    if topics:
        entry.topics.set(topics)
    for position, image in enumerate(images):
        attachment = Attachment(
            entry=entry,
            mime_type=image.mime_type,
            byte_size=image.byte_size,
            alt_text=image.alt_text,
            width=image.width,
            height=image.height,
            position=position,
        )
        attachment.file.save("image.webp", ContentFile(image.content), save=False)
        stored_files.append((attachment.file.storage, attachment.file.name))
        attachment.full_clean()
        attachment.save()
    return entry


def publish_entry(
    *,
    author,
    body: str,
    topics: Iterable[Topic] = (),
    images: Iterable[ProcessedImage] = (),
) -> Entry:
    stored_files: list[tuple] = []
    try:
        with transaction.atomic():
            entry = create_entry_records(
                author=author,
                body=body,
                topics=topics,
                images=images,
                stored_files=stored_files,
            )
        return entry
    except BaseException:
        cleanup_stored_files(stored_files)
        raise


@transaction.atomic
def add_comment(*, author, entry_public_id, body: str) -> Comment:
    _require_active_member(author)
    entry = (
        Entry.objects.select_for_update()
        .filter(
            public_id=entry_public_id,
            state=ContentState.PUBLISHED,
            author__is_active=True,
        )
        .select_related("author")
        .first()
    )
    if entry is None:
        raise ValidationError("内容不存在或当前不可评论")

    configuration = _configuration_for_write()
    if not configuration.comments_enabled:
        raise ValidationError("站点当前已关闭评论")
    body = _clean_body(value=body, maximum=configuration.comment_max_length, label="评论")
    state = (
        ContentState.PENDING
        if configuration.moderation_mode == ModerationMode.PREMODERATION
        else ContentState.PUBLISHED
    )
    comment = Comment(entry=entry, author=author, body=body, state=state)
    comment.full_clean()
    comment.save()

    if state == ContentState.PUBLISHED:
        notify(
            recipient=entry.author,
            actor=author,
            kind=NotificationKind.COMMENT,
            target_type="entry",
            target_public_id=entry.public_id,
            payload={"actor_username": author.username},
        )
    return comment


def _clean_review_reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("审核理由不能为空")
    if len(reason) > MAX_CONTENT_REVIEW_REASON_LENGTH:
        raise ValidationError(f"审核理由不能超过 {MAX_CONTENT_REVIEW_REASON_LENGTH} 个字符")
    return reason


def _reviewer_with_permission(*, actor, permission: str):
    user_model = get_user_model()
    if not isinstance(actor, user_model) or actor.pk is None or actor._state.adding:
        raise ValidationError("当前账号没有内容审核权限")
    reviewer = user_model.objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
    if reviewer is None or not reviewer.has_perm(permission):
        raise ValidationError("当前账号没有内容审核权限")
    return reviewer


def _review_pending_content(*, target, model, actor, outcome: str, reason: str):
    try:
        outcome = ContentReviewOutcome(outcome)
    except ValueError as error:
        raise ValidationError("审核结论无效") from error
    reason = _clean_review_reason(reason)

    if not isinstance(target, model) or target.pk is None or target._state.adding:
        raise ValidationError("待审内容不存在")
    target_type = "entry" if model is Entry else "comment"
    reviewer = _reviewer_with_permission(
        actor=actor,
        permission=f"publishing.change_{target_type}",
    )
    related = ("author",) if model is Entry else ("author", "entry", "entry__author")
    locked = model.objects.select_for_update().select_related(*related).filter(pk=target.pk).first()
    if locked is None:
        raise ValidationError("待审内容不存在")
    if locked.state != ContentState.PENDING:
        raise ValidationError("这项内容已经处理，不能重复审核")

    after_state = (
        ContentState.PUBLISHED if outcome == ContentReviewOutcome.APPROVE else ContentState.HIDDEN
    )
    if outcome == ContentReviewOutcome.APPROVE and not locked.author.is_active:
        raise ValidationError("停用成员的内容不能批准公开")
    if (
        model is Comment
        and outcome == ContentReviewOutcome.APPROVE
        and (locked.entry.state != ContentState.PUBLISHED or not locked.entry.author.is_active)
    ):
        raise ValidationError("原内容当前不可公开，不能批准这条评论")

    reviewed_at = timezone.now()
    changed = model.objects.filter(pk=locked.pk, state=ContentState.PENDING).update(
        state=after_state,
        updated_at=reviewed_at,
    )
    if changed != 1:
        raise ValidationError("这项内容刚刚已被其他管理员处理，请刷新队列")

    decision_fields = {
        "entry": locked if model is Entry else None,
        "comment": locked if model is Comment else None,
        "actor": reviewer,
        "outcome": outcome,
        "reason": reason,
        "before_state": ContentState.PENDING,
        "after_state": after_state,
    }
    try:
        decision = ContentReviewDecision.objects.create(**decision_fields)
    except IntegrityError as error:
        raise ValidationError("这项内容已经存在审核决定，不能重复审核") from error

    metadata = {
        "before_state": ContentState.PENDING,
        "after_state": after_state,
        "outcome": outcome,
        "author_public_id": str(locked.author.public_id),
        "decision_public_id": str(decision.public_id),
    }
    if model is Comment:
        metadata["entry_public_id"] = str(locked.entry.public_id)
    record_event(
        actor=reviewer,
        action=f"publishing.{target_type}.reviewed",
        target_type=target_type,
        target_public_id=locked.public_id,
        reason=reason,
        metadata=metadata,
    )

    approved = outcome == ContentReviewOutcome.APPROVE
    notify(
        recipient=locked.author,
        actor=None,
        kind=NotificationKind.MODERATION,
        target_type="",
        target_public_id=None,
        payload={
            "content_type": target_type,
            "content_public_id": str(locked.public_id),
            "outcome": outcome,
            "reason": reason,
            "reviewer_username": reviewer.username,
        },
    )
    if model is Comment and approved:
        notify(
            recipient=locked.entry.author,
            actor=locked.author,
            kind=NotificationKind.COMMENT,
            target_type="entry",
            target_public_id=locked.entry.public_id,
            payload={
                "actor_username": locked.author.username,
                "comment_public_id": str(locked.public_id),
            },
        )

    locked.state = after_state
    locked.updated_at = reviewed_at
    return decision


@transaction.atomic
def review_entry(*, entry: Entry, actor, outcome: str, reason: str) -> ContentReviewDecision:
    return _review_pending_content(
        target=entry,
        model=Entry,
        actor=actor,
        outcome=outcome,
        reason=reason,
    )


@transaction.atomic
def review_comment(*, comment: Comment, actor, outcome: str, reason: str) -> ContentReviewDecision:
    return _review_pending_content(
        target=comment,
        model=Comment,
        actor=actor,
        outcome=outcome,
        reason=reason,
    )
