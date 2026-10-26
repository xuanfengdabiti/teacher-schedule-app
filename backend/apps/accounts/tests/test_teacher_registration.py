"""Registration, verification and teacher-roster matching."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.academics.models import TeacherStatus
from apps.accounts.models import TeacherClaim, TeacherClaimStatus, User

from .conftest import (
    PASSWORD,
    extract_verification_key,
    latest_message_body,
    login,
    make_teacher_profile,
    make_user,
    register,
)

pytestmark = pytest.mark.django_db


def _verify(api_client: APIClient, key: str):
    return api_client.post(
        "/api/v1/auth/registration/verify-email/",
        {"key": key},
        format="json",
    )


def test_a01_register_verify_match_login_and_me(api_client, school) -> None:
    profile = make_teacher_profile(
        school,
        "T0001",
        email="t0001@example.invalid",
        formal_name="示例教师甲",
    )
    email = "T0001@Example.invalid"

    response = register(api_client, email=email, formal_name="示例教师甲", teacher_no="T0001")

    assert response.status_code == 201
    assert "access" not in response.data
    user = User.objects.get(email="t0001@example.invalid")
    claim = TeacherClaim.objects.get(user=user)
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.claimed_formal_name == "示例教师甲"
    assert claim.claimed_teacher_no == "T0001"
    assert profile.user_id is None

    body = latest_message_body()
    assert "teacherschedule://verify-email?key=" in body
    key = extract_verification_key(body)
    # The body also contains the raw key for copying on another device.
    assert key in body

    denied_login = login(api_client, "t0001@example.invalid")
    assert denied_login.status_code == 400
    assert "邮箱尚未验证" in str(denied_login.data)

    verify_response = _verify(api_client, key)
    assert verify_response.status_code == 200

    claim.refresh_from_db()
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.APPROVED
    assert claim.decided_by_id is None
    assert claim.note == "邮箱与工号自动匹配"
    assert claim.matched_profile_id == profile.id
    assert profile.user_id == user.id
    assert profile.teacher_status == TeacherStatus.ACTIVE
    assert profile.assignment_scope_version == 2

    login_response = login(api_client, "t0001@example.invalid")
    assert login_response.status_code == 200
    assert login_response.data["access"]
    assert login_response.data["refresh"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}")
    me_response = api_client.get("/api/v1/me/")
    assert me_response.status_code == 200
    assert me_response.data["email_verified"] is True
    assert me_response.data["teacher_status"] == "active"
    assert me_response.data["assignment_scope_version"] == 2
    assert me_response.data["teacher"]["teacher_no"] == "T0001"
    assert me_response.data["teacher"]["formal_name"] == "示例教师甲"
    assert me_response.data["claim"] is None


def test_only_teacher_no_match_stays_pending(api_client, school) -> None:
    profile = make_teacher_profile(school, "T1001", email="roster-t1001@example.invalid")
    response = register(
        api_client,
        email="different-mail@example.invalid",
        formal_name="工号相同教师",
        teacher_no="T1001",
    )
    assert response.status_code == 201

    verify_response = _verify(api_client, extract_verification_key(latest_message_body()))
    assert verify_response.status_code == 200

    claim = TeacherClaim.objects.get(user__email="different-mail@example.invalid")
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id == profile.id
    assert claim.note == "仅工号匹配，邮件与工号未同时匹配，等待教务核对。"
    assert profile.user_id is None


def test_double_match_occupied_profile_has_accurate_note(api_client, school) -> None:
    profile = make_teacher_profile(school, "T1010", email="occupied-match@example.invalid")
    owner = make_user("existing-owner@example.invalid")
    profile.user = owner
    profile.teacher_status = TeacherStatus.ACTIVE
    profile.save(update_fields=["user", "teacher_status", "updated_at"])

    response = register(
        api_client,
        email="occupied-match@example.invalid",
        formal_name="双匹配教师",
        teacher_no="T1010",
    )
    assert response.status_code == 201

    verify_response = _verify(api_client, extract_verification_key(latest_message_body()))
    assert verify_response.status_code == 200

    claim = TeacherClaim.objects.get(user__email="occupied-match@example.invalid")
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id == profile.id
    assert claim.note == "邮箱与工号均匹配，但教师档案已关联其他账号，等待教务核对。"
    assert profile.user_id == owner.id


def test_only_email_match_stays_pending(api_client, school) -> None:
    profile = make_teacher_profile(school, "T1002", email="match-mail@example.invalid")
    response = register(
        api_client,
        email="match-mail@example.invalid",
        formal_name="邮箱相同教师",
        teacher_no="T9999",
    )
    assert response.status_code == 201

    verify_response = _verify(api_client, extract_verification_key(latest_message_body()))
    assert verify_response.status_code == 200

    claim = TeacherClaim.objects.get(user__email="match-mail@example.invalid")
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id is None
    assert profile.user_id is None


def test_double_match_inactive_profile_has_accurate_note(api_client, school) -> None:
    profile = make_teacher_profile(
        school,
        "T1011",
        email="inactive-match@example.invalid",
        is_active=False,
    )

    response = register(
        api_client,
        email="inactive-match@example.invalid",
        formal_name="停用档案教师",
        teacher_no="T1011",
    )
    assert response.status_code == 201

    verify_response = _verify(api_client, extract_verification_key(latest_message_body()))
    assert verify_response.status_code == 200

    claim = TeacherClaim.objects.get(user__email="inactive-match@example.invalid")
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id == profile.id
    assert claim.note == "邮箱与工号均匹配，但教师档案已停用，等待教务核对。"
    assert profile.user_id is None


def test_same_name_different_person_stays_pending(api_client, school) -> None:
    profile = make_teacher_profile(
        school,
        "T1003",
        email="roster-name@example.invalid",
        formal_name="同名示例教师",
    )
    response = register(
        api_client,
        email="same-name@example.invalid",
        formal_name="同名示例教师",
        teacher_no="T8888",
    )
    assert response.status_code == 201

    verify_response = _verify(api_client, extract_verification_key(latest_message_body()))
    assert verify_response.status_code == 200

    claim = TeacherClaim.objects.get(user__email="same-name@example.invalid")
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id is None
    assert profile.user_id is None
    assert profile.formal_name == claim.claimed_formal_name


def test_verification_key_can_be_pasted_raw(api_client, school) -> None:
    make_teacher_profile(school, "T1004", email="raw-key@example.invalid")
    register(api_client, email="raw-key@example.invalid", teacher_no="T1004")

    key = extract_verification_key(latest_message_body())
    response = _verify(api_client, key)
    assert response.status_code == 200
    assert TeacherClaim.objects.get(user__email="raw-key@example.invalid").status == TeacherClaimStatus.APPROVED


def test_registration_requires_formal_name_and_teacher_no(api_client) -> None:
    response = api_client.post(
        "/api/v1/auth/registration/",
        {
            "email": "missing-fields@example.invalid",
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "formal_name" in response.data
    assert "teacher_no" in response.data
