from django.test import TestCase
from django.urls import reverse

from meppp.accounts.models import User
from meppp.notifications.models import Notification, NotificationKind


class ModerationNotificationPageTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="notification-member",
            password="notification-member-password",
        )
        self.client.force_login(self.member)

    def test_rejected_content_shows_reason_and_links_to_private_member_record(self):
        Notification.objects.create(
            recipient=self.member,
            kind=NotificationKind.MODERATION,
            payload={
                "content_type": "entry",
                "outcome": "reject",
                "reason": "包含无法核实的推广信息",
            },
        )

        response = self.client.get(reverse("web:notifications"))

        self.assertContains(response, "你的内容")
        self.assertContains(response, "未通过审核")
        self.assertContains(response, "审核说明：包含无法核实的推广信息")
        self.assertContains(response, reverse("web:member-dashboard"))

    def test_approved_comment_uses_specific_member_copy(self):
        Notification.objects.create(
            recipient=self.member,
            kind=NotificationKind.MODERATION,
            payload={
                "content_type": "comment",
                "outcome": "approve",
                "reason": "回应具体且符合规则",
            },
        )

        response = self.client.get(reverse("web:notifications"))

        self.assertContains(response, "你的评论")
        self.assertContains(response, "已通过审核")
        self.assertContains(response, "审核说明：回应具体且符合规则")
        self.assertContains(response, reverse("web:member-dashboard"))

    def test_report_action_explains_hidden_content_without_internal_report_details(self):
        Notification.objects.create(
            recipient=self.member,
            kind=NotificationKind.MODERATION,
            payload={
                "content_type": "entry",
                "outcome": "hidden",
            },
        )

        response = self.client.get(reverse("web:notifications"))

        self.assertContains(response, "你的内容")
        self.assertContains(response, "已因举报处置被隐藏")
        self.assertNotContains(response, "审核说明")
        self.assertNotContains(response, "举报人")
