import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from meppp.accounts.models import User
from meppp.audit.models import AuditEvent
from meppp.publishing.models import ContentState, Entry

from .models import (
    ModerationAction,
    ModerationDecision,
    Report,
    ReportReason,
    ReportStatus,
    SubjectType,
)
from .services import close_report


class ModerationServiceTests(TestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username="reporter")
        self.moderator = User.objects.create_superuser(
            username="moderator",
            password="moderator-test-password",
        )
        self.entry = Entry.objects.create(author=self.reporter, body="reported entry")
        self.report = Report.objects.create(
            reporter=self.reporter,
            subject_type=SubjectType.ENTRY,
            subject_public_id=self.entry.public_id,
            reason=ReportReason.SPAM,
        )

    def test_close_report_applies_action_decision_and_audit_in_one_transaction(self):
        report = close_report(
            report=self.report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="confirmed spam",
        )

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.HIDDEN)
        self.assertEqual(report.status, ReportStatus.RESOLVED)
        self.assertEqual(report.resolved_by, self.moderator)
        self.assertEqual(ModerationDecision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.get().action, "moderation.report.closed")

    def test_rejected_report_makes_no_target_change(self):
        report = close_report(
            report=self.report,
            actor=self.moderator,
            status=ReportStatus.REJECTED,
            action=ModerationAction.NO_ACTION,
            reason="not substantiated",
        )

        self.entry.refresh_from_db()
        self.assertEqual(report.status, ReportStatus.REJECTED)
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)

    def test_report_cannot_be_closed_twice_and_target_change_rolls_back(self):
        close_report(
            report=self.report,
            actor=self.moderator,
            status=ReportStatus.REJECTED,
            action=ModerationAction.NO_ACTION,
            reason="not substantiated",
        )

        with self.assertRaises(ValidationError):
            close_report(
                report=self.report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.HIDE_ENTRY,
                reason="late change",
            )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)

    def test_actor_must_have_moderation_permission(self):
        unprivileged = User.objects.create_user(username="unprivileged", is_staff=True)

        with self.assertRaisesMessage(ValidationError, "not allowed"):
            close_report(
                report=self.report,
                actor=unprivileged,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.HIDE_ENTRY,
                reason="unauthorized",
            )

    def test_action_must_match_subject(self):
        with self.assertRaisesMessage(ValidationError, "does not match"):
            close_report(
                report=self.report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.SUSPEND_USER,
                reason="wrong target type",
            )

    def test_duplicate_active_report_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                reporter=self.reporter,
                subject_type=SubjectType.ENTRY,
                subject_public_id=self.entry.public_id,
                reason=ReportReason.OTHER,
            )

    def test_same_action_can_resolve_another_report_after_target_is_already_hidden(self):
        close_report(
            report=self.report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="first report",
        )
        second_reporter = User.objects.create_user(username="second-reporter")
        second_report = Report.objects.create(
            reporter=second_reporter,
            subject_type=SubjectType.ENTRY,
            subject_public_id=self.entry.public_id,
            reason=ReportReason.SPAM,
        )

        result = close_report(
            report=second_report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="same target already handled",
        )

        self.assertEqual(result.status, ReportStatus.RESOLVED)
        second_decision = ModerationDecision.objects.get(report=second_report)
        event = AuditEvent.objects.get(target_public_id=second_report.public_id)
        self.assertEqual(second_decision.action, ModerationAction.HIDE_ENTRY)
        self.assertTrue(event.metadata["outcome"]["already_applied"])

    def test_report_evidence_cannot_be_changed_or_deleted(self):
        self.report.details = "rewritten"
        with self.assertRaisesMessage(ValidationError, "evidence"):
            self.report.save()
        with self.assertRaisesMessage(ValidationError, "evidence"):
            Report.objects.filter(pk=self.report.pk).update(details="rewritten")
        with self.assertRaisesMessage(ValidationError, "evidence"):
            Report._base_manager.filter(pk=self.report.pk).update(details="rewritten")
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            self.report.delete()

    def test_decision_is_append_only_for_instance_and_queryset(self):
        decision = ModerationDecision.objects.create(
            report=self.report,
            actor=self.moderator,
            action=ModerationAction.NO_ACTION,
            reason="recorded",
        )
        decision.reason = "changed"

        with self.assertRaises(ValidationError):
            decision.save()
        with self.assertRaises(ValidationError):
            ModerationDecision.objects.filter(pk=decision.pk).update(reason="changed")
        with self.assertRaises(ValidationError):
            ModerationDecision.objects.filter(pk=decision.pk).delete()
        with self.assertRaises(ValidationError):
            ModerationDecision._base_manager.filter(pk=decision.pk).update(reason="changed")


class ReportConstraintTests(TestCase):
    def test_final_status_requires_resolution_fields(self):
        reporter = User.objects.create_user(username="reporter")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                reporter=reporter,
                subject_type=SubjectType.ENTRY,
                subject_public_id=uuid.uuid4(),
                reason=ReportReason.SPAM,
                status=ReportStatus.RESOLVED,
            )
