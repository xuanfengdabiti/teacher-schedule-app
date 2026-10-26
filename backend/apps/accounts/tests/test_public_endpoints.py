"""Anonymous endpoints and the generated account API contract."""

from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from .conftest import extract_verification_key, latest_message_body, register

pytestmark = pytest.mark.django_db


def test_health_and_schema_are_anonymous(api_client) -> None:
    assert api_client.get("/api/v1/health/").status_code == 200
    assert api_client.get("/api/v1/schema/", {"format": "json"}).status_code == 200


def test_anonymous_account_recovery_endpoints_are_reachable(api_client) -> None:
    registration = register(api_client, email="anonymous@example.invalid", teacher_no="T5001")
    assert registration.status_code == 201

    verify = api_client.post(
        "/api/v1/auth/registration/verify-email/",
        {"key": extract_verification_key(latest_message_body())},
        format="json",
    )
    assert verify.status_code == 200

    resend = api_client.post(
        "/api/v1/auth/registration/resend-email/",
        {"email": "anonymous@example.invalid"},
        format="json",
    )
    assert resend.status_code == 200

    bad_login = api_client.post(
        "/api/v1/auth/login/",
        {"email": "anonymous@example.invalid", "password": "wrong-password"},
        format="json",
    )
    assert bad_login.status_code == 400

    reset = api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "anonymous@example.invalid"},
        format="json",
    )
    assert reset.status_code == 200

    unknown_reset = api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "not-registered@example.invalid"},
        format="json",
    )
    assert unknown_reset.status_code == 200

    bad_refresh = api_client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": "not-a-token"},
        format="json",
    )
    assert bad_refresh.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/me/"),
        ("patch", "/api/v1/me/"),
        ("post", "/api/v1/me/deactivation/"),
        ("post", "/api/v1/auth/logout/"),
        ("post", "/api/v1/auth/password/change/"),
    ],
)
def test_default_permission_requires_authentication(method, path) -> None:
    client = APIClient()
    response = getattr(client, method)(path, {}, format="json")
    assert response.status_code == 401


def test_reschedule_options_requires_authentication() -> None:
    response = APIClient().post(
        f"/api/v1/sessions/{uuid4()}/reschedule-options/",
        {},
        format="json",
    )
    assert response.status_code == 401


def test_openapi_contains_account_contract(api_client) -> None:
    response = api_client.get("/api/v1/schema/", {"format": "json"})
    schema = response.json()

    account_paths = {
        "/api/v1/auth/registration/",
        "/api/v1/auth/registration/verify-email/",
        "/api/v1/auth/registration/resend-email/",
        "/api/v1/auth/login/",
        "/api/v1/auth/logout/",
        "/api/v1/auth/token/refresh/",
        "/api/v1/auth/password/reset/",
        "/api/v1/auth/password/reset/confirm/",
        "/api/v1/auth/password/change/",
        "/api/v1/me/",
        "/api/v1/me/deactivation/",
    }
    assert account_paths <= set(schema["paths"])

    registration = schema["components"]["schemas"]["TeacherRegister"]
    assert {"email", "password1", "password2", "formal_name", "teacher_no"} <= set(registration["properties"])
    assert "username" not in registration["properties"]

    me_schema = schema["components"]["schemas"]["Me"]
    assert {
        "id",
        "email",
        "email_verified",
        "teacher_status",
        "assignment_scope_version",
        "current_term",
        "teacher",
        "claim",
    } <= set(me_schema["properties"])

    update_schema = schema["components"]["schemas"]["PatchedMeUpdate"]
    assert {
        "display_name",
        "department",
        "phone",
        "phone_visibility",
        "wechat",
        "wechat_visibility",
    } == set(update_schema["properties"])
    assert "teacher_no" not in update_schema["properties"]


def test_registration_response_has_no_tokens(api_client) -> None:
    response = register(api_client, email="no-token@example.invalid", teacher_no="T5002")
    assert response.status_code == 201
    assert set(response.data) == {"detail"}
