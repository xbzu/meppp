from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse


class HealthViewTests(TestCase):
    def test_live_reports_version_without_database_details(self):
        response = self.client.get(reverse("health:live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "meppp")
        self.assertEqual(response.json()["status"], "ok")

    def test_ready_checks_database(self):
        response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    @patch("meppp.health.views.connection.cursor", side_effect=DatabaseError)
    def test_ready_returns_503_when_database_is_unavailable(self, _cursor):
        with self.assertLogs("django.request", level="ERROR"):
            response = self.client.get(reverse("health:ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
