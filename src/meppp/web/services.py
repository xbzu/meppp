from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable

from django.conf import settings
from django.db import transaction

from meppp.publishing.models import Comment, Entry, Topic
from meppp.publishing.services import add_comment, publish_entry

from .models import SubmissionClaim


class DuplicateSubmission(Exception):
    pass


def _token_digest(*, purpose: str, token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{purpose}\0{token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _claim_submission(*, member, purpose: str, token: str) -> None:
    _, created = SubmissionClaim.objects.get_or_create(
        member=member,
        purpose=purpose,
        token_digest=_token_digest(purpose=purpose, token=token),
    )
    if not created:
        raise DuplicateSubmission


@transaction.atomic
def publish_entry_once(
    *,
    author,
    body: str,
    topics: Iterable[Topic],
    purpose: str,
    token: str,
) -> Entry:
    _claim_submission(member=author, purpose=purpose, token=token)
    return publish_entry(author=author, body=body, topics=topics)


@transaction.atomic
def add_comment_once(
    *,
    author,
    entry_public_id,
    body: str,
    purpose: str,
    token: str,
) -> Comment:
    _claim_submission(member=author, purpose=purpose, token=token)
    return add_comment(author=author, entry_public_id=entry_public_id, body=body)
