"""Classroom list contract and existing teacher-status errors (B32)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.teaching.tests.helpers import auth_client, link_user

pytestmark = pytest.mark.django_db


def test_classroom_list_fields_filter_and_order(demo_term):
    user = link_user(demo_term.teachers["0001"], "classroom-list@example.invalid")
    client = auth_client(user)

    response = client.get("/api/v1/classrooms/?q=A301")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    row = payload["results"][0]
    assert set(row) == {
        "id",
        "classroom_code",
        "campus",
        "building",
        "room_no",
        "capacity",
        "is_active",
    }
    assert row["classroom_code"] == "A301"

    response = client.get("/api/v1/classrooms/?campus=虚构校区&building=示例教学楼A")
    assert response.status_code == 200
    codes = [item["classroom_code"] for item in response.json()["results"]]
    assert codes == ["A301", "A302"]


def test_classroom_endpoints_keep_existing_403_codes(demo_term):
    demo = demo_term
    unlinked = get_user_model().objects.create_user(
        email="classroom-unlinked@example.invalid", password="test-pass-123"
    )
    response = auth_client(unlinked).get("/api/v1/classrooms/")
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_NOT_LINKED"

    user = link_user(demo.teachers["0001"], "classroom-disabled@example.invalid")
    demo.teachers["0001"].is_active = False
    demo.teachers["0001"].save(update_fields=["is_active", "updated_at"])
    response = auth_client(user).get(f"/api/v1/classrooms/{demo.classrooms['A301'].id}/usage/?date=2026-10-26")
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_DISABLED"
