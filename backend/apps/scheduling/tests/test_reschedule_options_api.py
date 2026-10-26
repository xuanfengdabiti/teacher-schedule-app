"""HTTP contract tests for B30 reschedule options."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.academics.models import TeacherStatus
from apps.scheduling.models import ClassSession, SessionStatus
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def generated_demo(demo_term):
    generate_sessions_for_term(demo_term.term)
    return demo_term


def _source(demo_term) -> ClassSession:
    return ClassSession.objects.select_related("offering__term", "classroom").get(
        rule=demo_term.rules["R-CRIME-EVEN"],
        origin_teaching_date=date(2026, 10, 26),
    )


def _link_user(teacher, email: str):
    user = get_user_model().objects.create_user(email=email, password="test-pass-123")
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user


def _url(session: ClassSession) -> str:
    return f"/api/v1/sessions/{session.id}/reschedule-options/"


def _complete(session: ClassSession, teacher) -> None:
    session.status = SessionStatus.COMPLETED
    session.actual_hours = Decimal("2.0")
    session.completed_at = django_timezone.now()
    session.completed_by_teacher = teacher
    session.completion_ref = "TEST-COMPLETION"
    session.save(
        update_fields=[
            "status",
            "actual_hours",
            "completed_at",
            "completed_by_teacher",
            "completion_ref",
            "updated_at",
        ]
    )


class TestAuthenticationAndPermissions:
    def test_unauthenticated_request_is_rejected(self, api_client, generated_demo) -> None:
        response = api_client.post(_url(_source(generated_demo)), {}, format="json")

        # SessionAuthentication with IsAuthenticated has no WWW-Authenticate
        # header, so DRF returns 403 rather than 401.
        assert response.status_code == 403

    def test_teacher_outside_session_and_assignment_gets_403(self, api_client, generated_demo) -> None:
        outsider = factories.make_teacher(generated_demo.school, "0003", "非任课示例教师")
        user = _link_user(outsider, "outsider@example.invalid")
        api_client.force_login(user)

        response = api_client.post(_url(_source(generated_demo)), {}, format="json")

        assert response.status_code == 403

    def test_user_without_teacher_profile_gets_403(self, api_client, generated_demo) -> None:
        user = get_user_model().objects.create_user(email="no-profile@example.invalid", password="test-pass-123")
        api_client.force_login(user)

        response = api_client.post(_url(_source(generated_demo)), {}, format="json")

        assert response.status_code == 403

    def test_missing_session_returns_404(self, api_client, generated_demo) -> None:
        user = _link_user(generated_demo.teachers["0002"], "session-teacher@example.invalid")
        api_client.force_login(user)

        response = api_client.post(
            f"/api/v1/sessions/{uuid4()}/reschedule-options/",
            {
                "date_from": "2026-10-27",
                "date_until": "2026-10-28",
            },
            format="json",
        )

        assert response.status_code == 404


class TestBusinessErrors:
    def test_completed_session_returns_409_with_code(self, api_client, generated_demo) -> None:
        source = _source(generated_demo)
        _complete(source, generated_demo.teachers["0002"])
        user = _link_user(generated_demo.teachers["0002"], "completed-source@example.invalid")
        api_client.force_login(user)

        response = api_client.post(
            _url(source),
            {
                "date_from": "2026-10-27",
                "date_until": "2026-10-28",
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["code"] == "SESSION_NOT_RESCHEDULABLE"  # type: ignore[index]
        assert response.data["field_errors"] == {}  # type: ignore[index]
        assert response.data["detail"]  # type: ignore[index]

    def test_equal_range_returns_400_with_code(self, api_client, generated_demo) -> None:
        user = _link_user(generated_demo.teachers["0002"], "invalid-range@example.invalid")
        api_client.force_login(user)

        response = api_client.post(
            _url(_source(generated_demo)),
            {
                "date_from": "2026-10-27",
                "date_until": "2026-10-27",
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["code"] == "INVALID_DATE_RANGE"  # type: ignore[index]
        assert "date_until" in response.data["field_errors"]  # type: ignore[index]


class TestResponseContract:
    def test_default_request_uses_school_today_and_fourteen_days(self, api_client, generated_demo) -> None:
        user = _link_user(generated_demo.teachers["0002"], "default-range@example.invalid")
        api_client.force_login(user)
        fixed_now = datetime(2026, 10, 26, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        with patch("apps.scheduling.views.timezone.now", return_value=fixed_now):
            response = api_client.post(_url(_source(generated_demo)), {}, format="json")

        assert response.status_code == 200
        assert response.data["checked_at"] == "2026-10-26T07:00:00+08:00"  # type: ignore[index]
        assert response.data["options"]  # type: ignore[index]
        # Default date_until is date_from + 14 days (half-open), so the last
        # target date is 2026-11-08, not 11-09.
        assert max(option["start_at"][:10] for option in response.data["options"]) <= "2026-11-08"  # type: ignore[index]

    def test_normal_request_returns_typed_options_and_iso_times(self, api_client, generated_demo) -> None:
        user = _link_user(generated_demo.teachers["0002"], "normal-response@example.invalid")
        api_client.force_login(user)
        session = _source(generated_demo)

        response = api_client.post(
            _url(session),
            {
                "date_from": "2026-12-28",
                "date_until": "2026-12-30",
                "allowed_block_codes": ["afternoon"],
            },
            format="json",
        )

        assert response.status_code == 200
        body = response.data  # type: ignore[assignment]
        assert set(body) == {
            "session_id",
            "schedule_version",
            "checked_at",
            "data_completeness",
            "options",
        }
        assert str(body["session_id"]) == str(session.id)
        assert body["schedule_version"] == generated_demo.term.schedule_version
        assert body["checked_at"].endswith("+08:00")
        assert body["data_completeness"] in {"complete", "partial"}
        assert body["options"]
        option = body["options"][0]
        assert set(option) == {
            "start_at",
            "end_at",
            "start_period",
            "end_period",
            "classroom_id",
            "classroom_code",
            "eligibility",
            "student_conflict_count",
            "conflicts",
            "missing_data",
            "reasons",
        }
        assert option["eligibility"] in {"available", "conflict", "unverified"}
        assert option["start_at"].endswith("+08:00")
        assert option["end_at"].endswith("+08:00")
        assert option["start_period"] <= option["end_period"]
        assert isinstance(option["student_conflict_count"], int)
        assert isinstance(option["conflicts"], list)
        assert isinstance(option["missing_data"], list)
        assert isinstance(option["reasons"], list)

    def test_schema_contains_endpoint_and_fields(self, api_client) -> None:
        response = api_client.get("/api/v1/schema/", {"format": "json"})

        assert response.status_code == 200
        schema = response.json()
        path = "/api/v1/sessions/{session_id}/reschedule-options/"
        assert path in schema["paths"]
        operation = schema["paths"][path]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/RescheduleOptionsQuery"
        )
        query_schema = schema["components"]["schemas"]["RescheduleOptionsQuery"]
        assert {"date_from", "date_until", "allowed_block_codes", "classroom_ids", "allow_weekend"} <= set(
            query_schema["properties"]
        )
        response_schema = schema["components"]["schemas"]["RescheduleOptionsResponse"]
        assert {"session_id", "schedule_version", "checked_at", "data_completeness", "options"} <= set(
            response_schema["properties"]
        )
