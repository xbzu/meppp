from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from meppp.audit.services import record_event
from meppp.publishing.models import Comment, ContentState, Entry

from .models import (
    ModerationAction,
    ModerationDecision,
    Report,
    ReportReason,
    ReportStatus,
    SubjectType,
)

FINAL_STATUSES = frozenset({ReportStatus.RESOLVED, ReportStatus.REJECTED})
ACTIVE_REPORT_STATUSES = (ReportStatus.OPEN, ReportStatus.TRIAGED)

ACTION_SUBJECTS = {
    ModerationAction.HIDE_ENTRY: SubjectType.ENTRY,
    ModerationAction.RESTORE_ENTRY: SubjectType.ENTRY,
    ModerationAction.HIDE_COMMENT: SubjectType.COMMENT,
    ModerationAction.RESTORE_COMMENT: SubjectType.COMMENT,
    ModerationAction.SUSPEND_USER: SubjectType.USER,
    ModerationAction.RESTORE_USER: SubjectType.USER,
}


def _visible_report_target(*, subject_type: str, subject_public_id):
    if subject_type == SubjectType.USER:
        return get_user_model().objects.filter(public_id=subject_public_id, is_active=True).first()
    if subject_type == SubjectType.ENTRY:
        return Entry.objects.filter(
            public_id=subject_public_id,
            state=ContentState.PUBLISHED,
            author__is_active=True,
        ).first()
    if subject_type == SubjectType.COMMENT:
        return (
            Comment.objects.select_related("entry")
            .filter(
                public_id=subject_public_id,
                state=ContentState.PUBLISHED,
                author__is_active=True,
                entry__state=ContentState.PUBLISHED,
                entry__author__is_active=True,
            )
            .first()
        )
    return None


def submit_report(*, reporter, subject_type: str, subject_public_id, reason: str, details: str):
    if not reporter or not reporter.is_authenticated or not reporter.is_active:
        raise ValidationError("需要有效的成员账号")
    if reason not in ReportReason.values:
        raise ValidationError("举报原因无效")
    details = details.strip()
    if reason == ReportReason.OTHER and not details:
        raise ValidationError("选择其他原因时请补充说明")
    if len(details) > 1_000:
        raise ValidationError("补充说明不能超过 1000 个字符")

    target = _visible_report_target(
        subject_type=subject_type,
        subject_public_id=subject_public_id,
    )
    if target is None:
        raise ValidationError("无法提交此举报")
    target_owner_id = target.pk if subject_type == SubjectType.USER else target.author_id
    if target_owner_id == reporter.pk:
        raise ValidationError("不能举报自己的账号或内容")

    try:
        with transaction.atomic():
            report = Report(
                reporter=reporter,
                subject_type=subject_type,
                subject_public_id=subject_public_id,
                reason=reason,
                details=details,
            )
            report.full_clean(validate_constraints=False)
            report.save(force_insert=True)
        return report, True
    except IntegrityError:
        existing = Report.objects.filter(
            reporter=reporter,
            subject_type=subject_type,
            subject_public_id=subject_public_id,
            status__in=ACTIVE_REPORT_STATUSES,
        ).first()
        if existing is None:
            raise
        return existing, False


def _require_moderator(actor) -> None:
    if not (
        actor and actor.is_active and actor.is_staff and actor.has_perm("moderation.change_report")
    ):
        raise ValidationError("The actor is not allowed to close reports")


def _apply_action(*, report: Report, actor, action: str) -> dict:
    if action == ModerationAction.NO_ACTION:
        return {"changed": False}

    expected_subject = ACTION_SUBJECTS.get(action)
    if expected_subject is None or report.subject_type != expected_subject:
        raise ValidationError("The moderation action does not match the report subject")

    if action in {ModerationAction.HIDE_ENTRY, ModerationAction.RESTORE_ENTRY}:
        before = (
            ContentState.PUBLISHED if action == ModerationAction.HIDE_ENTRY else ContentState.HIDDEN
        )
        after = (
            ContentState.HIDDEN if action == ModerationAction.HIDE_ENTRY else ContentState.PUBLISHED
        )
        target = Entry.objects.filter(public_id=report.subject_public_id).first()
        if target is None:
            raise ValidationError("The reported entry no longer exists")
        if target.state == after:
            return {"changed": False, "already_applied": True, "before": before, "after": after}
        if target.state != before:
            raise ValidationError("The entry cannot transition from its current state")
        updated = Entry.objects.filter(pk=target.pk, state=before).update(state=after)
        target_queryset = Entry.objects.filter(pk=target.pk)
    elif action in {ModerationAction.HIDE_COMMENT, ModerationAction.RESTORE_COMMENT}:
        before = (
            ContentState.PUBLISHED
            if action == ModerationAction.HIDE_COMMENT
            else ContentState.HIDDEN
        )
        after = (
            ContentState.HIDDEN
            if action == ModerationAction.HIDE_COMMENT
            else ContentState.PUBLISHED
        )
        target = Comment.objects.filter(public_id=report.subject_public_id).first()
        if target is None:
            raise ValidationError("The reported comment no longer exists")
        if target.state == after:
            return {"changed": False, "already_applied": True, "before": before, "after": after}
        if target.state != before:
            raise ValidationError("The comment cannot transition from its current state")
        updated = Comment.objects.filter(pk=target.pk, state=before).update(state=after)
        target_queryset = Comment.objects.filter(pk=target.pk)
    else:
        user_model = get_user_model()
        target = user_model.objects.filter(public_id=report.subject_public_id).first()
        if target is None:
            raise ValidationError("The reported user no longer exists")
        if target.is_superuser or target.pk == actor.pk:
            raise ValidationError("Owner and self-suspension actions are not allowed")
        before = action == ModerationAction.SUSPEND_USER
        after = not before
        if target.is_active == after:
            return {"changed": False, "already_applied": True, "before": before, "after": after}
        updated = user_model.objects.filter(pk=target.pk, is_active=before).update(is_active=after)
        target_queryset = user_model.objects.filter(pk=target.pk)

    if updated != 1:
        state_field = "is_active" if report.subject_type == SubjectType.USER else "state"
        if target_queryset.filter(**{state_field: after}).exists():
            return {"changed": False, "already_applied": True, "before": before, "after": after}
        raise ValidationError("The target is missing or already in the requested state")
    return {"changed": True, "already_applied": False, "before": before, "after": after}


@transaction.atomic
def close_report(*, report: Report, actor, status: str, action: str, reason: str) -> Report:
    _require_moderator(actor)
    if status not in FINAL_STATUSES:
        raise ValidationError("A closed report must be resolved or rejected")
    if status == ReportStatus.REJECTED and action != ModerationAction.NO_ACTION:
        raise ValidationError("Rejected reports cannot apply a moderation action")
    if status == ReportStatus.RESOLVED and action == ModerationAction.NO_ACTION:
        raise ValidationError("Resolved reports must apply a moderation action")

    outcome = _apply_action(report=report, actor=actor, action=action)

    updated = Report.objects.filter(
        pk=report.pk,
        status__in=[ReportStatus.OPEN, ReportStatus.TRIAGED],
    ).update(status=status, resolved_by=actor, resolved_at=timezone.now())
    if updated != 1:
        raise ValidationError("This report has already been closed")

    decision = ModerationDecision.objects.create(
        report=report,
        actor=actor,
        action=action,
        reason=reason,
    )
    record_event(
        actor=actor,
        action="moderation.report.closed",
        target_type="report",
        target_public_id=report.public_id,
        reason=reason,
        metadata={
            "status": status,
            "decision": str(decision.public_id),
            "action": action,
            "target": str(report.subject_public_id),
            "outcome": outcome,
        },
    )
    report.refresh_from_db()
    return report
