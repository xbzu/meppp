from __future__ import annotations

from ipaddress import ip_address

from django.conf import settings

INTERNAL_PROXY_PROTO_HEADER = "HTTP_X_MEPPP_PROXY_PROTO"


class TrustedProxyHeadersMiddleware:
    """Accept proxy headers only from the explicitly configured immediate proxy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.META.pop(INTERNAL_PROXY_PROTO_HEADER, None)

        try:
            remote_address = ip_address(request.META.get("REMOTE_ADDR", "").strip())
        except ValueError:
            trusted_proxy = False
        else:
            trusted_proxy = settings.TRUST_PROXY and any(
                remote_address in network for network in settings.TRUSTED_PROXY_NETWORKS
            )

        if not trusted_proxy:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
            request.META.pop("HTTP_X_REAL_IP", None)
            return self.get_response(request)

        forwarded_proto = request.META.get("HTTP_X_FORWARDED_PROTO", "").strip().lower()
        real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
        canonical_real_ip = None
        if real_ip and "," not in real_ip:
            try:
                canonical_real_ip = ip_address(real_ip).compressed
            except ValueError:
                pass

        if forwarded_proto not in {"http", "https"} or canonical_real_ip is None:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
            request.META.pop("HTTP_X_REAL_IP", None)
            return self.get_response(request)

        request.META["HTTP_X_FORWARDED_PROTO"] = forwarded_proto
        request.META["HTTP_X_REAL_IP"] = canonical_real_ip
        request.META[INTERNAL_PROXY_PROTO_HEADER] = forwarded_proto

        return self.get_response(request)
