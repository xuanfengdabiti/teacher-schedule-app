"""Django settings for the teacher schedule backend.

Reads configuration from environment variables, never hard-codes secrets.
"""

from __future__ import annotations

import os
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

DEBUG = os.environ.get("DJANGO_DEBUG", "").lower() in {"1", "true", "yes"}

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

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
    # third-party
    "rest_framework",
    "drf_spectacular",
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
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "教师课程表 API",
    "DESCRIPTION": "教师课程表移动 App 后端接口合同",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
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
