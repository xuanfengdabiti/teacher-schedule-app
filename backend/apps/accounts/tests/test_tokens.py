"""JWT rotation, logout, password-change/reset and deactivation revocation."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.academics.models import ContactVisibility
from apps.accounts.models import User
from apps.notifications.models import DeviceRegistration

from .conftest import (
    PASSWORD,
    extract_reset_credentials,
    latest_message_body,
    login,
    make_teacher_profile,
    make_user,
)

pytestmark = pytest.mark.django_db


def _tokens(api_client: APIClient, email: str) -> dict[str, str]:
    response = login(api_client, email)
    assert response.status_code == 200, response.data
    return {"access": response.data["access"], "refresh": response.data["refresh"]}


def _authed_client(access: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def test_refresh_rotation_blacklists_old_token(api_client) -> None:
    make_user("rotate@example.invalid")
    tokens = _tokens(api_client, "rotate@example.invalid")

    rotated = api_client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert rotated.status_code == 200
    assert rotated.data["access"]
    assert rotated.data["refresh"] != tokens["refresh"]

    old_again = api_client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert old_again.status_code == 401

    new_again = api_client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": rotated.data["refresh"]},
        format="json",
    )
    assert new_again.status_code == 200


def test_logout_blacklists_current_refresh(api_client) -> None:
    make_user("logout@example.invalid")
    tokens = _tokens(api_client, "logout@example.invalid")

    response = _authed_client(tokens["access"]).post(
        "/api/v1/auth/logout/",
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert response.status_code == 200

    refresh = api_client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert refresh.status_code == 401


def test_password_change_revokes_every_refresh_token(api_client) -> None:
    make_user("change@example.invalid")
    first_device = _tokens(api_client, "change@example.invalid")
    second_device = _tokens(APIClient(), "change@example.invalid")
    new_password = "another-strong-password-2026"

    response = _authed_client(first_device["access"]).post(
        "/api/v1/auth/password/change/",
        {
            "old_password": PASSWORD,
            "new_password1": new_password,
            "new_password2": new_password,
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["reauthentication_required"] is True
    assert "重新登录" in response.data["detail"]

    for refresh_token in (first_device["refresh"], second_device["refresh"]):
        refresh = api_client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        assert refresh.status_code == 401

    assert login(api_client, "change@example.invalid", PASSWORD).status_code == 400
    assert login(api_client, "change@example.invalid", new_password).status_code == 200


def test_password_reset_confirm_revokes_every_refresh_token(api_client) -> None:
    make_user("reset@example.invalid")
    first_device = _tokens(api_client, "reset@example.invalid")
    second_device = _tokens(APIClient(), "reset@example.invalid")
    new_password = "reset-strong-password-2026"

    reset_request = api_client.post(
        "/api/v1/auth/password/reset/",
        {"email": "reset@example.invalid"},
        format="json",
    )
    assert reset_request.status_code == 200
    body = latest_message_body()
    assert "teacherschedule://reset-password?uid=" in body
    assert "可复制参数" in body
    uid, token = extract_reset_credentials(body)

    reset_confirm = api_client.post(
        "/api/v1/auth/password/reset/confirm/",
        {
            "uid": uid,
            "token": token,
            "new_password1": new_password,
            "new_password2": new_password,
        },
        format="json",
    )
    assert reset_confirm.status_code == 200

    for refresh_token in (first_device["refresh"], second_device["refresh"]):
        refresh = api_client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        assert refresh.status_code == 401

    assert login(api_client, "reset@example.invalid", PASSWORD).status_code == 400
    assert login(api_client, "reset@example.invalid", new_password).status_code == 200


def test_deactivation_disables_login_refresh_and_clears_contacts(api_client, school) -> None:
    user = make_user("deactivate@example.invalid")
    profile = make_teacher_profile(
        school,
        "T3001",
        email="deactivate@example.invalid",
        phone="13800000000",
        phone_visibility=ContactVisibility.SCHOOL_TEACHERS,
        wechat="demo-wechat",
        wechat_visibility=ContactVisibility.COURSE_TEACHERS,
    )
    profile.user = user
    profile.teacher_status = "active"
    profile.save(update_fields=["user", "teacher_status", "updated_at"])
    tokens = _tokens(api_client, "deactivate@example.invalid")

    response = _authed_client(tokens["access"]).post("/api/v1/me/deactivation/")
    assert response.status_code == 204

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.is_active is False
    assert User.objects.filter(pk=user.pk).exists()
    assert profile.phone == ""
    assert profile.wechat == ""
    assert profile.phone_visibility == ContactVisibility.PRIVATE
    assert profile.wechat_visibility == ContactVisibility.PRIVATE

    refresh = api_client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert refresh.status_code == 401
    assert login(api_client, "deactivate@example.invalid").status_code == 400
    assert _authed_client(tokens["access"]).get("/api/v1/me/").status_code == 401


def test_deactivation_disables_all_device_bindings(api_client, school) -> None:
    user = make_user("deactivate-devices@example.invalid")
    profile = make_teacher_profile(school, "T3002", email="deactivate-devices@example.invalid")
    profile.user = user
    profile.teacher_status = "active"
    profile.save(update_fields=["user", "teacher_status", "updated_at"])
    devices = [
        DeviceRegistration.objects.create(
            user=user,
            installation_id=f"deactivate-install-{index}",
            provider="getui" if index == 0 else "apns",
            provider_client_id=f"deactivate-client-{index}",
            platform="android" if index == 0 else "ios",
        )
        for index in range(2)
    ]
    tokens = _tokens(api_client, "deactivate-devices@example.invalid")

    response = _authed_client(tokens["access"]).post("/api/v1/me/deactivation/")

    assert response.status_code == 204
    for device in devices:
        device.refresh_from_db()
        assert device.enabled is False
        assert device.provider_client_id is None
