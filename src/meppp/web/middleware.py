from __future__ import annotations

from django.utils.cache import patch_vary_headers


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

        if request.path.startswith("/admin/"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'",
            )
        else:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; font-src 'self'; connect-src 'self'; "
                "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
            )

        is_private_path = request.path in {"/login/", "/join/"} or request.path.startswith(
            "/notifications/"
        )
        if getattr(request, "user", None) is not None and (
            request.user.is_authenticated or is_private_path
        ):
            response.headers["Cache-Control"] = "private, no-store"
            patch_vary_headers(response, ("Cookie",))
        return response
