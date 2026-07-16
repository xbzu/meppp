import sys
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django.views.debug import ExceptionReporter

from meppp.configuration.models import RegistrationMode, SiteConfiguration
from meppp.web.views import register

from .admin import InvitationAdmin
from .models import Invitation, User
from .services import issue_invitation, register_member

PASSWORD = "Sensitive-report-password-4821!"
INVITATION_TOKEN = "SensitiveInvitationToken_" + "Z" * 48


def current_exception_report(request=None) -> str:
    exc_type, exc_value, traceback = sys.exc_info()
    return ExceptionReporter(request, exc_type, exc_value, traceback).get_traceback_text()


@override_settings(DEBUG=False)
class SensitiveDataReportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="sensitive-owner",
            password="Sensitive-owner-password-4821!",
        )

    def test_registration_request_and_service_locals_redact_password_and_invitation(self):
        SiteConfiguration.objects.create(
            pk=1,
            registration_mode=RegistrationMode.INVITE,
        )
        request = RequestFactory().post(
            "/join/",
            {
                "username": "sensitive-member",
                "email": "member@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "invitation_token": INVITATION_TOKEN,
                "accept_rules": "on",
            },
        )

        with patch("meppp.web.views.enforce_rate_limit", side_effect=RuntimeError("stop")):
            try:
                register(request)
            except RuntimeError:
                request_report = current_exception_report(request)
            else:  # pragma: no cover - defensive test guard
                self.fail("registration failure was not raised")

        with patch.object(User.objects, "create_user", side_effect=RuntimeError("stop")):
            try:
                register_member(
                    username="sensitive-member",
                    email="member@example.test",
                    password=PASSWORD,
                    invitation_token=INVITATION_TOKEN,
                )
            except RuntimeError:
                service_report = current_exception_report()
            else:  # pragma: no cover - defensive test guard
                self.fail("service failure was not raised")

        for report in (request_report, service_report):
            self.assertNotIn(PASSWORD, report)
            self.assertNotIn(INVITATION_TOKEN, report)

    def test_issue_service_and_admin_response_redact_plaintext_token(self):
        with (
            patch(
                "meppp.accounts.services.secrets.token_urlsafe",
                return_value=INVITATION_TOKEN,
            ),
            patch.object(Invitation, "save", side_effect=RuntimeError("stop")),
        ):
            try:
                issue_invitation(
                    issuer=self.owner,
                    expires_at=timezone.now() + timedelta(days=1),
                )
            except RuntimeError:
                service_report = current_exception_report()
            else:  # pragma: no cover - defensive test guard
                self.fail("invitation issue failure was not raised")

        invitation = Invitation(
            issuer=self.owner,
            token_digest="a" * 64,
            hint="ZZZZZZZZ",
            expires_at=timezone.now() + timedelta(days=1),
        )
        request = RequestFactory().post(
            "/admin/accounts/invitation/add/",
            {
                "bound_email": "",
                "expires_at": timezone.localtime(timezone.now() + timedelta(days=1)).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
            },
        )
        request.user = self.owner
        model_admin = InvitationAdmin(Invitation, AdminSite())
        with (
            patch(
                "meppp.accounts.admin.issue_invitation",
                return_value=(invitation, INVITATION_TOKEN),
            ),
            patch("meppp.accounts.admin.TemplateResponse", side_effect=RuntimeError("stop")),
        ):
            try:
                model_admin.add_view(request)
            except RuntimeError:
                admin_report = current_exception_report(request)
            else:  # pragma: no cover - defensive test guard
                self.fail("admin response failure was not raised")

        self.assertNotIn(INVITATION_TOKEN, service_report)
        self.assertNotIn(INVITATION_TOKEN, admin_report)
