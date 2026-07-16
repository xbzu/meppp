from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from meppp.notifications.models import NotificationKind
from meppp.notifications.services import notify
from meppp.publishing.models import ContentState, Entry

from .models import EntryLike, Follow


def _require_active_member(member) -> None:
    if not member or not member.is_authenticated or not member.is_active:
        raise ValidationError("需要有效的成员账号")


@transaction.atomic
def set_entry_like(*, actor, entry_public_id, liked: bool) -> bool:
    _require_active_member(actor)
    entry = (
        Entry.objects.filter(
            public_id=entry_public_id,
            state=ContentState.PUBLISHED,
            author__is_active=True,
        )
        .select_related("author")
        .first()
    )
    if entry is None:
        raise ValidationError("内容不存在或当前不可点赞")

    if liked:
        _, created = EntryLike.objects.get_or_create(actor=actor, entry=entry)
        if created:
            notify(
                recipient=entry.author,
                actor=actor,
                kind=NotificationKind.LIKE,
                target_type="entry",
                target_public_id=entry.public_id,
                payload={"actor_username": actor.username},
            )
        return True

    EntryLike.objects.filter(actor=actor, entry=entry).delete()
    return False


@transaction.atomic
def set_follow(*, actor, member_public_id, following: bool) -> bool:
    _require_active_member(actor)
    user_model = get_user_model()
    member = user_model.objects.filter(public_id=member_public_id, is_active=True).first()
    if member is None:
        raise ValidationError("成员不存在")
    if member.pk == actor.pk:
        raise ValidationError("不能关注自己")

    if following:
        _, created = Follow.objects.get_or_create(follower=actor, followed=member)
        if created:
            notify(
                recipient=member,
                actor=actor,
                kind=NotificationKind.FOLLOW,
                target_type="user",
                target_public_id=actor.public_id,
                payload={"actor_username": actor.username},
            )
        return True

    Follow.objects.filter(follower=actor, followed=member).delete()
    return False
