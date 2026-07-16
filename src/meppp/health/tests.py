from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthViewTests(TestCase):
    def test_live_reports_only_minimal_status(self):
        response = self.client.get(reverse("health:live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_ready_checks_database(self):
        response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_ready_is_a_real_http_probe_even_when_https_redirects_are_enabled(self):
        response = self.client.get(reverse("health:ready"), REMOTE_ADDR="127.0.0.1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    @patch("meppp.health.views.connection.cursor")
    def test_ready_returns_503_when_database_does_not_confirm_query(self, cursor_factory):
        cursor_factory.return_value.__enter__.return_value.fetchone.return_value = None

        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    @patch(
        "meppp.health.views.connection.cursor",
        side_effect=DatabaseError("sensitive database failure"),
    )
    def test_ready_returns_503_when_database_is_unavailable(self, _cursor):
        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "sensitive database failure", status_code=503)

    @patch("meppp.health.views.os.access", return_value=False)
    def test_ready_returns_503_when_media_directory_is_not_writable(self, _access):
        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    @patch(
        "meppp.health.views.shutil.disk_usage",
        return_value=SimpleNamespace(free=0),
    )
    def test_ready_returns_503_when_media_free_space_is_below_floor(self, _disk_usage):
        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
