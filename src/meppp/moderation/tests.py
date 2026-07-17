import uuid
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from meppp.accounts.models import User
from meppp.audit.models import AuditEvent
from meppp.notifications.models import Notification, NotificationKind
from meppp.publishing.models import Comment, ContentState, Entry

from .models import (
    ModerationAction,
    ModerationDecision,
    Report,
    ReportQuerySet,
    ReportReason,
    ReportStatus,
    SubjectType,
)
from .services import MAX_MODERATION_REASON_LENGTH, close_report, triage_report


class ModerationServiceTests(TestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username="reporter")
        self.author = User.objects.create_user(username="content-author")
        self.moderator = User.objects.create_superuser(
            username="moderator",
            password="moderator-test-password",
        )
        self.entry = Entry.objects.create(author=self.author, body="reported entry")
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
        decision = ModerationDecision.objects.get()
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, "moderation.report.closed")
        self.assertEqual(decision.metadata["from_status"], ReportStatus.OPEN)
        self.assertEqual(decision.metadata["to_status"], ReportStatus.RESOLVED)
        self.assertEqual(decision.metadata["subject_type"], SubjectType.ENTRY)
        self.assertEqual(decision.metadata["subject_public_id"], str(self.entry.public_id))
        self.assertEqual(event.metadata["decision"], str(decision.public_id))
        self.assertEqual(event.metadata["resolved_by"], str(self.moderator.public_id))
        self.assertEqual(event.metadata["outcome"]["before"], ContentState.PUBLISHED)
        self.assertEqual(event.metadata["outcome"]["after"], ContentState.HIDDEN)
        notification = Notification.objects.get(kind=NotificationKind.MODERATION)
        self.assertEqual(notification.recipient, self.entry.author)
        self.assertIsNone(notification.actor)
        self.assertEqual(notification.payload["content_type"], SubjectType.ENTRY)
        self.assertEqual(notification.payload["content_public_id"], str(self.entry.public_id))
        self.assertEqual(notification.payload["outcome"], "hidden")
        self.assertNotIn("reason", notification.payload)
        self.assertNotIn(self.reporter.username, notification.payload.values())
        self.assertNotIn(str(self.reporter.public_id), notification.payload.values())
        self.assertFalse(Notification.objects.filter(recipient=self.reporter).exists())

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
        self.assertFalse(Notification.objects.exists())

    @patch("meppp.moderation.services.notify", side_effect=RuntimeError("notification failed"))
    def test_notification_failure_rolls_back_report_and_content_action(self, notify):
        with self.assertRaisesMessage(RuntimeError, "notification failed"):
            close_report(
                report=self.report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.HIDE_ENTRY,
                reason="must remain atomic",
            )

        self.report.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertFalse(Notification.objects.exists())
        notify.assert_called_once()

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
        self.assertEqual(ModerationDecision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

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

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

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
        self.assertEqual(Notification.objects.count(), 1)

    def test_triage_assigns_a_moderator_and_records_complete_audit_context(self):
        assignee = User.objects.create_user(username="assignee", is_staff=True)
        assignee.user_permissions.add(self._change_report_permission())

        report = triage_report(
            report=self.report,
            actor=self.moderator,
            assigned_to=assignee,
            reason="  needs policy review  ",
        )

        event = AuditEvent.objects.get(action="moderation.report.triaged")
        self.assertEqual(report.status, ReportStatus.TRIAGED)
        self.assertEqual(report.assigned_to, assignee)
        self.assertEqual(event.reason, "needs policy review")
        self.assertEqual(event.metadata["from_status"], ReportStatus.OPEN)
        self.assertEqual(event.metadata["to_status"], ReportStatus.TRIAGED)
        self.assertIsNone(event.metadata["before_assigned_to"])
        self.assertEqual(event.metadata["after_assigned_to"], str(assignee.public_id))
        self.assertEqual(event.metadata["subject_type"], SubjectType.ENTRY)
        self.assertEqual(event.metadata["subject_public_id"], str(self.entry.public_id))

    def test_triage_rejects_unprivileged_assignee_and_duplicate_transition(self):
        unprivileged = User.objects.create_user(username="assignee", is_staff=True)
        with self.assertRaisesMessage(ValidationError, "not allowed"):
            triage_report(
                report=self.report,
                actor=self.moderator,
                assigned_to=unprivileged,
                reason="first pass",
            )
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(AuditEvent.objects.count(), 0)

        triaged = triage_report(
            report=self.report,
            actor=self.moderator,
            assigned_to=self.moderator,
            reason="first pass",
        )
        with self.assertRaisesMessage(ValidationError, "already been triaged or closed"):
            triage_report(
                report=self.report,
                actor=self.moderator,
                assigned_to=self.moderator,
                reason="duplicate pass",
            )
        triaged.refresh_from_db()
        self.assertEqual(triaged.status, ReportStatus.TRIAGED)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_close_uses_persisted_evidence_instead_of_a_tampered_instance(self):
        other_entry = Entry.objects.create(author=self.reporter, body="unrelated entry")
        self.report.subject_public_id = other_entry.public_id

        close_report(
            report=self.report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="verified target",
        )

        self.entry.refresh_from_db()
        other_entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.HIDDEN)
        self.assertEqual(other_entry.state, ContentState.PUBLISHED)

    def test_failed_target_transition_rolls_back_report_decision_and_audit(self):
        Entry.objects.filter(pk=self.entry.pk).update(state=ContentState.DELETED)

        with self.assertRaisesMessage(ValidationError, "cannot transition"):
            close_report(
                report=self.report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.HIDE_ENTRY,
                reason="invalid target state",
            )

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertIsNone(self.report.resolved_by)
        self.assertIsNone(self.report.resolved_at)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_entry_hide_and_restore_actions(self):
        close_report(
            report=self.report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="hide entry",
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.HIDDEN)

        restore_report = self._new_report(SubjectType.ENTRY, self.entry.public_id)
        close_report(
            report=restore_report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.RESTORE_ENTRY,
            reason="restore entry",
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)

    def test_comment_hide_and_restore_actions(self):
        comment = Comment.objects.create(
            entry=self.entry,
            author=self.author,
            body="reported comment",
        )
        report = self._new_report(SubjectType.COMMENT, comment.public_id)
        close_report(
            report=report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_COMMENT,
            reason="hide comment",
        )
        comment.refresh_from_db()
        self.assertEqual(comment.state, ContentState.HIDDEN)

        restore_report = self._new_report(SubjectType.COMMENT, comment.public_id)
        close_report(
            report=restore_report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.RESTORE_COMMENT,
            reason="restore comment",
        )
        comment.refresh_from_db()
        self.assertEqual(comment.state, ContentState.PUBLISHED)
        notifications = list(
            Notification.objects.filter(
                recipient=self.author,
                kind=NotificationKind.MODERATION,
            ).order_by("created_at")
        )
        self.assertEqual(
            [notification.payload["content_type"] for notification in notifications],
            [SubjectType.COMMENT, SubjectType.COMMENT],
        )
        self.assertEqual(
            [notification.payload["outcome"] for notification in notifications],
            ["hidden", "restored"],
        )

    def test_user_suspend_and_restore_actions(self):
        target = User.objects.create_user(username="reported-user")
        report = self._new_report(SubjectType.USER, target.public_id)
        close_report(
            report=report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.SUSPEND_USER,
            reason="suspend member",
        )
        target.refresh_from_db()
        self.assertFalse(target.is_active)

        restore_report = self._new_report(SubjectType.USER, target.public_id)
        close_report(
            report=restore_report,
            actor=self.moderator,
            status=ReportStatus.RESOLVED,
            action=ModerationAction.RESTORE_USER,
            reason="restore member",
        )
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_staff_and_self_suspension_are_blocked_and_rolled_back(self):
        owner = User.objects.create_superuser(username="other-owner", password="owner-password")
        owner_report = self._new_report(SubjectType.USER, owner.public_id)
        with self.assertRaisesMessage(ValidationError, "not allowed"):
            close_report(
                report=owner_report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.SUSPEND_USER,
                reason="unsafe owner action",
            )
        owner_report.refresh_from_db()
        self.assertEqual(owner_report.status, ReportStatus.OPEN)

        staff = User.objects.create_user(username="other-staff", is_staff=True)
        staff_report = self._new_report(SubjectType.USER, staff.public_id)
        with self.assertRaisesMessage(ValidationError, "Staff"):
            close_report(
                report=staff_report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.SUSPEND_USER,
                reason="unsafe staff action",
            )
        staff_report.refresh_from_db()
        staff.refresh_from_db()
        self.assertEqual(staff_report.status, ReportStatus.OPEN)
        self.assertTrue(staff.is_active)

        self_report = self._new_report(SubjectType.USER, self.moderator.public_id)
        with self.assertRaisesMessage(ValidationError, "not allowed"):
            close_report(
                report=self_report,
                actor=self.moderator,
                status=ReportStatus.RESOLVED,
                action=ModerationAction.SUSPEND_USER,
                reason="unsafe self action",
            )
        self_report.refresh_from_db()
        self.assertEqual(self_report.status, ReportStatus.OPEN)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_reason_is_required_and_bounded_in_the_service(self):
        for reason in (None, "   ", "x" * (MAX_MODERATION_REASON_LENGTH + 1)):
            with self.subTest(reason=reason):
                with self.assertRaises(ValidationError):
                    close_report(
                        report=self.report,
                        actor=self.moderator,
                        status=ReportStatus.REJECTED,
                        action=ModerationAction.NO_ACTION,
                        reason=reason,
                    )
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)

    def test_invalid_close_status_and_status_action_combinations_are_rejected(self):
        invalid_combinations = (
            (ReportStatus.OPEN, ModerationAction.NO_ACTION, "resolved or rejected"),
            (ReportStatus.REJECTED, ModerationAction.HIDE_ENTRY, "cannot apply"),
            (ReportStatus.RESOLVED, ModerationAction.NO_ACTION, "must apply"),
        )
        for status, action, message in invalid_combinations:
            with self.subTest(status=status, action=action):
                with self.assertRaisesMessage(ValidationError, message):
                    close_report(
                        report=self.report,
                        actor=self.moderator,
                        status=status,
                        action=action,
                        reason="invalid combination",
                    )
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)

    def test_unsaved_or_missing_actor_and_report_are_rejected(self):
        missing_actor = User(pk=987_654, username="missing-moderator")
        with self.assertRaisesMessage(ValidationError, "not allowed"):
            close_report(
                report=self.report,
                actor=missing_actor,
                status=ReportStatus.REJECTED,
                action=ModerationAction.NO_ACTION,
                reason="missing actor",
            )
        with self.assertRaisesMessage(ValidationError, "not allowed"):
            close_report(
                report=self.report,
                actor=User(username="unsaved-moderator"),
                status=ReportStatus.REJECTED,
                action=ModerationAction.NO_ACTION,
                reason="unsaved actor",
            )

        unsaved_report = Report()
        missing_report = Report(pk=987_654)
        for operation in (triage_report, close_report):
            for report in (unsaved_report, missing_report):
                with self.subTest(operation=operation.__name__, report_pk=report.pk):
                    kwargs = {
                        "report": report,
                        "actor": self.moderator,
                        "reason": "missing report",
                    }
                    if operation is triage_report:
                        kwargs["assigned_to"] = self.moderator
                    else:
                        kwargs.update(
                            status=ReportStatus.REJECTED,
                            action=ModerationAction.NO_ACTION,
                        )
                    with self.assertRaisesMessage(ValidationError, "does not exist"):
                        operation(**kwargs)

    def test_missing_targets_roll_back_report_claim(self):
        action_by_subject = {
            SubjectType.ENTRY: ModerationAction.HIDE_ENTRY,
            SubjectType.COMMENT: ModerationAction.HIDE_COMMENT,
            SubjectType.USER: ModerationAction.SUSPEND_USER,
        }
        for subject_type, action in action_by_subject.items():
            with self.subTest(subject_type=subject_type):
                report = self._new_report(subject_type, uuid.uuid4())
                with self.assertRaisesMessage(ValidationError, "no longer exists"):
                    close_report(
                        report=report,
                        actor=self.moderator,
                        status=ReportStatus.RESOLVED,
                        action=action,
                        reason="missing target",
                    )
                report.refresh_from_db()
                self.assertEqual(report.status, ReportStatus.OPEN)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_conditional_report_updates_reject_concurrent_triage_and_close(self):
        with patch.object(ReportQuerySet, "update", return_value=0):
            with self.assertRaisesMessage(ValidationError, "already been triaged or closed"):
                triage_report(
                    report=self.report,
                    actor=self.moderator,
                    assigned_to=self.moderator,
                    reason="lost triage race",
                )

        with patch.object(ReportQuerySet, "update", return_value=0):
            with self.assertRaisesMessage(ValidationError, "already been closed"):
                close_report(
                    report=self.report,
                    actor=self.moderator,
                    status=ReportStatus.REJECTED,
                    action=ModerationAction.NO_ACTION,
                    reason="lost close race",
                )

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def _new_report(self, subject_type, subject_public_id):
        return Report.objects.create(
            reporter=self.reporter,
            subject_type=subject_type,
            subject_public_id=subject_public_id,
            reason=ReportReason.SPAM,
        )

    @staticmethod
    def _change_report_permission():
        return Permission.objects.get(
            content_type__app_label="moderation",
            codename="change_report",
        )

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


class ModerationAdminTests(TestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username="admin-reporter")
        self.moderator = User.objects.create_superuser(
            username="admin-moderator",
            password="admin-moderator-password",
        )
        self.entry = Entry.objects.create(author=self.reporter, body="admin reported entry")
        self.report = Report.objects.create(
            reporter=self.reporter,
            subject_type=SubjectType.ENTRY,
            subject_public_id=self.entry.public_id,
            reason=ReportReason.HARASSMENT,
            details="immutable evidence",
        )
        self.change_url = reverse("admin:moderation_report_change", args=[self.report.pk])
        self.workflow_url = reverse("admin:moderation_report_workflow", args=[self.report.pk])
        self.client.force_login(self.moderator)

    def test_change_page_is_read_only_and_links_to_the_workflow(self):
        response = self.client.get(self.change_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.workflow_url)
        self.assertContains(response, "处理举报")
        self.assertNotContains(response, 'name="_save"')

    def test_workflow_displays_immutable_evidence_and_subject_specific_actions(self):
        response = self.client.get(self.workflow_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "举报证据（只读）")
        self.assertContains(response, self.reporter.username)
        self.assertContains(response, str(self.entry.public_id))
        self.assertContains(response, "immutable evidence")
        self.assertContains(response, "隐藏内容")
        self.assertContains(response, "恢复内容")
        self.assertNotContains(response, "停用用户")
        self.assertNotContains(response, "隐藏评论")

    def test_admin_triage_uses_service_and_audits_assignment(self):
        response = self.client.post(
            self.workflow_url,
            {
                "operation": "triage",
                "triage-assigned_to": self.moderator.pk,
                "triage-reason": "  policy review  ",
            },
        )

        self.assertRedirects(response, self.change_url)
        self.report.refresh_from_db()
        event = AuditEvent.objects.get(action="moderation.report.triaged")
        self.assertEqual(self.report.status, ReportStatus.TRIAGED)
        self.assertEqual(self.report.assigned_to, self.moderator)
        self.assertEqual(event.reason, "policy review")
        self.assertEqual(event.metadata["after_assigned_to"], str(self.moderator.public_id))

    def test_admin_assignee_choices_exclude_staff_without_change_permission(self):
        permitted = User.objects.create_user(username="permitted-reviewer", is_staff=True)
        permitted.user_permissions.add(self._permission("change_report"))
        unprivileged = User.objects.create_user(username="unprivileged-reviewer", is_staff=True)

        response = self.client.get(self.workflow_url)

        self.assertContains(response, permitted.username)
        self.assertNotContains(response, unprivileged.username)

        response = self.client.post(
            self.workflow_url,
            {
                "operation": "triage",
                "triage-assigned_to": unprivileged.pk,
                "triage-reason": "invalid assignment",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "选择一个有效的选项")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_admin_can_hide_target_and_close_report_through_service(self):
        response = self._post_close(
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="confirmed harassment",
        )

        self.assertRedirects(response, self.change_url)
        self.report.refresh_from_db()
        self.entry.refresh_from_db()
        decision = ModerationDecision.objects.get(report=self.report)
        event = AuditEvent.objects.get(action="moderation.report.closed")
        self.assertEqual(self.report.status, ReportStatus.RESOLVED)
        self.assertEqual(self.entry.state, ContentState.HIDDEN)
        self.assertEqual(decision.action, ModerationAction.HIDE_ENTRY)
        self.assertEqual(event.metadata["action"], ModerationAction.HIDE_ENTRY)

    def test_admin_can_reject_report_without_changing_target(self):
        response = self._post_close(
            status=ReportStatus.REJECTED,
            action=ModerationAction.NO_ACTION,
            reason="evidence insufficient",
        )

        self.assertRedirects(response, self.change_url)
        self.report.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.REJECTED)
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)
        self.assertEqual(
            ModerationDecision.objects.get(report=self.report).action,
            ModerationAction.NO_ACTION,
        )

    def test_admin_rejects_action_for_another_subject_type(self):
        response = self._post_close(
            status=ReportStatus.RESOLVED,
            action=ModerationAction.SUSPEND_USER,
            reason="wrong action type",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "选择一个有效的选项")
        self.report.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_repeated_admin_close_is_idempotently_rejected(self):
        first_response = self._post_close(
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="first close",
        )
        self.assertEqual(first_response.status_code, 302)

        second_response = self._post_close(
            status=ReportStatus.RESOLVED,
            action=ModerationAction.HIDE_ENTRY,
            reason="duplicate close",
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertContains(second_response, "This report has already been closed")
        self.report.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.RESOLVED)
        self.assertEqual(self.entry.state, ContentState.HIDDEN)
        self.assertEqual(ModerationDecision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_repeated_admin_triage_is_rejected_without_second_audit(self):
        payload = {
            "operation": "triage",
            "triage-assigned_to": self.moderator.pk,
            "triage-reason": "assign once",
        }
        first_response = self.client.post(self.workflow_url, payload)
        self.assertEqual(first_response.status_code, 302)

        payload["triage-reason"] = "assign twice"
        second_response = self.client.post(self.workflow_url, payload)

        self.assertEqual(second_response.status_code, 200)
        self.assertContains(second_response, "already been triaged or closed")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.TRIAGED)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_admin_form_rejects_invalid_status_action_combinations(self):
        combinations = (
            (
                ReportStatus.REJECTED,
                ModerationAction.HIDE_ENTRY,
                "驳回举报时只能选择不处置。",
            ),
            (
                ReportStatus.RESOLVED,
                ModerationAction.NO_ACTION,
                "确认举报成立时必须选择一个对象操作。",
            ),
        )
        for status, action, message in combinations:
            with self.subTest(status=status, action=action):
                response = self._post_close(
                    status=status,
                    action=action,
                    reason="invalid combination",
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_unknown_workflow_operation_and_missing_report_are_rejected(self):
        with self.assertLogs("django", level="WARNING"):
            unknown_response = self.client.post(self.workflow_url, {"operation": "unknown"})
            missing_url = reverse("admin:moderation_report_workflow", args=[987_654])
            missing_response = self.client.get(missing_url)

        self.assertEqual(unknown_response.status_code, 400)
        self.assertEqual(missing_response.status_code, 404)

    def test_closed_report_detail_does_not_offer_another_workflow(self):
        self._post_close(
            status=ReportStatus.REJECTED,
            action=ModerationAction.NO_ACTION,
            reason="closed once",
        )

        response = self.client.get(self.change_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.workflow_url)

    def test_view_only_staff_can_read_evidence_but_cannot_open_workflow(self):
        viewer = User.objects.create_user(username="evidence-viewer", is_staff=True)
        viewer.user_permissions.add(self._permission("view_report"))
        self.client.force_login(viewer)

        change_response = self.client.get(self.change_url)
        with self.assertLogs("django.request", level="WARNING"):
            workflow_response = self.client.get(self.workflow_url)

        self.assertEqual(change_response.status_code, 200)
        self.assertContains(change_response, "immutable evidence")
        self.assertNotContains(change_response, self.workflow_url)
        self.assertEqual(workflow_response.status_code, 403)

    def test_direct_post_to_read_only_change_page_is_forbidden(self):
        with self.assertLogs("django.request", level="WARNING"):
            response = self.client.post(
                self.change_url,
                {
                    "details": "rewritten evidence",
                    "status": ReportStatus.RESOLVED,
                    "assigned_to": self.moderator.pk,
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.details, "immutable evidence")
        self.assertEqual(self.report.status, ReportStatus.OPEN)
        self.assertIsNone(self.report.assigned_to)
        self.assertEqual(ModerationDecision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def _post_close(self, *, status, action, reason):
        return self.client.post(
            self.workflow_url,
            {
                "operation": "close",
                "close-status": status,
                "close-action": action,
                "close-reason": reason,
            },
        )

    @staticmethod
    def _permission(codename):
        return Permission.objects.get(
            content_type__app_label="moderation",
            codename=codename,
        )


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
