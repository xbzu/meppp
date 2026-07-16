from __future__ import annotations

from django.utils.cache import patch_cache_control, patch_vary_headers


class PublicSecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        is_admin_path = request.path.startswith("/admin/")
        if is_admin_path:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'",
            )
        else:
            # Cloudflare injects both the base beacon and versioned descendants
            # beneath /beacon.min.js/, so keep both path-scoped sources explicit.
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
                "script-src 'self' https://static.cloudflareinsights.com/beacon.min.js "
                "https://static.cloudflareinsights.com/beacon.min.js/; "
                "font-src 'self'; connect-src 'self'; media-src 'self' blob:; "
                "frame-src https://www.youtube-nocookie.com; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
            )

        is_private_path = request.path in {
            "/login/",
            "/join/",
            "/recover/",
            "/recovery-code/",
        } or request.path.startswith("/notifications/")
        user = getattr(request, "user", None)
        is_authenticated = user is not None and user.is_authenticated
        if is_admin_path or is_authenticated or is_private_path:
            patch_cache_control(
                response,
                private=True,
                no_store=True,
                no_transform=True,
            )
            patch_vary_headers(response, ("Cookie",))
        return response
