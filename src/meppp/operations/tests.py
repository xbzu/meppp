from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from meppp.configuration.models import ModerationMode, RegistrationMode, SiteConfiguration
from meppp.moderation.models import Report, ReportReason, ReportStatus, SubjectType
from meppp.publishing.models import Comment, ContentState, Entry

from .roles import ROLE_PERMISSIONS
from .selectors import get_operations_snapshot


def group_permission_specs(group: Group) -> set[tuple[str, str]]:
    return set(group.permissions.values_list("content_type__app_label", "codename"))


class BootstrapRolesCommandTests(TestCase):
    def test_command_is_idempotent_and_matches_the_code_manifest(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command("bootstrap_roles", stdout=first_output)
        call_command("bootstrap_roles", stdout=second_output)

        self.assertEqual(set(Group.objects.values_list("name", flat=True)), set(ROLE_PERMISSIONS))
        for role_name, expected_permissions in ROLE_PERMISSIONS.items():
            group = Group.objects.get(name=role_name)
            self.assertEqual(group_permission_specs(group), set(expected_permissions))
        self.assertIn("created", first_output.getvalue())
        self.assertIn("unchanged", second_output.getvalue())
        self.assertFalse(Group.objects.filter(name="Owner").exists())

    def test_command_removes_permission_drift_without_removing_members(self):
        call_command("bootstrap_roles", verbosity=0)
        operator_group = Group.objects.get(name="运营")
        forbidden_permission = Permission.objects.get(
            content_type__app_label="accounts",
            codename="view_user",
        )
        operator_group.permissions.add(forbidden_permission)
        member = get_user_model().objects.create_user(username="operator-member")
        operator_group.user_set.add(member)

        call_command("bootstrap_roles", verbosity=0)

        operator_group.refresh_from_db()
        self.assertEqual(group_permission_specs(operator_group), set(ROLE_PERMISSIONS["运营"]))
        self.assertTrue(operator_group.user_set.filter(pk=member.pk).exists())

    def test_role_manifest_never_grants_account_or_auth_management(self):
        granted_app_labels = {
            app_label
            for role_permissions in ROLE_PERMISSIONS.values()
            for app_label, _codename in role_permissions
        }
        self.assertTrue({"accounts", "auth"}.isdisjoint(granted_app_labels))


class OperationsDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_roles", verbosity=0)
        user_model = get_user_model()
        now = timezone.now()

        cls.operator = user_model.objects.create_user(
            username="operator",
            password="test-password-operator",
            is_staff=True,
        )
        cls.operator.groups.add(Group.objects.get(name="运营"))
        user_model.objects.filter(pk=cls.operator.pk).update(date_joined=now - timedelta(days=30))

        cls.author = user_model.objects.create_user(username="author")
        user_model.objects.filter(pk=cls.author.pk).update(date_joined=now - timedelta(days=30))

        cls.today_member = user_model.objects.create_user(username="today-member")
        cls.inactive_today_member = user_model.objects.create_user(
            username="inactive-today-member",
            is_active=False,
        )
        cls.recent_member = user_model.objects.create_user(username="recent-member")
        user_model.objects.filter(pk=cls.recent_member.pk).update(
            date_joined=now - timedelta(days=3)
        )
        cls.old_member = user_model.objects.create_user(username="old-member")
        user_model.objects.filter(pk=cls.old_member.pk).update(date_joined=now - timedelta(days=8))

        SiteConfiguration.objects.create(
            registration_mode=RegistrationMode.INVITE,
            moderation_mode=ModerationMode.PREMODERATION,
        )
        cls.pending_entry = Entry.objects.create(
            author=cls.author,
            body="pending entry",
            state=ContentState.PENDING,
        )
        cls.pending_comment = Comment.objects.create(
            entry=cls.pending_entry,
            author=cls.author,
            body="pending comment",
            state=ContentState.PENDING,
        )
        Report.objects.create(
            reporter=cls.author,
            subject_type=SubjectType.ENTRY,
            subject_public_id=cls.pending_entry.public_id,
            reason=ReportReason.SPAM,
        )
        Report.objects.create(
            reporter=cls.author,
            subject_type=SubjectType.COMMENT,
            subject_public_id=cls.pending_comment.public_id,
            reason=ReportReason.HARASSMENT,
            status=ReportStatus.TRIAGED,
            assigned_to=cls.operator,
        )

    def test_snapshot_uses_five_bounded_aggregate_queries(self):
        with self.assertNumQueries(5):
            snapshot = get_operations_snapshot(now=timezone.now())

        self.assertEqual(snapshot.pending_entries, 1)
        self.assertEqual(snapshot.pending_comments, 1)
        self.assertEqual(snapshot.open_reports, 1)
        self.assertEqual(snapshot.triaged_reports, 1)
        self.assertEqual(snapshot.active_members, 5)
        self.assertEqual(snapshot.members_joined_today, 2)
        self.assertEqual(snapshot.members_joined_seven_days, 3)
        self.assertEqual(snapshot.registration_mode, RegistrationMode.INVITE)
        self.assertEqual(snapshot.moderation_mode, ModerationMode.PREMODERATION)

    def test_operator_sees_dashboard_and_only_operator_quick_links(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("operations:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "运营总览")
        self.assertContains(response, "仅限邀请")
        self.assertContains(response, "发布前审核")
        link_labels = {link["label"] for link in response.context["quick_links"]}
        self.assertEqual(
            link_labels,
            {"待审内容", "待审评论", "站点配置", "话题管理"},
        )
        links_by_label = {link["label"]: link["url"] for link in response.context["quick_links"]}
        self.assertEqual(
            links_by_label["待审内容"],
            reverse("admin:publishing_pendingentry_changelist"),
        )
        self.assertEqual(
            links_by_label["待审评论"],
            reverse("admin:publishing_pendingcomment_changelist"),
        )

    def test_moderator_sees_report_links_but_not_configuration_or_members(self):
        moderator = get_user_model().objects.create_user(
            username="moderator",
            password="test-password-moderator",
            is_staff=True,
        )
        moderator.groups.add(Group.objects.get(name="审核"))
        self.client.force_login(moderator)

        response = self.client.get(reverse("operations:dashboard"))

        self.assertEqual(response.status_code, 200)
        link_labels = {link["label"] for link in response.context["quick_links"]}
        self.assertEqual(
            link_labels,
            {"待审内容", "待审评论", "待处理举报", "已分派举报"},
        )

    def test_staff_without_operations_permission_is_denied(self):
        staff_member = get_user_model().objects.create_user(
            username="unprivileged-staff",
            password="test-password-staff",
            is_staff=True,
        )
        self.client.force_login(staff_member)

        with self.assertLogs("django.request", level="WARNING"):
            response = self.client.get(reverse("operations:dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_non_staff_role_member_is_redirected_to_admin_login(self):
        member = get_user_model().objects.create_user(
            username="non-staff-operator",
            password="test-password-member",
        )
        member.groups.add(Group.objects.get(name="运营"))
        self.client.force_login(member)

        response = self.client.get(reverse("operations:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_superuser_gets_every_available_quick_link(self):
        owner = get_user_model().objects.create_superuser(
            username="owner",
            password="test-password-owner",
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("operations:dashboard"))

        self.assertEqual(response.status_code, 200)
        link_labels = {link["label"] for link in response.context["quick_links"]}
        self.assertTrue(
            {
                "待审内容",
                "待审评论",
                "待处理举报",
                "已分派举报",
                "站点配置",
                "注册邀请",
                "话题管理",
                "可用成员",
                "审计记录",
            }.issubset(link_labels)
        )

    def test_admin_header_only_shows_operations_link_to_authorized_staff(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, reverse("operations:dashboard"))

        staff_member = get_user_model().objects.create_user(
            username="header-unprivileged-staff",
            password="test-password-staff",
            is_staff=True,
        )
        self.client.force_login(staff_member)
        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, reverse("operations:dashboard"))
