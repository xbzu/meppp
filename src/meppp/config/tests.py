import json
import os
import subprocess
import sys
from ipaddress import ip_network
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from meppp.accounts.models import User
from meppp.config.middleware import INTERNAL_PROXY_PROTO_HEADER, TrustedProxyHeadersMiddleware
from meppp.config.settings import env_nonnegative_int

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class SettingsEnvironmentTests(SimpleTestCase):
    active_secret = "active-production-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
    fallback_secret = "fallback-production-test-secret-9876543210-zyxwvutsrqponmlkjihgfedcba"
    settings_probe = """
import json
from meppp.config import settings

print(json.dumps({
    "csrf_trusted_origins": settings.CSRF_TRUSTED_ORIGINS,
    "django_log_level": settings.LOGGING["loggers"]["django"]["level"],
    "hsts_include_subdomains": settings.SECURE_HSTS_INCLUDE_SUBDOMAINS,
    "hsts_preload": settings.SECURE_HSTS_PRELOAD,
    "hsts_seconds": settings.SECURE_HSTS_SECONDS,
    "middleware": settings.MIDDLEWARE,
    "root_log_level": settings.LOGGING["root"]["level"],
    "secret_key_fallbacks": settings.SECRET_KEY_FALLBACKS,
    "silenced_system_checks": settings.SILENCED_SYSTEM_CHECKS,
}))
"""

    def run_production_settings(self, **overrides):
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("MEPPP_")
        }
        with TemporaryDirectory() as data_dir:
            environment.update(
                {
                    "MEPPP_ALLOWED_HOSTS": "meppp.com",
                    "MEPPP_DATA_DIR": data_dir,
                    "MEPPP_ENV": "production",
                    "MEPPP_SECRET_KEY": self.active_secret,
                }
            )
            environment.update(overrides)
            return subprocess.run(
                [sys.executable, "-c", self.settings_probe],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )

    def read_settings(self, **overrides):
        result = self.run_production_settings(**overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_production_starts_with_short_hsts_rollout(self):
        settings_values = self.read_settings()

        self.assertEqual(settings_values["hsts_seconds"], 3_600)
        self.assertIs(settings_values["hsts_include_subdomains"], False)
        self.assertIs(settings_values["hsts_preload"], False)
        self.assertEqual(
            settings_values["silenced_system_checks"],
            ["security.W005", "security.W021"],
        )

    def test_hsts_rollout_can_be_explicitly_extended(self):
        settings_values = self.read_settings(
            MEPPP_SECURE_HSTS_SECONDS="31536000",
            MEPPP_SECURE_HSTS_INCLUDE_SUBDOMAINS="true",
            MEPPP_SECURE_HSTS_PRELOAD="true",
        )

        self.assertEqual(settings_values["hsts_seconds"], 31_536_000)
        self.assertIs(settings_values["hsts_include_subdomains"], True)
        self.assertIs(settings_values["hsts_preload"], True)
        self.assertEqual(settings_values["silenced_system_checks"], [])

    def test_invalid_hsts_combinations_fail_fast(self):
        invalid_combinations = (
            {
                "MEPPP_SECURE_HSTS_SECONDS": "0",
                "MEPPP_SECURE_HSTS_INCLUDE_SUBDOMAINS": "true",
            },
            {
                "MEPPP_SECURE_HSTS_SECONDS": "31536000",
                "MEPPP_SECURE_HSTS_PRELOAD": "true",
            },
            {
                "MEPPP_SECURE_HSTS_SECONDS": "86400",
                "MEPPP_SECURE_HSTS_INCLUDE_SUBDOMAINS": "true",
                "MEPPP_SECURE_HSTS_PRELOAD": "true",
            },
        )
        for overrides in invalid_combinations:
            with self.subTest(overrides=overrides):
                result = self.run_production_settings(**overrides)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MEPPP_SECURE_HSTS", result.stderr)

    def test_hsts_seconds_rejects_values_that_are_not_ascii_non_negative_integers(self):
        for invalid_value in ("", "-1", "+1", "1.5", "１"):
            with self.subTest(invalid_value=invalid_value):
                result = self.run_production_settings(
                    MEPPP_SECURE_HSTS_SECONDS=invalid_value,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be a non-negative integer", result.stderr)

    def test_csrf_origins_and_log_level_are_loaded_from_production_environment(self):
        settings_values = self.read_settings(
            MEPPP_CSRF_TRUSTED_ORIGINS="https://meppp.com, https://www.meppp.com",
            MEPPP_LOG_LEVEL="warning",
        )

        self.assertEqual(
            settings_values["csrf_trusted_origins"],
            ["https://meppp.com", "https://www.meppp.com"],
        )
        self.assertEqual(settings_values["root_log_level"], "WARNING")
        self.assertEqual(settings_values["django_log_level"], "WARNING")

    def test_invalid_log_level_fails_fast(self):
        result = self.run_production_settings(MEPPP_LOG_LEVEL="verbose")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MEPPP_LOG_LEVEL must be a valid logging level", result.stderr)

    def test_production_rejects_unknown_environment_and_debug_mode(self):
        invalid_configurations = (
            ({"MEPPP_ENV": "prodution"}, "MEPPP_ENV"),
            ({"MEPPP_DEBUG": "true"}, "MEPPP_DEBUG"),
            ({"MEPPP_SECURE": "false"}, "MEPPP_SECURE"),
        )
        for overrides, expected_error in invalid_configurations:
            with self.subTest(overrides=overrides):
                result = self.run_production_settings(**overrides)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)

    def test_production_proxy_trust_requires_exact_ip_addresses(self):
        valid = self.run_production_settings(
            MEPPP_TRUST_PROXY="true",
            MEPPP_TRUSTED_PROXY_IPS="172.30.89.1/32,2001:db8::1/128",
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        for networks in ("0.0.0.0/0", "172.30.89.0/28", "2001:db8::/64"):
            with self.subTest(networks=networks):
                result = self.run_production_settings(
                    MEPPP_TRUST_PROXY="true",
                    MEPPP_TRUSTED_PROXY_IPS=networks,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exact proxy IP addresses", result.stderr)

    def test_production_accepts_a_distinct_strong_secret_fallback(self):
        fallback = self.fallback_secret
        settings_values = self.read_settings(MEPPP_SECRET_KEY_FALLBACKS=fallback)

        self.assertEqual(settings_values["secret_key_fallbacks"], [fallback])

    def test_production_rejects_unsafe_secret_fallbacks(self):
        unsafe_fallbacks = (
            "short",
            "replace-" + ("x" * 64),
            "f" * 64,
            self.active_secret,
            f"{self.fallback_secret},{self.fallback_secret}",
        )
        for fallback in unsafe_fallbacks:
            with self.subTest(fallback=fallback):
                result = self.run_production_settings(MEPPP_SECRET_KEY_FALLBACKS=fallback)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MEPPP_SECRET_KEY_FALLBACKS", result.stderr)

    def test_production_rejects_missing_wildcard_or_malformed_allowed_hosts(self):
        for hosts in ("", "*", ".meppp.com", "https://meppp.com", "meppp.com:443"):
            with self.subTest(hosts=hosts):
                result = self.run_production_settings(MEPPP_ALLOWED_HOSTS=hosts)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MEPPP_ALLOWED_HOSTS", result.stderr)

    def test_production_rejects_inexact_or_insecure_csrf_origins(self):
        invalid_origins = (
            "http://meppp.com",
            "https://*.meppp.com",
            "https://meppp.com/path",
            "https://user@meppp.com",
            "https://meppp.com:invalid",
        )
        for origins in invalid_origins:
            with self.subTest(origins=origins):
                result = self.run_production_settings(MEPPP_CSRF_TRUSTED_ORIGINS=origins)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MEPPP_CSRF_TRUSTED_ORIGINS", result.stderr)

    def test_proxy_header_sanitizer_runs_before_django_security_middleware(self):
        middleware = self.read_settings()["middleware"]

        self.assertLess(
            middleware.index("meppp.config.middleware.TrustedProxyHeadersMiddleware"),
            middleware.index("django.middleware.security.SecurityMiddleware"),
        )
        self.assertLess(
            middleware.index("django.middleware.security.SecurityMiddleware"),
            middleware.index("whitenoise.middleware.WhiteNoiseMiddleware"),
        )

    @patch.dict(os.environ, {"MEPPP_TEST_INTEGER": " 0 "})
    def test_non_negative_integer_parser_accepts_zero(self):
        self.assertEqual(env_nonnegative_int("MEPPP_TEST_INTEGER", 10), 0)


@override_settings(
    TRUST_PROXY=True,
    TRUSTED_PROXY_NETWORKS=(ip_network("10.0.0.5/32"),),
    SECURE_PROXY_SSL_HEADER=(INTERNAL_PROXY_PROTO_HEADER, "https"),
)
class TrustedProxyHeaderTests(SimpleTestCase):
    def capture_meta(self, **request_headers):
        captured_meta = {}

        def get_response(request):
            captured_meta.update(request.META)
            return HttpResponse()

        request = RequestFactory().get("/", **request_headers)
        TrustedProxyHeadersMiddleware(get_response)(request)
        return captured_meta

    def test_untrusted_remote_cannot_supply_any_proxy_header(self):
        request_meta = self.capture_meta(
            REMOTE_ADDR="203.0.113.8",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_REAL_IP="198.51.100.10",
            HTTP_X_MEPPP_PROXY_PROTO="https",
        )

        self.assertNotIn("HTTP_X_FORWARDED_PROTO", request_meta)
        self.assertNotIn("HTTP_X_REAL_IP", request_meta)
        self.assertNotIn(INTERNAL_PROXY_PROTO_HEADER, request_meta)

    def test_trusted_remote_receives_normalized_single_value_headers(self):
        request_meta = self.capture_meta(
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_PROTO=" HTTPS ",
            HTTP_X_REAL_IP="2001:0db8:0000:0000:0000:0000:0000:0001",
        )

        self.assertEqual(request_meta["HTTP_X_FORWARDED_PROTO"], "https")
        self.assertEqual(request_meta["HTTP_X_REAL_IP"], "2001:db8::1")
        self.assertEqual(request_meta[INTERNAL_PROXY_PROTO_HEADER], "https")

    def test_trusted_remote_cannot_supply_chained_or_invalid_values(self):
        request_meta = self.capture_meta(
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_PROTO="https,http",
            HTTP_X_REAL_IP="198.51.100.10, 198.51.100.11",
            HTTP_X_MEPPP_PROXY_PROTO="https",
        )

        self.assertNotIn("HTTP_X_FORWARDED_PROTO", request_meta)
        self.assertNotIn("HTTP_X_REAL_IP", request_meta)
        self.assertNotIn(INTERNAL_PROXY_PROTO_HEADER, request_meta)

    def test_trusted_remote_requires_a_complete_valid_header_pair(self):
        invalid_pairs = (
            {
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            {
                "HTTP_X_FORWARDED_PROTO": "ftp",
                "HTTP_X_REAL_IP": "198.51.100.10",
            },
            {
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_X_REAL_IP": "not-an-ip",
            },
        )
        for request_headers in invalid_pairs:
            with self.subTest(request_headers=request_headers):
                request_meta = self.capture_meta(REMOTE_ADDR="10.0.0.5", **request_headers)

                self.assertNotIn("HTTP_X_FORWARDED_PROTO", request_meta)
                self.assertNotIn("HTTP_X_REAL_IP", request_meta)
                self.assertNotIn(INTERNAL_PROXY_PROTO_HEADER, request_meta)


@override_settings(
    SECURE_SSL_REDIRECT=True,
    TRUST_PROXY=True,
    TRUSTED_PROXY_NETWORKS=(ip_network("10.0.0.5/32"),),
    SECURE_PROXY_SSL_HEADER=(INTERNAL_PROXY_PROTO_HEADER, "https"),
)
class ProxyHttpsBoundaryTests(TestCase):
    def test_untrusted_forwarded_proto_cannot_bypass_https_redirect(self):
        response = self.client.get(
            "/admin/",
            REMOTE_ADDR="203.0.113.8",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_REAL_IP="198.51.100.10",
            HTTP_X_MEPPP_PROXY_PROTO="https",
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://testserver/admin/")

    def test_exact_trusted_proxy_can_report_https(self):
        response = self.client.get(
            "/admin/",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_REAL_IP="198.51.100.10",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/login/?next=/admin/")

    def test_trusted_proxy_with_chained_proto_is_redirected(self):
        response = self.client.get(
            "/admin/",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_PROTO="https,http",
            HTTP_X_REAL_IP="198.51.100.10",
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://testserver/admin/")

    def test_trusted_proxy_missing_real_ip_is_redirected(self):
        response = self.client.get(
            "/admin/",
            REMOTE_ADDR="10.0.0.5",
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://testserver/admin/")


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
