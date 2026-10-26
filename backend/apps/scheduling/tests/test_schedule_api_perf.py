"""Scale performance guard for the B13 weekly teaching-class schedule API."""

from __future__ import annotations

import time

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import TeacherStatus
from apps.scheduling.tests.scale_data import build_scale_data

pytestmark = [pytest.mark.django_db, pytest.mark.perf]


def _weekly_class_url(teaching_class_id) -> str:
    return f"/api/v1/sessions/?from=2026-10-26&to=2026-11-02&scope=teaching_class&teaching_class_id={teaching_class_id}"


def test_weekly_teaching_class_schedule_stays_under_half_a_second() -> None:
    data = build_scale_data()
    teacher = data.teachers[0]
    user = get_user_model().objects.create_user(email="perf-schedule@example.invalid", password="perf-pass-123")
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    url = _weekly_class_url(data.offerings[0].teaching_class_id)

    # Warm the URL resolver and serializers; the measured request is still a
    # complete authenticated HTTP call against PostgreSQL.
    warm_response = client.get(url)
    assert warm_response.status_code == 200
    assert warm_response.data["results"]  # type: ignore[index]

    started = time.perf_counter()
    response = client.get(url)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    result_count = len(response.data["results"])  # type: ignore[index]
    print(
        f"\nB13 weekly teaching-class API: {result_count} sessions in {elapsed * 1000:.0f} ms "
        f"(target < 500 ms; 20,000-session term)"
    )
    assert result_count
    assert elapsed < 0.5, f"weekly teaching-class query took {elapsed:.3f}s (target < 0.50s)"
