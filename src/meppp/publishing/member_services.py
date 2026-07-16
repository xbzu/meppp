from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from meppp.audit.services import record_event

from .models import Comment, ContentState, Entry


def _require_active_member(member) -> None:
    if not member or not member.is_authenticated or not member.is_active:
        raise ValidationError("需要有效的成员账号")


@transaction.atomic
def withdraw_entry(*, actor, entry_public_id) -> Entry:
    _require_active_member(actor)
    entry = Entry.objects.select_for_update().get(
        public_id=entry_public_id,
        author=actor,
    )
    if entry.state == ContentState.DELETED:
        return entry
    if entry.state not in {ContentState.PENDING, ContentState.PUBLISHED}:
        raise ValidationError("这条内容当前不能由作者撤回")

    previous_state = entry.state
    entry.state = ContentState.DELETED
    entry.save(update_fields=("state", "updated_at"))
    record_event(
        actor=actor,
        action="entry.withdrawn",
        target_type="entry",
        target_public_id=entry.public_id,
        metadata={"schema_version": 1, "before": previous_state, "after": entry.state},
    )
    return entry


@transaction.atomic
def withdraw_comment(*, actor, comment_public_id) -> Comment:
    _require_active_member(actor)
    comment = Comment.objects.select_for_update().get(
        public_id=comment_public_id,
        author=actor,
    )
    if comment.state == ContentState.DELETED:
        return comment
    if comment.state not in {ContentState.PENDING, ContentState.PUBLISHED}:
        raise ValidationError("这条评论当前不能由作者撤回")

    previous_state = comment.state
    comment.state = ContentState.DELETED
    comment.save(update_fields=("state", "updated_at"))
    record_event(
        actor=actor,
        action="comment.withdrawn",
        target_type="comment",
        target_public_id=comment.public_id,
        metadata={"schema_version": 1, "before": previous_state, "after": comment.state},
    )
    return comment
