from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "docker" / "healthcheck.py"
SPEC = importlib.util.spec_from_file_location("container_healthcheck", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load container healthcheck module")
healthcheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(healthcheck)


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ContainerHealthcheckTests(unittest.TestCase):
    def test_healthcheck_sends_the_configured_allowed_host(self):
        with patch.object(
            healthcheck,
            "urlopen",
            return_value=FakeResponse(b'{"status":"ready"}'),
        ) as opener:
            self.assertTrue(
                healthcheck.check_ready("http://127.0.0.1:8000/health/ready", "meppp.com")
            )

        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Host"), "meppp.com")

    def test_healthcheck_rejects_an_invalid_host_without_a_request(self):
        with patch.object(healthcheck, "urlopen") as opener:
            self.assertFalse(
                healthcheck.check_ready("http://127.0.0.1:8000/health/ready", "meppp.com/invalid")
            )

        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
