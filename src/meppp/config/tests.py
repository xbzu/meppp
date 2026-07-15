from django.test import TestCase

from meppp.accounts.models import User


class AdminSmokeTests(TestCase):
    def test_superuser_can_open_admin(self):
        User.objects.create_superuser(
            username="owner",
            email="owner@example.com",
            password="a-strong-test-password",
        )
        self.client.login(username="owner", password="a-strong-test-password")

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MEPPP")
