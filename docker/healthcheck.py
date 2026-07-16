from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def valid_host_header(host: str) -> bool:
    return bool(host) and not any(
        character.isspace() or character in {"/", "\\", ","} for character in host
    )


def check_ready(url: str, host: str, timeout: float = 3.0) -> bool:
    if not valid_host_header(host):
        return False
    request = Request(
        url,
        headers={
            "Host": host,
            "User-Agent": "meppp-container-healthcheck/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                return False
            payload = json.load(response)
    except HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError:
        return False
    return payload == {"status": "ready"}


def main() -> int:
    url = os.getenv("MEPPP_HEALTHCHECK_URL", "http://127.0.0.1:8000/health/ready")
    host = os.getenv("MEPPP_HEALTHCHECK_HOST", "meppp.com").strip()
    if check_ready(url, host):
        return 0
    print(f"MEPPP readiness check failed: url={url} host={host}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
