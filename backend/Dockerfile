# syntax=docker/dockerfile:1
#
# Production-style image for the Django/DRF backend.
#
# Build from the repository root (the backend is the build context):
#   docker compose -f infra/deploy/compose.yaml build
#
# Runtime:
#   - Python 3.12 patch release, pinned by digest for reproducible rebuilds.
#   - Dependencies come from uv.lock and are installed with `uv sync --frozen`
#     (no resolution, dev group excluded).
#   - Gunicorn serves config.wsgi:application; `runserver` is never used.
#   - The entrypoint runs migrations and collectstatic, then execs Gunicorn so
#     it becomes PID 1.
#
# This is a single-instance startup model: run exactly one API replica so that
# migrations and collectstatic happen once. Scale out only after moving those
# steps to a dedicated release job.

FROM python:3.12.16-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# `libpq5` is the runtime library used by psycopg; copy the uv binary from
# the official image instead of downloading it at build time.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

WORKDIR /app

# Dependency layer: install exactly what uv.lock records, without the dev
# group and without downloading another Python interpreter.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# The project is a virtual uv project (no build backend), so the working
# directory is placed on sys.path for Gunicorn/manage.py imports.
ENV PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings \
    DJANGO_MEDIA_ROOT=/app/var/media \
    PATH="/app/.venv/bin:${PATH}"

RUN mkdir -p /app/staticfiles /app/var/media /app/var/log \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/', timeout=3).read()"]

# `--` becomes $0 and the CMD array becomes "$@", so exec hands PID 1 directly
# to Gunicorn. Migrations and collectstatic run once per single-instance start.
ENTRYPOINT ["/bin/sh", "-c", "set -eu; python manage.py migrate --noinput; python manage.py collectstatic --noinput; exec \"$@\"", "--"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
