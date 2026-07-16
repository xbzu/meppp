from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase

from meppp.configuration.models import RegistrationMode, SiteConfiguration

from .admin import MepppUserAdmin
from .models import Profile, User
from .services import register_member


class UserModelTests(TestCase):
    def test_email_is_normalized_and_public_id_is_stable(self):
        user = User.objects.create_user(
            username="member",
            email="  Member@Example.COM ",
            password="a-long-test-password",
        )

        user.refresh_from_db()
        self.assertEqual(user.email, "member@example.com")
        self.assertIsNotNone(user.public_id)
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_nonblank_email_is_case_insensitively_unique(self):
        User.objects.create_user(username="first", email="same@example.com")

        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="second", email="SAME@example.com")

    def test_multiple_blank_emails_are_allowed(self):
        User.objects.create_user(username="first")
        User.objects.create_user(username="second")

        self.assertEqual(User.objects.filter(email="").count(), 2)

    def test_username_is_case_insensitively_unique(self):
        User.objects.create_user(username="Alice")

        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="alice")

    def test_username_authentication_is_case_insensitive(self):
        User.objects.create_user(username="QuietMember", password="a-long-test-password")

        authenticated = self.client.login(
            username="quietmember",
            password="a-long-test-password",
        )

        self.assertTrue(authenticated)

    def test_users_are_deactivated_instead_of_deleted(self):
        user = User.objects.create_user(username="member")

        with self.assertRaisesMessage(ValidationError, "deactivated"):
            user.delete()
        with self.assertRaisesMessage(ValidationError, "deactivated"):
            User.objects.filter(pk=user.pk).delete()
        with self.assertRaisesMessage(ValidationError, "deactivated"):
            User._base_manager.filter(pk=user.pk).delete()

    def test_only_superusers_can_change_users_in_admin(self):
        model_admin = MepppUserAdmin(User, AdminSite())
        request = RequestFactory().get("/admin/accounts/user/")
        request.user = User.objects.create_user(username="staff", is_staff=True)

        self.assertFalse(model_admin.has_change_permission(request))

        request.user = User.objects.create_superuser(username="owner", password="owner-password")
        self.assertTrue(model_admin.has_change_permission(request))

    def test_profile_uses_username_when_display_name_is_empty(self):
        user = User.objects.create_user(username="member")
        profile = user.profile

        self.assertEqual(str(profile), "member")

    def test_superuser_creation_also_provisions_a_profile(self):
        owner = User.objects.create_superuser(username="owner-with-profile")

        self.assertTrue(Profile.objects.filter(user=owner).exists())


class RegistrationServiceTests(TestCase):
    def test_closed_mode_is_enforced_in_the_service(self):
        configuration = SiteConfiguration.objects.create(
            pk=1,
            registration_mode=RegistrationMode.CLOSED,
        )

        configuration.registration_mode = RegistrationMode.CLOSED
        configuration.save()
        with self.assertRaisesMessage(ValidationError, "未开放注册"):
            register_member(
                username="member-closed",
                email="",
                password="a-long-test-password",
            )

        self.assertFalse(User.objects.exists())

    def test_open_mode_creates_user_and_profile_atomically(self):
        SiteConfiguration.objects.create(pk=1, registration_mode=RegistrationMode.OPEN)

        user = register_member(
            username="member",
            email="member@example.test",
            password="a-long-test-password",
        )

        self.assertTrue(Profile.objects.filter(user=user).exists())
