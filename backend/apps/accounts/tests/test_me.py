"""GET/PATCH /api/v1/me/ contract and editable-field rules."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.academics.models import ContactVisibility, TeacherStatus, Term

from .conftest import jwt_client_for, make_teacher_profile, make_user

pytestmark = pytest.mark.django_db


def _current_term(school) -> Term:
    today = timezone.localdate()
    start = today - timedelta(days=30)
    first_monday = start - timedelta(days=start.weekday())
    return Term.objects.create(
        school=school,
        code="2026-current",
        name="当前学期",
        start_date=start,
        end_date=today + timedelta(days=30),
        first_week_monday=first_monday,
        teaching_week_count=20,
    )


def test_unlinked_user_cannot_patch_teacher_fields(api_client) -> None:
    user = make_user("unlinked-patch@example.invalid")
    response = jwt_client_for(user).patch(
        "/api/v1/me/",
        {"phone": "13900000000"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data == {
        "code": "TEACHER_NOT_LINKED",
        "detail": "当前账号未关联教师档案，不能修改教师资料。",
        "field_errors": {},
    }


def test_linked_user_can_patch_contacts_and_me_includes_current_term(api_client, school) -> None:
    term = _current_term(school)
    user = make_user("linked@example.invalid")
    profile = make_teacher_profile(
        school,
        "T4001",
        email="linked@example.invalid",
        formal_name="可编辑教师",
        department="原院系",
    )
    profile.user = user
    profile.teacher_status = TeacherStatus.ACTIVE
    profile.save(update_fields=["user", "teacher_status", "updated_at"])

    client = jwt_client_for(user)
    response = client.patch(
        "/api/v1/me/",
        {
            "display_name": "显示名",
            "department": "侦查学院",
            "phone": "13900000000",
            "phone_visibility": "course_teachers",
            "wechat": "wechat-demo",
            "wechat_visibility": "school_teachers",
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["email_verified"] is True
    assert response.data["teacher_status"] == "active"
    assert response.data["assignment_scope_version"] == 1
    assert response.data["current_term"]["id"] == str(term.id)
    assert response.data["current_term"]["code"] == "2026-current"
    assert response.data["teacher"]["teacher_no"] == "T4001"
    assert response.data["teacher"]["display_name"] == "显示名"
    assert response.data["teacher"]["department"] == "侦查学院"
    assert response.data["teacher"]["phone"] == "13900000000"
    assert response.data["teacher"]["phone_visibility"] == "course_teachers"
    assert response.data["teacher"]["wechat"] == "wechat-demo"
    assert response.data["teacher"]["wechat_visibility"] == "school_teachers"

    profile.refresh_from_db()
    assert profile.phone == "13900000000"
    assert profile.phone_visibility == ContactVisibility.COURSE_TEACHERS


@pytest.mark.parametrize(
    "payload",
    [
        {"phone_visibility": "friends"},
        {"wechat_visibility": "public"},
    ],
)
def test_invalid_contact_visibility_is_rejected(payload, school) -> None:
    user = make_user("bad-visibility@example.invalid")
    profile = make_teacher_profile(school, "T4002", email="bad-visibility@example.invalid")
    profile.user = user
    profile.teacher_status = TeacherStatus.ACTIVE
    profile.save(update_fields=["user", "teacher_status", "updated_at"])

    response = jwt_client_for(user).patch("/api/v1/me/", payload, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "VALIDATION_ERROR"
    assert next(iter(payload)) in response.data["field_errors"]


@pytest.mark.parametrize(
    "payload",
    [
        {"teacher_no": "T9999"},
        {"formal_name": "冒名教师"},
    ],
)
def test_identity_fields_are_read_only(payload, school) -> None:
    user = make_user("readonly@example.invalid")
    profile = make_teacher_profile(school, "T4003", email="readonly@example.invalid")
    profile.user = user
    profile.teacher_status = TeacherStatus.ACTIVE
    profile.save(update_fields=["user", "teacher_status", "updated_at"])
    original_teacher_no = profile.teacher_no
    original_formal_name = profile.formal_name

    response = jwt_client_for(user).patch("/api/v1/me/", payload, format="json")

    assert response.status_code == 400
    assert next(iter(payload)) in response.data["field_errors"]
    profile.refresh_from_db()
    assert profile.teacher_no == original_teacher_no
    assert profile.formal_name == original_formal_name


def test_inactive_profile_reports_disabled_status(school) -> None:
    user = make_user("disabled-profile@example.invalid")
    profile = make_teacher_profile(school, "T4004", email="disabled-profile@example.invalid")
    profile.user = user
    profile.teacher_status = TeacherStatus.DISABLED
    profile.is_active = False
    profile.save(update_fields=["user", "teacher_status", "is_active", "updated_at"])

    response = jwt_client_for(user).get("/api/v1/me/")

    assert response.status_code == 200
    assert response.data["teacher_status"] == "disabled"


@pytest.mark.parametrize(
    ("is_active", "teacher_status"),
    [
        (False, TeacherStatus.ACTIVE),
        (True, TeacherStatus.DISABLED),
    ],
)
def test_disabled_profile_cannot_patch_teacher_fields(is_active, teacher_status, school) -> None:
    user = make_user("disabled-patch@example.invalid")
    profile = make_teacher_profile(
        school,
        "T4005",
        email="disabled-patch@example.invalid",
        phone="13800000000",
    )
    profile.user = user
    profile.teacher_status = teacher_status
    profile.is_active = is_active
    profile.save(update_fields=["user", "teacher_status", "is_active", "updated_at"])

    response = jwt_client_for(user).patch(
        "/api/v1/me/",
        {"phone": "13900000000"},
        format="json",
    )

    assert response.status_code == 403
    assert response.data == {
        "code": "TEACHER_DISABLED",
        "detail": "教师档案已停用，不能修改教师资料。",
        "field_errors": {},
    }
    profile.refresh_from_db()
    assert profile.phone == "13800000000"
