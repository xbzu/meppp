from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from meppp.accounts.models import Profile
from meppp.audit.models import AuditEvent
from meppp.publishing.models import Comment, ContentState, Entry

PASSWORD = "Member-Portal-Test-Password-2026!"
NEW_PASSWORD = "Member-Portal-New-Password-2026!"


class MemberPortalTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.member = user_model.objects.create_user(username="member", password=PASSWORD)
        self.other = user_model.objects.create_user(username="other", password=PASSWORD)
        Profile.objects.update_or_create(
            user=self.member,
            defaults={"display_name": "Member"},
        )
        Profile.objects.update_or_create(user=self.other, defaults={"display_name": "Other"})
        self.client.force_login(self.member)

    def test_dashboard_is_private_and_only_lists_the_members_records(self):
        own = Entry.objects.create(
            author=self.member,
            body="my pending note",
            state=ContentState.PENDING,
        )
        Entry.objects.create(author=self.other, body="someone else's note")
        parent = Entry.objects.create(author=self.other, body="public parent")
        comment = Comment.objects.create(
            entry=parent,
            author=self.member,
            body="my reply",
            state=ContentState.PENDING,
        )
        hidden_comment = Comment.objects.create(
            entry=parent,
            author=self.member,
            body="hidden reply",
            state=ContentState.HIDDEN,
        )

        response = self.client.get(reverse("web:member-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own.body)
        self.assertContains(response, comment.body)
        self.assertContains(response, hidden_comment.body)
        self.assertContains(response, "评论已由管理员隐藏，请到通知中查看审核说明")
        self.assertNotContains(response, "someone else's note")
        self.assertEqual(response.context["state_counts"][ContentState.PENDING], 2)
        self.assertEqual(response.context["state_counts"][ContentState.HIDDEN], 1)
        self.client.logout()
        response = self.client.get(reverse("web:member-dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.headers["Location"])

    def test_profile_settings_trim_values_and_write_metadata_only_audit(self):
        response = self.client.post(
            reverse("web:member-settings"),
            {"display_name": "  新名字  ", "bio": "  一段简介  "},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.member)
        self.assertEqual(profile.display_name, "新名字")
        self.assertEqual(profile.bio, "一段简介")
        event = AuditEvent.objects.get(action="account.profile.updated")
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.metadata["schema_version"], 1)
        self.assertEqual(event.metadata["changed_fields"], ["display_name", "bio"])
        self.assertNotIn("新名字", str(event.metadata))
        self.assertNotIn("一段简介", str(event.metadata))

    def test_settings_repairs_a_missing_profile(self):
        Profile.objects.filter(user=self.member).delete()

        response = self.client.get(reverse("web:member-settings"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Profile.objects.filter(user=self.member).exists())

    def test_password_change_keeps_session_and_is_audited_without_password_data(self):
        response = self.client.post(
            reverse("web:member-password"),
            {
                "old_password": PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "密码已更新")
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(NEW_PASSWORD))
        self.assertEqual(self.client.get(reverse("web:member-dashboard")).status_code, 200)
        event = AuditEvent.objects.get(action="account.password.changed")
        self.assertEqual(event.metadata, {"schema_version": 1})
        self.assertNotIn(PASSWORD, str(event.metadata))
        self.assertNotIn(NEW_PASSWORD, str(event.metadata))

    def test_author_can_soft_withdraw_published_entry_and_public_url_disappears(self):
        entry = Entry.objects.create(author=self.member, body="withdraw me")

        response = self.client.post(
            reverse("web:member-entry-withdraw", args=[entry.public_id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.state, ContentState.DELETED)
        self.assertEqual(
            self.client.get(reverse("web:entry-detail", args=[entry.public_id])).status_code,
            404,
        )
        event = AuditEvent.objects.get(action="entry.withdrawn")
        self.assertEqual(event.metadata["before"], ContentState.PUBLISHED)
        self.assertEqual(event.metadata["after"], ContentState.DELETED)

        self.client.post(reverse("web:member-entry-withdraw", args=[entry.public_id]))
        self.assertEqual(AuditEvent.objects.filter(action="entry.withdrawn").count(), 1)

    def test_author_can_withdraw_pending_comment(self):
        entry = Entry.objects.create(author=self.other, body="parent")
        comment = Comment.objects.create(
            entry=entry,
            author=self.member,
            body="pending reply",
            state=ContentState.PENDING,
        )

        response = self.client.post(
            reverse("web:member-comment-withdraw", args=[comment.public_id])
        )

        self.assertEqual(response.status_code, 302)
        comment.refresh_from_db()
        self.assertEqual(comment.state, ContentState.DELETED)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="comment.withdrawn",
                target_public_id=comment.public_id,
            ).exists()
        )

    def test_member_cannot_withdraw_another_members_or_moderator_hidden_content(self):
        other_entry = Entry.objects.create(author=self.other, body="not yours")
        hidden_entry = Entry.objects.create(
            author=self.member,
            body="moderator evidence",
            state=ContentState.HIDDEN,
        )

        response = self.client.post(
            reverse("web:member-entry-withdraw", args=[other_entry.public_id])
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse("web:member-entry-withdraw", args=[hidden_entry.public_id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当前不能由作者撤回")
        hidden_entry.refresh_from_db()
        self.assertEqual(hidden_entry.state, ContentState.HIDDEN)

    def test_withdraw_endpoints_reject_get(self):
        entry = Entry.objects.create(author=self.member, body="post only")
        comment = Comment.objects.create(entry=entry, author=self.member, body="post only")

        self.assertEqual(
            self.client.get(
                reverse("web:member-entry-withdraw", args=[entry.public_id])
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                reverse("web:member-comment-withdraw", args=[comment.public_id])
            ).status_code,
            405,
        )
