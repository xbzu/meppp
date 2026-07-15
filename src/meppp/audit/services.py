from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model

from .models import AuditEvent


def record_event(
    *,
    action: str,
    target_type: str,
    actor=None,
    target_public_id: UUID | None = None,
    request_id: UUID | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    user_model = get_user_model()
    if actor is not None and not isinstance(actor, user_model):
        raise TypeError("actor must be a user instance or None")
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_public_id=target_public_id,
        request_id=request_id,
        reason=reason,
        metadata=metadata or {},
    )
