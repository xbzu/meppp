from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from .models import Notification, NotificationKind


def notify(
    *,
    recipient,
    kind: str,
    actor=None,
    target_type: str = "",
    target_public_id: UUID | None = None,
    payload: dict | None = None,
) -> Notification | None:
    if actor is not None and actor.pk == recipient.pk:
        return None
    return Notification.objects.create(
        recipient=recipient,
        actor=actor,
        kind=kind,
        target_type=target_type,
        target_public_id=target_public_id,
        payload=payload or {},
    )


@transaction.atomic
def mark_all_read(*, recipient) -> int:
    return Notification.objects.filter(recipient=recipient, read_at__isnull=True).update(
        read_at=timezone.now()
    )


__all__ = ["NotificationKind", "mark_all_read", "notify"]
