from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from meppp.audit.services import record_event
from meppp.configuration.models import RegistrationMode, SiteConfiguration

from .models import Invitation, RecoveryCredential, User
from .normalization import normalize_username

INVITATION_TOKEN_BYTES = 32
INVITATION_HINT_LENGTH = 8
MAX_INVITATION_REASON_LENGTH = 500
INVITATION_UNAVAILABLE_MESSAGE = "邀请码无效、已失效或不适用于这个邮箱"
RECOVERY_CODE_BYTES = 24
RECOVERY_UNAVAILABLE_MESSAGE = "账号信息或恢复码无效"
DUMMY_RECOVERY_DIGEST = make_password("invalid-recovery-code")


def _normalize_email(value: str) -> str:
    return value.strip().lower()


@sensitive_variables("token")
def _token_digest(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def _canonical_staff_actor(actor, *, permission: str) -> User:
    actor_id = getattr(actor, "pk", None)
    if actor_id is None:
        raise ValidationError("当前账号无权管理邀请")
    try:
        actor = User.objects.get(pk=actor_id)
    except User.DoesNotExist as error:
        raise ValidationError("当前账号无权管理邀请") from error
    if not actor.is_active or not actor.is_staff or not actor.has_perm(permission):
        raise ValidationError("当前账号无权管理邀请")
    return actor


def _clean_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise ValidationError("撤销原因不能为空")
    if len(reason) > MAX_INVITATION_REASON_LENGTH:
        raise ValidationError(f"撤销原因不能超过 {MAX_INVITATION_REASON_LENGTH} 个字符")
    return reason


@sensitive_variables("plaintext_token")
@transaction.atomic
def issue_invitation(
    *,
    issuer,
    expires_at: datetime,
    bound_email: str = "",
) -> tuple[Invitation, str]:
    issuer = _canonical_staff_actor(issuer, permission="accounts.add_invitation")
    if timezone.is_naive(expires_at) or expires_at <= timezone.now():
        raise ValidationError("邀请有效期必须是未来时间")
    bound_email = _normalize_email(bound_email)

    for _ in range(3):
        plaintext_token = secrets.token_urlsafe(INVITATION_TOKEN_BYTES)
        invitation = Invitation(
            token_digest=_token_digest(plaintext_token),
            hint=plaintext_token[-INVITATION_HINT_LENGTH:],
            issuer=issuer,
            expires_at=expires_at,
            bound_email=bound_email,
        )
        invitation.full_clean()
        try:
            with transaction.atomic():
                invitation.save(force_insert=True)
        except IntegrityError:
            continue
        break
    else:
        raise ValidationError("暂时无法生成唯一邀请码，请重试")

    record_event(
        actor=issuer,
        action="account.invitation.issued",
        target_type="invitation",
        target_public_id=invitation.public_id,
        metadata={
            "hint": invitation.hint,
            "expires_at": invitation.expires_at.isoformat(),
            "bound_email_restricted": bool(invitation.bound_email),
        },
    )
    return invitation, plaintext_token


@transaction.atomic
def revoke_invitation(*, invitation: Invitation, actor, reason: str) -> Invitation:
    actor = _canonical_staff_actor(actor, permission="accounts.change_invitation")
    reason = _clean_reason(reason)
    invitation_id = getattr(invitation, "pk", None)
    if invitation_id is None:
        raise ValidationError("邀请不存在")
    try:
        current = Invitation.objects.select_for_update().get(pk=invitation_id)
    except Invitation.DoesNotExist as error:
        raise ValidationError("邀请不存在") from error
    if current.claimed_at is not None:
        raise ValidationError("已经领取的邀请不能撤销")
    if current.revoked_at is not None:
        raise ValidationError("邀请已经撤销")

    revoked_at = timezone.now()
    updated = Invitation.objects.filter(
        pk=current.pk,
        claimed_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=revoked_at)
    if updated != 1:
        raise ValidationError("邀请状态已经发生变化，请刷新后重试")
    record_event(
        actor=actor,
        action="account.invitation.revoked",
        target_type="invitation",
        target_public_id=current.public_id,
        reason=reason,
        metadata={"hint": current.hint},
    )
    current.refresh_from_db()
    return current


@sensitive_variables("invitation_token", "digest")
@transaction.atomic
def claim_invitation(
    *,
    invitation_token: str,
    email: str,
    claimed_by: User,
) -> Invitation:
    if not invitation_token.strip():
        raise ValidationError(INVITATION_UNAVAILABLE_MESSAGE)
    claimed_by_id = getattr(claimed_by, "pk", None)
    if claimed_by_id is None or not claimed_by.is_active:
        raise ValidationError(INVITATION_UNAVAILABLE_MESSAGE)

    now = timezone.now()
    digest = _token_digest(invitation_token)
    invitation = Invitation.objects.select_for_update().filter(token_digest=digest).first()
    normalized_email = _normalize_email(email)
    if (
        invitation is None
        or invitation.revoked_at is not None
        or invitation.claimed_at is not None
        or invitation.expires_at <= now
        or (invitation.bound_email and invitation.bound_email != normalized_email)
    ):
        raise ValidationError(INVITATION_UNAVAILABLE_MESSAGE)

    updated = Invitation.objects.filter(
        pk=invitation.pk,
        claimed_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).update(claimed_at=now, claimed_by_id=claimed_by_id)
    if updated != 1:
        raise ValidationError(INVITATION_UNAVAILABLE_MESSAGE)

    record_event(
        actor=claimed_by,
        action="account.invitation.claimed",
        target_type="invitation",
        target_public_id=invitation.public_id,
        metadata={
            "hint": invitation.hint,
            "claimed_by": str(claimed_by.public_id),
        },
    )
    invitation.refresh_from_db()
    return invitation


@sensitive_variables("password", "invitation_token")
@transaction.atomic
def register_member(
    *,
    username: str,
    email: str,
    password: str,
    invitation_token: str = "",
) -> User:
    configuration, _ = SiteConfiguration.objects.select_for_update().get_or_create(pk=1)
    if configuration.registration_mode == RegistrationMode.CLOSED:
        raise ValidationError("站点当前未开放注册")
    if configuration.registration_mode == RegistrationMode.INVITE and not invitation_token.strip():
        raise ValidationError("请输入有效的邀请码")

    email = _normalize_email(email)
    if not email:
        raise ValidationError("请输入有效邮箱")

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
    )
    if configuration.registration_mode == RegistrationMode.INVITE:
        claim_invitation(
            invitation_token=invitation_token,
            email=user.email,
            claimed_by=user,
        )
    return user


@sensitive_variables("plaintext_code")
def issue_recovery_code(*, user: User) -> str:
    if user.pk is None or not user.is_active:
        raise ValidationError("当前账号不能生成恢复码")
    plaintext_code = secrets.token_urlsafe(RECOVERY_CODE_BYTES)
    RecoveryCredential.objects.update_or_create(
        user=user,
        defaults={
            "token_digest": make_password(plaintext_code),
            "issued_at": timezone.now(),
        },
    )
    record_event(
        actor=user,
        action="account.recovery_code.rotated",
        target_type="user",
        target_public_id=user.public_id,
        metadata={"delivery": "display_once"},
    )
    return plaintext_code


@sensitive_variables("password", "invitation_token", "plaintext_code")
@transaction.atomic
def register_member_with_recovery(
    *,
    username: str,
    email: str,
    password: str,
    invitation_token: str = "",
) -> tuple[User, str]:
    user = register_member(
        username=username,
        email=email,
        password=password,
        invitation_token=invitation_token,
    )
    plaintext_code = issue_recovery_code(user=user)
    return user, plaintext_code


@sensitive_variables("recovery_code", "new_password", "replacement_code")
@transaction.atomic
def recover_account(
    *,
    username: str,
    email: str,
    recovery_code: str,
    new_password: str,
) -> tuple[User, str]:
    normalized_username = normalize_username(username)
    normalized_email = _normalize_email(email)
    user = (
        User.objects.select_for_update()
        .filter(
            username__iexact=normalized_username,
            email__iexact=normalized_email,
            is_active=True,
        )
        .select_related("recovery_credential")
        .first()
    )
    credential = getattr(user, "recovery_credential", None) if user is not None else None
    digest = credential.token_digest if credential is not None else DUMMY_RECOVERY_DIGEST
    valid = bool(recovery_code.strip()) and check_password(recovery_code.strip(), digest)
    if user is None or credential is None or not valid:
        raise ValidationError(RECOVERY_UNAVAILABLE_MESSAGE)

    validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    replacement_code = issue_recovery_code(user=user)
    record_event(
        actor=user,
        action="account.password.recovered",
        target_type="user",
        target_public_id=user.public_id,
        metadata={"recovery_code_rotated": True},
    )
    return user, replacement_code
