import sys
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.views.debug import ExceptionReporter

from meppp.accounts.models import RecoveryCredential, User
from meppp.accounts.services import issue_recovery_code
from meppp.configuration.models import RegistrationMode, SiteConfiguration

from .rate_limit import RATE_LIMITS, RateLimit
from .views import (
    RECOVERY_ISSUED_SESSION_KEY,
    RECOVERY_NOTICE_CACHE,
    RECOVERY_NOTICE_SESSION_KEY,
    _recovery_cache_key,
    _stage_recovery_code,
    account_recovery,
    recovery_code_notice,
    recovery_code_rotate,
    register,
)

PASSWORD = "Valid-community-password-4821!"
NEW_PASSWORD = "Another-valid-community-password-9582!"


class AccountRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        RECOVERY_NOTICE_CACHE.clear()
        SiteConfiguration.objects.create(pk=1, registration_mode=RegistrationMode.OPEN)

    def test_registration_requires_email_and_creates_hashed_recovery_code(self):
        missing_email = self.client.post(
            reverse("web:register"),
            {
                "username": "missing-email",
                "email": "",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "accept_rules": "on",
            },
        )
        self.assertEqual(missing_email.status_code, 200)
        self.assertFalse(User.objects.filter(username="missing-email").exists())

        response = self.client.post(
            reverse("web:register"),
            {
                "username": "member",
                "email": "member@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "accept_rules": "on",
                "website": "",
            },
        )

        self.assertRedirects(response, reverse("web:recovery-code"))
        user = User.objects.get(username="member")
        reference = self.client.session[RECOVERY_NOTICE_SESSION_KEY]
        plaintext = RECOVERY_NOTICE_CACHE.get(_recovery_cache_key(reference))
        credential = RecoveryCredential.objects.get(user=user)
        self.assertNotEqual(credential.token_digest, plaintext)
        self.assertTrue(check_password(plaintext, credential.token_digest))

    def test_recovery_plaintext_uses_a_separate_bounded_cache(self):
        RECOVERY_NOTICE_CACHE.set("recovery-proof", "one-time-code", timeout=60)
        cache.set("rate-proof", 1, timeout=60)

        cache.clear()

        self.assertEqual(RECOVERY_NOTICE_CACHE.get("recovery-proof"), "one-time-code")

    def test_every_plaintext_recovery_frame_is_marked_sensitive(self):
        expectations = {
            _stage_recovery_code: (
                ("recovery_code",),
                lambda: _stage_recovery_code(None, recovery_code="secret", next_url="/"),
            ),
            register: (("recovery_code",), lambda: register(None)),
            recovery_code_notice: (("recovery_code",), lambda: recovery_code_notice(None)),
            recovery_code_rotate: (("recovery_code",), lambda: recovery_code_rotate(None)),
            account_recovery: (("replacement_code",), lambda: account_recovery(None)),
        }

        for function, (variables, invoke) in expectations.items():
            with self.subTest(function=function.__name__):
                with self.assertRaises((AttributeError, TypeError)):
                    invoke()
                self.assertEqual(function.sensitive_variables, variables)

    def test_exception_reporter_does_not_include_a_plaintext_recovery_code(self):
        plaintext = "recovery-code-that-must-never-appear"
        try:
            _stage_recovery_code(None, recovery_code=plaintext, next_url="/")
        except AttributeError:
            report = ExceptionReporter(None, *sys.exc_info()).get_traceback_text()
        else:
            self.fail("the controlled invalid request did not raise")

        self.assertNotIn(plaintext, report)
        self.assertIn("********************", report)

    def test_registration_honeypot_does_not_create_an_account(self):
        response = self.client.post(
            reverse("web:register"),
            {
                "username": "bot",
                "email": "bot@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "accept_rules": "on",
                "website": "https://spam.example/",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="bot").exists())

    def test_recovery_resets_password_and_rotates_the_code(self):
        user = User.objects.create_user(
            username="member",
            email="member@example.test",
            password=PASSWORD,
        )
        old_code = issue_recovery_code(user=user)

        response = self.client.post(
            reverse("web:account-recovery"),
            {
                "username": "MEMBER",
                "email": " Member@Example.Test ",
                "recovery_code": old_code,
                "password1": NEW_PASSWORD,
                "password2": NEW_PASSWORD,
            },
        )

        self.assertRedirects(response, reverse("web:recovery-code"))
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        reference = self.client.session[RECOVERY_NOTICE_SESSION_KEY]
        replacement = RECOVERY_NOTICE_CACHE.get(_recovery_cache_key(reference))
        self.assertNotEqual(replacement, old_code)
        credential = RecoveryCredential.objects.get(user=user)
        self.assertFalse(check_password(old_code, credential.token_digest))
        self.assertTrue(check_password(replacement, credential.token_digest))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_invalid_recovery_is_generic_and_does_not_change_password(self):
        user = User.objects.create_user(
            username="member",
            email="member@example.test",
            password=PASSWORD,
        )
        issue_recovery_code(user=user)

        response = self.client.post(
            reverse("web:account-recovery"),
            {
                "username": "unknown",
                "email": "unknown@example.test",
                "recovery_code": "incorrect",
                "password1": NEW_PASSWORD,
                "password2": NEW_PASSWORD,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "账号信息或恢复码无效")
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_endpoint_has_a_separate_rate_limit(self):
        payload = {
            "username": "unknown",
            "email": "unknown@example.test",
            "recovery_code": "incorrect",
            "password1": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        }
        with patch.dict(RATE_LIMITS, {"recover": RateLimit(1, 60)}):
            self.client.post(reverse("web:account-recovery"), payload)
            response = self.client.post(reverse("web:account-recovery"), payload)

        self.assertEqual(response.status_code, 429)

    def test_another_ip_cannot_globally_lock_account_recovery(self):
        user = User.objects.create_user(
            username="cross-ip-member",
            email="cross-ip@example.test",
            password=PASSWORD,
        )
        recovery_code = issue_recovery_code(user=user)
        payload = {
            "username": user.username,
            "email": user.email,
            "recovery_code": "incorrect",
            "password1": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        }

        with patch.dict(RATE_LIMITS, {"recover": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:account-recovery"),
                payload,
                REMOTE_ADDR="198.51.100.30",
            )
            payload["recovery_code"] = recovery_code
            response = self.client.post(
                reverse("web:account-recovery"),
                payload,
                REMOTE_ADDR="198.51.100.31",
            )

        self.assertRedirects(response, reverse("web:recovery-code"))
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))

    def test_logged_in_member_can_rotate_code_only_with_current_password(self):
        user = User.objects.create_user(
            username="member",
            email="member@example.test",
            password=PASSWORD,
        )
        old_code = issue_recovery_code(user=user)
        self.client.force_login(user)

        rejected = self.client.post(
            reverse("web:recovery-code-rotate"),
            {"current_password": "wrong"},
        )
        self.assertRedirects(rejected, reverse("web:member-password"))
        credential = RecoveryCredential.objects.get(user=user)
        self.assertTrue(check_password(old_code, credential.token_digest))

        accepted = self.client.post(
            reverse("web:recovery-code-rotate"),
            {"current_password": PASSWORD},
        )
        self.assertRedirects(accepted, reverse("web:recovery-code"))
        credential.refresh_from_db()
        self.assertFalse(check_password(old_code, credential.token_digest))

    def test_recovery_code_rotation_has_a_separate_security_rate_limit(self):
        user = User.objects.create_user(
            username="rate-limited-rotation",
            email="rotation@example.test",
            password=PASSWORD,
        )
        issue_recovery_code(user=user)
        self.client.force_login(user)

        with patch.dict(RATE_LIMITS, {"account_security": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:recovery-code-rotate"),
                {"current_password": "wrong"},
            )
            response = self.client.post(
                reverse("web:recovery-code-rotate"),
                {"current_password": PASSWORD},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")

    def test_password_change_shares_the_security_rate_limit(self):
        user = User.objects.create_user(
            username="rate-limited-password",
            email="password@example.test",
            password=PASSWORD,
        )
        self.client.force_login(user)

        with patch.dict(RATE_LIMITS, {"account_security": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:member-password"),
                {
                    "old_password": "wrong",
                    "new_password1": NEW_PASSWORD,
                    "new_password2": NEW_PASSWORD,
                },
            )
            response = self.client.post(
                reverse("web:member-password"),
                {
                    "old_password": PASSWORD,
                    "new_password1": NEW_PASSWORD,
                    "new_password2": NEW_PASSWORD,
                },
            )

        self.assertEqual(response.status_code, 429)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_notice_requires_login_and_confirmation_removes_plaintext(self):
        anonymous = self.client.get(reverse("web:recovery-code"))
        self.assertEqual(anonymous.status_code, 302)

        user = User.objects.create_user(
            username="member",
            email="member@example.test",
            password=PASSWORD,
        )
        self.client.force_login(user)
        session = self.client.session
        reference = "temporary-reference"
        RECOVERY_NOTICE_CACHE.set(
            _recovery_cache_key(reference),
            "temporary-code",
            timeout=900,
        )
        session[RECOVERY_NOTICE_SESSION_KEY] = reference
        session[RECOVERY_ISSUED_SESSION_KEY] = int(timezone.now().timestamp())
        session.save()

        shown = self.client.get(reverse("web:recovery-code"))
        self.assertContains(shown, "temporary-code")
        confirmed = self.client.post(reverse("web:recovery-code"))
        self.assertRedirects(confirmed, reverse("web:home"))
        self.assertNotIn(RECOVERY_NOTICE_SESSION_KEY, self.client.session)
        self.assertIsNone(RECOVERY_NOTICE_CACHE.get(_recovery_cache_key(reference)))
