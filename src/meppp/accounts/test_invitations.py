import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from meppp.audit.models import AuditEvent
from meppp.configuration.models import RegistrationMode, SiteConfiguration

from .models import Invitation, Profile, User
from .services import (
    INVITATION_UNAVAILABLE_MESSAGE,
    claim_invitation,
    issue_invitation,
    register_member,
    revoke_invitation,
)

PASSWORD = "Invitation-test-password-4821!"


class InvitationServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(username="owner", password=PASSWORD)

    def issue(self, *, bound_email=""):
        return issue_invitation(
            issuer=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
            bound_email=bound_email,
        )

    def invite_only(self):
        SiteConfiguration.objects.update_or_create(
            pk=1,
            defaults={"registration_mode": RegistrationMode.INVITE},
        )

    def test_issue_stores_only_a_high_entropy_digest_and_safe_audit_metadata(self):
        invitation, plaintext = self.issue(bound_email=" Member@Example.test ")

        self.assertGreaterEqual(len(plaintext), 43)
        self.assertEqual(invitation.token_digest, hashlib.sha256(plaintext.encode()).hexdigest())
        self.assertEqual(invitation.hint, plaintext[-8:])
        self.assertEqual(invitation.bound_email, "member@example.test")
        stored_evidence = repr(list(AuditEvent.objects.values("action", "reason", "metadata")))
        self.assertNotIn(plaintext, stored_evidence)
        self.assertNotIn(invitation.token_digest, stored_evidence)
        self.assertNotIn("member@example.test", stored_evidence)
        self.assertTrue(
            AuditEvent.objects.get(action="account.invitation.issued").metadata[
                "bound_email_restricted"
            ]
        )

    def test_invite_registration_claims_once_and_replay_rolls_back_the_second_user(self):
        self.invite_only()
        invitation, plaintext = self.issue()

        first = register_member(
            username="first",
            email="first@example.test",
            password=PASSWORD,
            invitation_token=plaintext,
        )
        with self.assertRaisesMessage(ValidationError, INVITATION_UNAVAILABLE_MESSAGE):
            register_member(
                username="second",
                email="second@example.test",
                password=PASSWORD,
                invitation_token=plaintext,
            )

        invitation.refresh_from_db()
        self.assertEqual(invitation.claimed_by, first)
        self.assertIsNotNone(invitation.claimed_at)
        self.assertFalse(User.objects.filter(username="second").exists())
        self.assertFalse(Profile.objects.filter(user__username="second").exists())

    def test_bound_email_is_case_insensitive_and_rejects_another_email(self):
        self.invite_only()
        accepted, accepted_token = self.issue(bound_email="Member@Example.test")
        rejected, rejected_token = self.issue(bound_email="other@example.test")

        member = register_member(
            username="member",
            email="MEMBER@example.test",
            password=PASSWORD,
            invitation_token=accepted_token,
        )
        with self.assertRaisesMessage(ValidationError, INVITATION_UNAVAILABLE_MESSAGE):
            register_member(
                username="wrong-email",
                email="different@example.test",
                password=PASSWORD,
                invitation_token=rejected_token,
            )

        accepted.refresh_from_db()
        rejected.refresh_from_db()
        self.assertEqual(accepted.claimed_by, member)
        self.assertIsNone(rejected.claimed_by)
        self.assertFalse(User.objects.filter(username="wrong-email").exists())

    def test_expired_and_revoked_invitations_cannot_be_claimed(self):
        self.invite_only()
        expired, expired_token = self.issue()
        Invitation.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        revoked, revoked_token = self.issue()
        revoke_invitation(invitation=revoked, actor=self.owner, reason="不再需要")

        for username, token in (("expired", expired_token), ("revoked", revoked_token)):
            with self.subTest(username=username):
                with self.assertRaisesMessage(ValidationError, INVITATION_UNAVAILABLE_MESSAGE):
                    register_member(
                        username=username,
                        email=f"{username}@example.test",
                        password=PASSWORD,
                        invitation_token=token,
                    )
        self.assertFalse(User.objects.filter(username__in=["expired", "revoked"]).exists())

    def test_closed_mode_rejects_a_valid_token_and_open_mode_does_not_consume_it(self):
        invitation, plaintext = self.issue()
        SiteConfiguration.objects.create(pk=1, registration_mode=RegistrationMode.CLOSED)

        with self.assertRaisesMessage(ValidationError, "未开放注册"):
            register_member(
                username="closed",
                email="",
                password=PASSWORD,
                invitation_token=plaintext,
            )
        SiteConfiguration.objects.filter(pk=1).update(registration_mode=RegistrationMode.OPEN)
        member = register_member(
            username="open-member",
            email="open-member@example.test",
            password=PASSWORD,
            invitation_token=plaintext,
        )

        invitation.refresh_from_db()
        self.assertIsNone(invitation.claimed_by)
        self.assertTrue(Profile.objects.filter(user=member).exists())

    def test_conditional_update_rejects_a_stale_concurrent_claim_snapshot(self):
        invitation, plaintext = self.issue()
        stale_snapshot = Invitation.objects.get(pk=invitation.pk)
        first = User.objects.create_user(username="first-claim")
        second = User.objects.create_user(username="second-claim")
        claim_invitation(invitation_token=plaintext, email="", claimed_by=first)

        with patch.object(Invitation.objects, "select_for_update") as select_for_update:
            select_for_update.return_value.filter.return_value.first.return_value = stale_snapshot
            with self.assertRaisesMessage(ValidationError, INVITATION_UNAVAILABLE_MESSAGE):
                claim_invitation(invitation_token=plaintext, email="", claimed_by=second)

        invitation.refresh_from_db()
        self.assertEqual(invitation.claimed_by, first)
        self.assertEqual(
            AuditEvent.objects.filter(action="account.invitation.claimed").count(),
            1,
        )

    def test_claimed_invitation_cannot_be_revoked_and_repeated_revoke_is_rejected(self):
        invitation, plaintext = self.issue()
        member = User.objects.create_user(username="claimed")
        claim_invitation(invitation_token=plaintext, email="", claimed_by=member)
        with self.assertRaisesMessage(ValidationError, "已经领取"):
            revoke_invitation(invitation=invitation, actor=self.owner, reason="错误撤销")

        unused, _ = self.issue()
        revoke_invitation(invitation=unused, actor=self.owner, reason="首次撤销")
        with self.assertRaisesMessage(ValidationError, "已经撤销"):
            revoke_invitation(invitation=unused, actor=self.owner, reason="重复撤销")


class InvitationAdminTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(username="owner", password=PASSWORD)
        self.client.force_login(self.owner)

    def test_admin_issues_plaintext_once_without_persisting_or_auditing_it(self):
        plaintext = "AdminOneTimeToken_" + "A" * 40
        expires_at = timezone.localtime(timezone.now() + timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        with patch("meppp.accounts.services.secrets.token_urlsafe", return_value=plaintext):
            response = self.client.post(
                reverse("admin:accounts_invitation_add"),
                {"bound_email": "Invitee@Example.test", "expires_at": expires_at},
            )

        invitation = Invitation.objects.get()
        self.assertContains(response, plaintext)
        self.assertContains(response, "复制邀请码")
        self.assertContains(response, "web/js/admin_invitation.js")
        self.assertContains(response, "web/css/admin_invitation.css")
        for directive in ("private", "no-store", "no-transform"):
            self.assertIn(directive, response.headers["Cache-Control"])
        self.assertNotEqual(invitation.token_digest, plaintext)
        self.assertEqual(invitation.bound_email, "invitee@example.test")

        detail = self.client.get(reverse("admin:accounts_invitation_change", args=[invitation.pk]))
        listing = self.client.get(reverse("admin:accounts_invitation_changelist"))
        self.assertNotContains(detail, plaintext)
        self.assertNotContains(detail, invitation.token_digest)
        self.assertNotContains(listing, plaintext)
        self.assertNotContains(listing, invitation.token_digest)
        audit_evidence = repr(list(AuditEvent.objects.values("action", "reason", "metadata")))
        self.assertNotIn(plaintext, audit_evidence)
        self.assertNotIn(invitation.token_digest, audit_evidence)

    def test_admin_revoke_workflow_requires_reason_and_audits_the_action(self):
        invitation, _ = issue_invitation(
            issuer=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )
        url = reverse("admin:accounts_invitation_revoke", args=[invitation.pk])

        invalid = self.client.post(url, {"reason": ""})
        valid = self.client.post(url, {"reason": "成员请求撤销"})

        self.assertContains(invalid, "撤销原因不能为空")
        self.assertRedirects(
            valid,
            reverse("admin:accounts_invitation_change", args=[invitation.pk]),
        )
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.revoked_at)
        event = AuditEvent.objects.get(action="account.invitation.revoked")
        self.assertEqual(event.reason, "成员请求撤销")
        self.assertNotIn(invitation.token_digest, repr(event.metadata))
