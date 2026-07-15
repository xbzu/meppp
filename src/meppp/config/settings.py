from __future__ import annotations

import os
from pathlib import Path

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


BASE_DIR = Path(__file__).resolve().parents[3]
ENVIRONMENT = os.getenv("MEPPP_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"
DEBUG = env_bool("MEPPP_DEBUG", not IS_PRODUCTION)

SECRET_KEY = os.getenv("MEPPP_SECRET_KEY", "")
if IS_PRODUCTION and not SECRET_KEY:
    raise ImproperlyConfigured("MEPPP_SECRET_KEY is required in production")
if IS_PRODUCTION and (len(SECRET_KEY) < 50 or "replace" in SECRET_KEY.lower()):
    raise ImproperlyConfigured("MEPPP_SECRET_KEY must be a strong, non-placeholder value")
if not SECRET_KEY:
    SECRET_KEY = "development-only-secret-key-do-not-use-in-production"

SECRET_KEY_FALLBACKS = env_list("MEPPP_SECRET_KEY_FALLBACKS")
ALLOWED_HOSTS = env_list("MEPPP_ALLOWED_HOSTS", "127.0.0.1,localhost" if not IS_PRODUCTION else "")
if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("MEPPP_ALLOWED_HOSTS is required in production")

CSRF_TRUSTED_ORIGINS = env_list("MEPPP_CSRF_TRUSTED_ORIGINS")
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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if IS_PRODUCTION:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "meppp.config.urls"
WSGI_APPLICATION = "meppp.config.wsgi.application"
ASGI_APPLICATION = "meppp.config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
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

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_PRODUCTION
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/admin/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_MODE = env_bool("MEPPP_SECURE", IS_PRODUCTION)
SESSION_COOKIE_SECURE = SECURE_MODE
CSRF_COOKIE_SECURE = SECURE_MODE
SECURE_SSL_REDIRECT = SECURE_MODE
SECURE_HSTS_SECONDS = 31_536_000 if SECURE_MODE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_MODE
SECURE_HSTS_PRELOAD = SECURE_MODE
if env_bool("MEPPP_TRUST_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": os.getenv("MEPPP_LOG_LEVEL", "INFO")},
}
