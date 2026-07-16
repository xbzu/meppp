from __future__ import annotations

import os
import re
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean value")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_nonnegative_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip()
    if not normalized.isascii() or not normalized.isdigit():
        raise ImproperlyConfigured(f"{name} must be a non-negative integer")
    return int(normalized)


def env_networks(name: str) -> tuple:
    try:
        return tuple(ip_network(item, strict=False) for item in env_list(name))
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must contain valid IP addresses or CIDRs") from error


HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


def is_valid_host(value: str, *, allow_unbracketed_ipv6: bool = False) -> bool:
    if (
        ":" in value
        and not allow_unbracketed_ipv6
        and not (value.startswith("[") and value.endswith("]"))
    ):
        return False
    ip_candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        ip_address(ip_candidate)
    except ValueError:
        return HOSTNAME_PATTERN.fullmatch(value) is not None
    return True


def validate_production_secret(name: str, value: str) -> None:
    placeholder_markers = (
        "replace",
        "change-me",
        "changeme",
        "development-only",
        "django-insecure",
    )
    weak_value = (
        len(value) < 50
        or value != value.strip()
        or len(set(value)) < 5
        or any(marker in value.casefold() for marker in placeholder_markers)
    )
    if weak_value:
        raise ImproperlyConfigured(f"{name} must contain only strong, non-placeholder values")


def validate_csrf_origin(origin: str, *, require_https: bool) -> None:
    parsed = urlsplit(origin)
    try:
        _ = parsed.port
    except ValueError as error:
        raise ImproperlyConfigured(
            "MEPPP_CSRF_TRUSTED_ORIGINS must contain valid origins"
        ) from error
    invalid = (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not is_valid_host(parsed.hostname, allow_unbracketed_ipv6=True)
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.path)
        or bool(parsed.query)
        or bool(parsed.fragment)
    )
    if invalid or (require_https and parsed.scheme != "https"):
        raise ImproperlyConfigured(
            "MEPPP_CSRF_TRUSTED_ORIGINS must contain exact HTTPS origins in production"
        )


BASE_DIR = Path(__file__).resolve().parents[3]
PACKAGE_DIR = Path(__file__).resolve().parents[1]
ENVIRONMENT = os.getenv("MEPPP_ENV", "development").strip().lower()
if ENVIRONMENT not in {"development", "test", "production"}:
    raise ImproperlyConfigured("MEPPP_ENV must be development, test, or production")
IS_PRODUCTION = ENVIRONMENT == "production"
DEBUG = env_bool("MEPPP_DEBUG", not IS_PRODUCTION)
if IS_PRODUCTION and DEBUG:
    raise ImproperlyConfigured("MEPPP_DEBUG must be disabled in production")

SECRET_KEY = os.getenv("MEPPP_SECRET_KEY", "")
if IS_PRODUCTION and not SECRET_KEY:
    raise ImproperlyConfigured("MEPPP_SECRET_KEY is required in production")
if IS_PRODUCTION:
    validate_production_secret("MEPPP_SECRET_KEY", SECRET_KEY)
if not SECRET_KEY:
    SECRET_KEY = "development-only-secret-key-do-not-use-in-production"

SECRET_KEY_FALLBACKS = env_list("MEPPP_SECRET_KEY_FALLBACKS")
if IS_PRODUCTION:
    for fallback in SECRET_KEY_FALLBACKS:
        validate_production_secret("MEPPP_SECRET_KEY_FALLBACKS", fallback)
    if SECRET_KEY in SECRET_KEY_FALLBACKS or len(SECRET_KEY_FALLBACKS) != len(
        set(SECRET_KEY_FALLBACKS)
    ):
        raise ImproperlyConfigured(
            "MEPPP_SECRET_KEY_FALLBACKS must be unique and must not contain MEPPP_SECRET_KEY"
        )

ALLOWED_HOSTS = env_list("MEPPP_ALLOWED_HOSTS", "127.0.0.1,localhost" if not IS_PRODUCTION else "")
if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("MEPPP_ALLOWED_HOSTS is required in production")
if any(host == "*" or not is_valid_host(host) for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("MEPPP_ALLOWED_HOSTS must contain exact hostnames or IP addresses")

CSRF_TRUSTED_ORIGINS = env_list("MEPPP_CSRF_TRUSTED_ORIGINS")
for csrf_origin in CSRF_TRUSTED_ORIGINS:
    validate_csrf_origin(csrf_origin, require_https=IS_PRODUCTION)
DATA_DIR = Path(os.getenv("MEPPP_DATA_DIR", BASE_DIR / "data")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "meppp.accounts.apps.AccountsConfig",
    "meppp.configuration.apps.ConfigurationConfig",
    "meppp.publishing.apps.PublishingConfig",
    "meppp.social.apps.SocialConfig",
    "meppp.notifications.apps.NotificationsConfig",
    "meppp.audit.apps.AuditConfig",
    "meppp.moderation.apps.ModerationConfig",
    "meppp.operations.apps.OperationsConfig",
    "meppp.web.apps.WebConfig",
]

MIDDLEWARE = [
    "meppp.config.middleware.TrustedProxyHeadersMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "meppp.web.middleware.PublicSecurityHeadersMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if IS_PRODUCTION:
    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "meppp.config.urls"
WSGI_APPLICATION = "meppp.config.wsgi.application"
ASGI_APPLICATION = "meppp.config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates", PACKAGE_DIR / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "meppp.web.context_processors.site_context",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "meppp.sqlite3",
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;PRAGMA foreign_keys=ON;"
            ),
        },
    }
}
TEST_DATABASE_PATH = os.getenv("MEPPP_TEST_DATABASE_PATH", "").strip()
if TEST_DATABASE_PATH:
    # LiveServerTestCase serves concurrent browser requests. A real SQLite file
    # exercises the production connection model; SQLite's shared in-memory test
    # database can produce cross-thread driver errors under parallel image loads.
    DATABASES["default"]["TEST"] = {"NAME": TEST_DATABASE_PATH}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = os.getenv("MEPPP_TIME_ZONE", "Asia/Shanghai")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
STORAGES = {
    "default": {"BACKEND": "meppp.publishing.storage.AtomicFileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_PRODUCTION
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "meppp-single-process",
    }
}

SECURE_MODE = env_bool("MEPPP_SECURE", IS_PRODUCTION)
if IS_PRODUCTION and not SECURE_MODE:
    raise ImproperlyConfigured("MEPPP_SECURE must be enabled in production")
SESSION_COOKIE_SECURE = SECURE_MODE
CSRF_COOKIE_SECURE = SECURE_MODE
SECURE_SSL_REDIRECT = SECURE_MODE
SECURE_REDIRECT_EXEMPT = [r"^health/(?:live|ready)$"]
SECURE_HSTS_SECONDS = env_nonnegative_int(
    "MEPPP_SECURE_HSTS_SECONDS",
    3_600 if IS_PRODUCTION and SECURE_MODE else 0,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("MEPPP_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("MEPPP_SECURE_HSTS_PRELOAD", False)
if SECURE_HSTS_INCLUDE_SUBDOMAINS and not SECURE_HSTS_SECONDS:
    raise ImproperlyConfigured(
        "MEPPP_SECURE_HSTS_INCLUDE_SUBDOMAINS requires a positive HSTS duration"
    )
if SECURE_HSTS_PRELOAD and (not SECURE_HSTS_INCLUDE_SUBDOMAINS or SECURE_HSTS_SECONDS < 31_536_000):
    raise ImproperlyConfigured(
        "MEPPP_SECURE_HSTS_PRELOAD requires subdomains and at least 31536000 seconds"
    )
# A short first-stage HSTS duration intentionally precedes the subdomain and preload commitments.
# Keep every other deployment warning active, and restore these two automatically at one year.
SILENCED_SYSTEM_CHECKS = (
    ["security.W005", "security.W021"] if 0 < SECURE_HSTS_SECONDS < 31_536_000 else []
)
TRUST_PROXY = env_bool("MEPPP_TRUST_PROXY", False)
TRUSTED_PROXY_NETWORKS = env_networks("MEPPP_TRUSTED_PROXY_IPS")
if TRUST_PROXY and not TRUSTED_PROXY_NETWORKS:
    raise ImproperlyConfigured("MEPPP_TRUSTED_PROXY_IPS is required when proxy trust is enabled")
if (
    IS_PRODUCTION
    and TRUST_PROXY
    and any(network.prefixlen != network.max_prefixlen for network in TRUSTED_PROXY_NETWORKS)
):
    raise ImproperlyConfigured(
        "MEPPP_TRUSTED_PROXY_IPS must contain exact proxy IP addresses in production"
    )
SECURE_PROXY_SSL_HEADER = ("HTTP_X_MEPPP_PROXY_PROTO", "https")

FILE_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 4
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
MEDIA_MIN_FREE_BYTES = env_nonnegative_int("MEPPP_MEDIA_MIN_FREE_BYTES", 256 * 1024 * 1024)

LOG_LEVEL = os.getenv("MEPPP_LOG_LEVEL", "INFO").strip().upper()
if LOG_LEVEL not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
    raise ImproperlyConfigured("MEPPP_LOG_LEVEL must be a valid logging level")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
