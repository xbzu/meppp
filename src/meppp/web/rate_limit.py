from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from ipaddress import ip_address

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class RateLimit:
    requests: int
    window_seconds: int


RATE_LIMITS = {
    "login": RateLimit(10, 300),
    "register": RateLimit(5, 3600),
    "publish": RateLimit(10, 300),
    "comment": RateLimit(20, 300),
    "reaction": RateLimit(60, 60),
    "report": RateLimit(5, 3600),
}


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("请求过于频繁")


def _private_key(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def _increment(key: str, *, timeout: int) -> int:
    if cache.add(key, 1, timeout=timeout):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)
        return 1


def client_ip(request) -> str | None:
    try:
        remote_address = ip_address(request.META.get("REMOTE_ADDR", "").strip())
    except ValueError:
        return None

    trusted_proxy = settings.TRUST_PROXY and any(
        remote_address in network for network in settings.TRUSTED_PROXY_NETWORKS
    )
    if not trusted_proxy:
        return remote_address.compressed

    forwarded_value = request.META.get("HTTP_X_REAL_IP", "").strip()
    if not forwarded_value or "," in forwarded_value:
        return None
    try:
        return ip_address(forwarded_value).compressed
    except ValueError:
        return None


def enforce_rate_limit(request, *, scope: str, identity: str | None = None) -> None:
    rule = RATE_LIMITS[scope]
    values = []
    remote_address = client_ip(request)
    if remote_address is not None:
        values.append(("ip", remote_address))
    if identity is not None:
        values.append(("identity", identity))

    exceeded = False
    for key_type, value in values:
        key = f"meppp:rate:{scope}:{key_type}:{_private_key(value)}"
        exceeded = _increment(key, timeout=rule.window_seconds) > rule.requests or exceeded
    if exceeded:
        raise RateLimitExceeded(rule.window_seconds)
