from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction

from meppp.configuration.models import ModerationMode, SiteConfiguration
from meppp.configuration.selectors import get_site_configuration
from meppp.notifications.models import NotificationKind
from meppp.notifications.services import notify

from .models import Comment, ContentState, Entry, Topic


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


@transaction.atomic
def publish_entry(*, author, body: str, topics: Iterable[Topic] = ()) -> Entry:
    _require_active_member(author)
    configuration = _configuration_for_write()
    body = _clean_body(value=body, maximum=configuration.post_max_length, label="正文")
    topics = list(topics)
    if len(topics) > 3:
        raise ValidationError("每条内容最多选择 3 个话题")

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
    return entry


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
