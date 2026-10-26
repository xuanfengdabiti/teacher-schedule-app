"""Django settings for the teacher schedule backend.

Reads configuration from environment variables, never hard-codes secrets.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def parse_database_url(url: str) -> dict[str, str]:
    """Parse a `postgres://…` / `postgresql://…` DATABASE_URL into a Django
    DATABASES entry.

    Handles URL‑encoded passwords and defaults the port to 5432 when none is
    supplied.  ``urllib.parse.unquote`` is used explicitly on user/password to
    mirror what libpq / psycopg do internally, guaranteeing that passwords
    containing ``%40`` (``@``), ``%3A`` (``:``) or similar work correctly.
    """
    parsed = urlparse(url)
    engine = "django.db.backends.postgresql"
    name = parsed.path.removeprefix("/")
    host = parsed.hostname or "localhost"
    port = str(parsed.port) if parsed.port else "5432"
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""

    return {
        "ENGINE": engine,
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": port,
    }


def env_bool(name: str, default: bool = False) -> bool:
    """Read a conventional on/off environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_app_link_base(value: str) -> str:
    """Return an App link prefix that can be concatenated with a route.

    Deep-link schemes such as ``teacherschedule://`` already end in the
    authority separator; HTTPS bases usually need exactly one trailing slash.
    """
    value = value.strip()
    if not value:
        return "teacherschedule://"
    if value.endswith("://"):
        return value
    return value.rstrip("/") + "/"


# ---------------------------------------------------------------------------
# core Django settings – all read from the environment
# ---------------------------------------------------------------------------

_secret_key = os.environ.get("DJANGO_SECRET_KEY")
if not _secret_key:
    raise RuntimeError(
        "DJANGO_SECRET_KEY environment variable is required. "
        "Copy backend/.env.example to backend/.env and run commands with "
        "`uv run --env-file .env ...`, or export it in your environment."
    )

SECRET_KEY = _secret_key

DEBUG = env_bool("DJANGO_DEBUG")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

# Mobile email links use an App custom scheme or a hosted App landing path.
APP_LINK_BASE = normalize_app_link_base(os.environ.get("APP_LINK_BASE", "teacherschedule://"))

# ---------------------------------------------------------------------------
# database – PostgreSQL only; missing DATABASE_URL is a hard error
# ---------------------------------------------------------------------------

_database_url = os.environ.get("DATABASE_URL")
if not _database_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is required. Set it to something like postgres://user:pass@host:5432/dbname"
    )

DATABASES = {"default": parse_database_url(_database_url)}

# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # third-party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    # local
    "apps.common",
    "apps.accounts",
    "apps.academics",
    "apps.classrooms",
    "apps.scheduling",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

SITE_ID = 1

ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# custom User – must be set before the first migration
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "allauth.account.auth_backends.AuthenticationBackend",
]

# ---------------------------------------------------------------------------
# email – local development defaults to Mailpit's SMTP endpoint
# ---------------------------------------------------------------------------

EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "127.0.0.1")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "1025"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "教师课程表 <noreply@example.invalid>")

# ---------------------------------------------------------------------------
# internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# rest framework & OpenAPI schema
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
}

# Access tokens are short-lived; refresh tokens rotate and are blacklisted.
# Mobile clients store both in the operating system's secure storage, never in
# an httpOnly cookie.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# Only dj-rest-auth exposes account endpoints; allauth headless is deliberately
# not installed.  User details live at /api/v1/me/, not /auth/user/.
REST_AUTH = {
    "USE_JWT": True,
    "TOKEN_MODEL": None,
    "SESSION_LOGIN": False,
    "JWT_AUTH_COOKIE": None,
    "JWT_AUTH_REFRESH_COOKIE": None,
    "JWT_AUTH_HTTPONLY": False,
    "OLD_PASSWORD_FIELD_ENABLED": True,
    "LOGOUT_ON_PASSWORD_CHANGE": False,
    "REGISTER_SERIALIZER": "apps.accounts.serializers.TeacherRegisterSerializer",
    "LOGIN_SERIALIZER": "apps.accounts.serializers.TeacherLoginSerializer",
    "PASSWORD_RESET_SERIALIZER": "apps.accounts.serializers.TeacherPasswordResetSerializer",
    "PASSWORD_CHANGE_SERIALIZER": "apps.accounts.serializers.RevokeOnPasswordChangeSerializer",
    "PASSWORD_RESET_CONFIRM_SERIALIZER": "apps.accounts.serializers.RevokeOnPasswordResetConfirmSerializer",
}

# Email-based login without a username.  allauth's current setting names
# (LOGIN_METHODS / SIGNUP_FIELDS) replace the deprecated ACCOUNT_* booleans.
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = False
ACCOUNT_EMAIL_NOTIFICATIONS = False
# Do not send allauth's "unknown account" mail: its web signup URL is not
# mounted, and a silent 200 avoids account enumeration.
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False

SPECTACULAR_SETTINGS = {
    "TITLE": "教师课程表 API",
    "DESCRIPTION": "教师课程表移动 App 后端接口合同",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "ContactVisibilityEnum": "apps.academics.models.ContactVisibility.choices",
    },
}

# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"

# Password validation (Django defaults)
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
