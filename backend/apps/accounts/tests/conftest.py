"""Shared account test fixtures."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import School, TeacherProfile
from apps.accounts.models import User

PASSWORD = "strong-test-password-2026"


@pytest.fixture(autouse=True)
def locmem_email(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    cache.clear()
    yield
    mail.outbox = []


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def school(db) -> School:
    return School.objects.create(code="DEMO-SCHOOL", name="虚构演示学校")


def make_user(email: str, *, password: str = PASSWORD, verified: bool = True, **extra) -> User:
    user = User.objects.create_user(email=email, password=password, **extra)
    if verified:
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


def make_teacher_profile(
    school: School,
    teacher_no: str,
    *,
    email: str = "",
    formal_name: str | None = None,
    **extra,
) -> TeacherProfile:
    return TeacherProfile.objects.create(
        school=school,
        teacher_no=teacher_no,
        formal_name=formal_name or f"示例教师{teacher_no}",
        email=email,
        **extra,
    )


def jwt_client_for(user: User, client: APIClient | None = None) -> APIClient:
    client = client or APIClient()
    access = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def registration_payload(
    *,
    email: str = "teacher@example.invalid",
    formal_name: str = "示例教师",
    teacher_no: str = "T0001",
    password: str = PASSWORD,
) -> dict[str, str]:
    return {
        "email": email,
        "password1": password,
        "password2": password,
        "formal_name": formal_name,
        "teacher_no": teacher_no,
    }


def register(api_client: APIClient, **overrides):
    return api_client.post(
        "/api/v1/auth/registration/",
        registration_payload(**overrides),
        format="json",
    )


def latest_message_body() -> str:
    assert mail.outbox, "expected an email"
    return mail.outbox[-1].body


def extract_verification_key(body: str) -> str:
    marker = "verify-email?key="
    start = body.index(marker) + len(marker)
    encoded = body[start:].splitlines()[0].strip()
    return unquote(encoded)


def extract_reset_credentials(body: str) -> tuple[str, str]:
    marker = "reset-password?uid="
    start = body.index(marker) + len(marker)
    encoded = body[start:].splitlines()[0].strip()
    uid_part, token_part = encoded.split("&token=", 1)
    return unquote(uid_part), unquote(token_part)


def login(api_client: APIClient, email: str, password: str = PASSWORD):
    return api_client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": password},
        format="json",
    )
