from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from meppp.accounts.models import User
from meppp.audit.models import AuditEvent
from meppp.notifications.models import Notification, NotificationKind

from .models import (
    Comment,
    ContentReviewDecision,
    ContentReviewOutcome,
    ContentState,
    Entry,
    EntryQuerySet,
    Topic,
)
from .services import MAX_CONTENT_REVIEW_REASON_LENGTH, review_comment, review_entry


class PublishingModelTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author")

    def test_public_queryset_excludes_hidden_and_deleted_entries(self):
        visible = Entry.objects.create(author=self.author, body="visible")
        Entry.objects.create(author=self.author, body="pending", state=ContentState.PENDING)
        Entry.objects.create(author=self.author, body="hidden", state=ContentState.HIDDEN)
        Entry.objects.create(author=self.author, body="deleted", state=ContentState.DELETED)

        self.assertEqual(list(Entry.objects.public()), [visible])

    def test_topic_slug_is_normalized(self):
        topic = Topic.objects.create(slug="  Python ", label="Python")

        self.assertEqual(topic.slug, "python")

    def test_entries_use_soft_delete_state(self):
        entry = Entry.objects.create(author=self.author, body="preserve me")

        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            Entry.objects.filter(pk=entry.pk).delete()
        with self.assertRaises(ValidationError):
            Entry._base_manager.filter(pk=entry.pk).delete()


class ContentReviewServiceTests(TestCase):
    def setUp(self):
        self.entry_author = User.objects.create_user(username="entry-author")
        self.comment_author = User.objects.create_user(username="comment-author")
        self.reviewer = User.objects.create_superuser(username="reviewer")
        self.entry = Entry.objects.create(
            author=self.entry_author,
            body="pending entry",
            state=ContentState.PENDING,
        )
        self.parent_entry = Entry.objects.create(author=self.entry_author, body="published parent")
        self.comment = Comment.objects.create(
            entry=self.parent_entry,
            author=self.comment_author,
            body="pending comment",
            state=ContentState.PENDING,
        )

    def test_approve_entry_is_atomic_audited_append_only_and_notifies_author(self):
        decision = review_entry(
            entry=self.entry,
            actor=self.reviewer,
            outcome=ContentReviewOutcome.APPROVE,
            reason="  符合社区规则  ",
        )

        self.entry.refresh_from_db()
        event = AuditEvent.objects.get(action="publishing.entry.reviewed")
        notification = Notification.objects.get(kind=NotificationKind.MODERATION)
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)
        self.assertEqual(decision.entry, self.entry)
        self.assertIsNone(decision.comment)
        self.assertEqual(decision.reason, "符合社区规则")
        self.assertEqual(decision.before_state, ContentState.PENDING)
        self.assertEqual(decision.after_state, ContentState.PUBLISHED)
        self.assertEqual(event.target_public_id, self.entry.public_id)
        self.assertEqual(event.metadata["decision_public_id"], str(decision.public_id))
        self.assertEqual(notification.recipient, self.entry_author)
        self.assertEqual(notification.target_type, "")
        self.assertIsNone(notification.target_public_id)
        self.assertEqual(notification.payload["outcome"], ContentReviewOutcome.APPROVE)

        decision.reason = "rewritten"
        with self.assertRaises(ValidationError):
            decision.save()
        with self.assertRaises(ValidationError):
            ContentReviewDecision.objects.filter(pk=decision.pk).update(reason="rewritten")
        with self.assertRaises(ValidationError):
            ContentReviewDecision.objects.filter(pk=decision.pk).delete()

    def test_reject_entry_hides_it_and_sends_no_broken_public_link(self):
        decision = review_entry(
            entry=self.entry,
            actor=self.reviewer,
            outcome=ContentReviewOutcome.REJECT,
            reason="包含无法核实的推广信息",
        )

        self.entry.refresh_from_db()
        notification = Notification.objects.get(kind=NotificationKind.MODERATION)
        self.assertEqual(self.entry.state, ContentState.HIDDEN)
        self.assertEqual(decision.after_state, ContentState.HIDDEN)
        self.assertEqual(notification.target_type, "")
        self.assertIsNone(notification.target_public_id)
        self.assertEqual(notification.payload["outcome"], ContentReviewOutcome.REJECT)

    def test_approve_comment_notifies_comment_author_then_original_entry_author(self):
        decision = review_comment(
            comment=self.comment,
            actor=self.reviewer,
            outcome=ContentReviewOutcome.APPROVE,
            reason="回应具体且符合规则",
        )

        self.comment.refresh_from_db()
        notifications = list(Notification.objects.order_by("created_at", "pk"))
        self.assertEqual(self.comment.state, ContentState.PUBLISHED)
        self.assertEqual(decision.comment, self.comment)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0].kind, NotificationKind.MODERATION)
        self.assertEqual(notifications[0].recipient, self.comment_author)
        self.assertEqual(notifications[1].kind, NotificationKind.COMMENT)
        self.assertEqual(notifications[1].recipient, self.entry_author)
        self.assertEqual(notifications[1].actor, self.comment_author)
        self.assertEqual(notifications[1].target_public_id, self.parent_entry.public_id)

    def test_reject_comment_only_notifies_its_author(self):
        review_comment(
            comment=self.comment,
            actor=self.reviewer,
            outcome=ContentReviewOutcome.REJECT,
            reason="偏离原讨论主题",
        )

        self.comment.refresh_from_db()
        notification = Notification.objects.get()
        self.assertEqual(self.comment.state, ContentState.HIDDEN)
        self.assertEqual(notification.kind, NotificationKind.MODERATION)
        self.assertEqual(notification.recipient, self.comment_author)
        self.assertEqual(Notification.objects.count(), 1)

    def test_invalid_outcome_reason_and_nonpending_state_leave_no_side_effects(self):
        invalid_calls = (
            {"outcome": "invalid", "reason": "reason"},
            {"outcome": ContentReviewOutcome.APPROVE, "reason": "   "},
            {
                "outcome": ContentReviewOutcome.APPROVE,
                "reason": "x" * (MAX_CONTENT_REVIEW_REASON_LENGTH + 1),
            },
        )
        for kwargs in invalid_calls:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    review_entry(entry=self.entry, actor=self.reviewer, **kwargs)

        published = Entry.objects.create(author=self.entry_author, body="already public")
        with self.assertRaisesMessage(ValidationError, "已经处理"):
            review_entry(
                entry=published,
                actor=self.reviewer,
                outcome=ContentReviewOutcome.REJECT,
                reason="late review",
            )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.PENDING)
        self.assertFalse(ContentReviewDecision.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_exact_target_permission_is_required(self):
        entry_reviewer = User.objects.create_user(username="entry-reviewer", is_staff=True)
        entry_reviewer.user_permissions.add(self._permission("change_entry"))
        unprivileged = User.objects.create_user(username="unprivileged", is_staff=True)

        with self.assertRaisesMessage(ValidationError, "没有内容审核权限"):
            review_entry(
                entry=self.entry,
                actor=unprivileged,
                outcome=ContentReviewOutcome.APPROVE,
                reason="not allowed",
            )
        with self.assertRaisesMessage(ValidationError, "没有内容审核权限"):
            review_comment(
                comment=self.comment,
                actor=entry_reviewer,
                outcome=ContentReviewOutcome.APPROVE,
                reason="wrong permission",
            )

        review_entry(
            entry=self.entry,
            actor=entry_reviewer,
            outcome=ContentReviewOutcome.APPROVE,
            reason="permitted entry review",
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.PUBLISHED)

    def test_conditional_update_blocks_concurrent_or_repeated_review(self):
        with patch.object(EntryQuerySet, "update", return_value=0):
            with self.assertRaisesMessage(ValidationError, "其他管理员"):
                review_entry(
                    entry=self.entry,
                    actor=self.reviewer,
                    outcome=ContentReviewOutcome.APPROVE,
                    reason="lost race",
                )

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.state, ContentState.PENDING)
        self.assertFalse(ContentReviewDecision.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertFalse(Notification.objects.exists())

        review_entry(
            entry=self.entry,
            actor=self.reviewer,
            outcome=ContentReviewOutcome.APPROVE,
            reason="first and only decision",
        )
        with self.assertRaisesMessage(ValidationError, "已经处理"):
            review_entry(
                entry=self.entry,
                actor=self.reviewer,
                outcome=ContentReviewOutcome.REJECT,
                reason="second decision",
            )
        self.assertEqual(ContentReviewDecision.objects.count(), 1)

    def test_approval_requires_active_author_and_visible_parent_entry(self):
        self.comment_author.is_active = False
        self.comment_author.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValidationError, "停用成员"):
            review_comment(
                comment=self.comment,
                actor=self.reviewer,
                outcome=ContentReviewOutcome.APPROVE,
                reason="cannot publish inactive author",
            )

        self.comment_author.is_active = True
        self.comment_author.save(update_fields=["is_active"])
        self.parent_entry.state = ContentState.HIDDEN
        self.parent_entry.save(update_fields=["state"])
        with self.assertRaisesMessage(ValidationError, "原内容当前不可公开"):
            review_comment(
                comment=self.comment,
                actor=self.reviewer,
                outcome=ContentReviewOutcome.APPROVE,
                reason="cannot publish under hidden entry",
            )
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.state, ContentState.PENDING)

    @staticmethod
    def _permission(codename):
        return Permission.objects.get(content_type__app_label="publishing", codename=codename)


class ContentReviewAdminTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="queue-author")
        self.commenter = User.objects.create_user(username="queue-commenter")
        self.reviewer = User.objects.create_superuser(
            username="queue-reviewer",
            password="queue-reviewer-password",
        )
        self.pending_entry = Entry.objects.create(
            author=self.author,
            body="需要审核的公开内容",
            state=ContentState.PENDING,
        )
        self.published_entry = Entry.objects.create(author=self.author, body="已经公开的内容")
        self.pending_comment = Comment.objects.create(
            entry=self.published_entry,
            author=self.commenter,
            body="需要审核的评论",
            state=ContentState.PENDING,
        )
        self.entry_queue_url = reverse("admin:publishing_pendingentry_changelist")
        self.comment_queue_url = reverse("admin:publishing_pendingcomment_changelist")
        self.entry_review_url = reverse(
            "admin:publishing_pendingentry_review", args=[self.pending_entry.pk]
        )
        self.comment_review_url = reverse(
            "admin:publishing_pendingcomment_review", args=[self.pending_comment.pk]
        )
        self.client.force_login(self.reviewer)

    def test_chinese_pending_queues_only_show_pending_items_and_clear_review_actions(self):
        entry_response = self.client.get(self.entry_queue_url)
        comment_response = self.client.get(self.comment_queue_url)

        self.assertEqual(entry_response.status_code, 200)
        self.assertContains(entry_response, "待审内容")
        self.assertContains(entry_response, "需要审核的公开内容")
        self.assertContains(entry_response, self.entry_review_url)
        self.assertContains(entry_response, "立即审核")
        self.assertNotContains(entry_response, "已经公开的内容")
        self.assertContains(comment_response, "待审评论")
        self.assertContains(comment_response, "需要审核的评论")
        self.assertContains(comment_response, "已经公开的内容")
        self.assertContains(comment_response, self.comment_review_url)

    def test_single_review_page_shows_read_only_evidence_and_one_confirmed_decision_form(self):
        response = self.client.get(self.entry_review_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "只读审核对象")
        self.assertContains(response, "需要审核的公开内容")
        self.assertContains(response, "批准公开")
        self.assertContains(response, "驳回并隐藏")
        self.assertContains(response, "我已核对内容、作者和审核结论")
        self.assertContains(response, "提交后不能修改")
        self.assertContains(response, 'name="outcome"', count=2)
        self.assertContains(response, 'name="confirm"', count=1)

    def test_admin_approves_entry_through_service_and_removes_it_from_queue(self):
        response = self.client.post(
            self.entry_review_url,
            {
                "outcome": ContentReviewOutcome.APPROVE,
                "reason": "内容清楚且符合社区规则",
                "confirm": "on",
            },
        )

        self.assertRedirects(response, self.entry_queue_url)
        self.pending_entry.refresh_from_db()
        self.assertEqual(self.pending_entry.state, ContentState.PUBLISHED)
        self.assertEqual(ContentReviewDecision.objects.count(), 1)
        self.assertNotContains(self.client.get(self.entry_queue_url), "需要审核的公开内容")
        self.assertEqual(self.client.get(self.entry_review_url).status_code, 404)

    def test_admin_rejects_comment_and_missing_confirmation_does_not_write(self):
        invalid = self.client.post(
            self.comment_review_url,
            {
                "outcome": ContentReviewOutcome.REJECT,
                "reason": "先验证确认门禁",
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "提交前请确认")
        self.pending_comment.refresh_from_db()
        self.assertEqual(self.pending_comment.state, ContentState.PENDING)
        self.assertFalse(ContentReviewDecision.objects.exists())

        response = self.client.post(
            self.comment_review_url,
            {
                "outcome": ContentReviewOutcome.REJECT,
                "reason": "评论偏离原讨论且含推广信息",
                "confirm": "on",
            },
        )
        self.assertRedirects(response, self.comment_queue_url)
        self.pending_comment.refresh_from_db()
        self.assertEqual(self.pending_comment.state, ContentState.HIDDEN)

    def test_generic_admin_is_read_only_and_forged_state_post_is_forbidden(self):
        change_url = reverse("admin:publishing_entry_change", args=[self.pending_entry.pk])
        response = self.client.get(change_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.entry_review_url)
        self.assertNotContains(response, '<select name="state"')
        self.assertNotContains(response, 'name="_save"')

        forbidden = self.client.post(
            change_url,
            {"state": ContentState.PUBLISHED, "body": "forged"},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.pending_entry.refresh_from_db()
        self.assertEqual(self.pending_entry.state, ContentState.PENDING)

    def test_unprivileged_staff_cannot_open_or_submit_review_workflow(self):
        staff = User.objects.create_user(
            username="queue-unprivileged",
            password="queue-unprivileged-password",
            is_staff=True,
        )
        self.client.force_login(staff)

        self.assertEqual(self.client.get(self.entry_review_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.entry_review_url,
                {
                    "outcome": ContentReviewOutcome.APPROVE,
                    "reason": "not allowed",
                    "confirm": "on",
                },
            ).status_code,
            403,
        )
        self.pending_entry.refresh_from_db()
        self.assertEqual(self.pending_entry.state, ContentState.PENDING)
