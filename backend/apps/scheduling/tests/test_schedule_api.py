"""Contract and permission tests for the B13 schedule read APIs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import Enrollment, TeacherStatus, TeachingAssignment
from apps.scheduling.models import ClassSession, SessionStatus
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db

WEEK_8 = {
    "from": "2026-10-26",
    "to": "2026-11-02",
}


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def generated_demo(demo_term):
    generate_sessions_for_term(demo_term.term)
    return demo_term


def _link_user(teacher, email: str):
    user = get_user_model().objects.create_user(email=email, password="test-pass-123")
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user


def _authenticate(client: APIClient, user) -> APIClient:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def _session_url(params: dict | None = None) -> str:
    if not params:
        return "/api/v1/sessions/"
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"/api/v1/sessions/?{query}"


def _week_sessions(client: APIClient, **overrides):
    params = {**WEEK_8, **overrides}
    return client.get(_session_url(params))


def _source_session(demo_term, rule_code: str, origin: date) -> ClassSession:
    return ClassSession.objects.get(
        rule=demo_term.rules[rule_code],
        origin_teaching_date=origin,
    )


class TestTeacherScopes:
    def test_teacher_0002_mine_only_sees_their_even_week_crime_session(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "teacher-0002@example.invalid"))

        response = _week_sessions(api_client)

        assert response.status_code == 200
        body = response.data
        assert body["term"]["id"] == str(generated_demo.term.id)  # type: ignore[index]
        assert body["term"]["schedule_version"] == generated_demo.term.schedule_version  # type: ignore[index]
        assert len(body["results"]) == 1  # type: ignore[index]
        session = body["results"][0]  # type: ignore[index]
        assert session["course_code"] == "C-CRIME"
        assert session["course_name"] == "刑事侦查专题研究（示例）"
        assert session["is_mine"] is True
        assert session["teaching_week"] == 8
        assert session["weekday"] == 1
        assert session["date"] == "2026-10-26"
        assert session["start_period"] == 1
        assert session["end_period"] == 2
        assert session["start_at"] == "2026-10-26T08:00:00+08:00"
        assert session["end_at"] == "2026-10-26T09:40:00+08:00"
        assert session["teaching_class"]["class_code"] == "TC-2026-A"
        assert session["teachers"] == [{"id": str(generated_demo.teachers["0002"].id), "name": "示例教师乙"}]
        assert session["classroom"]["classroom_code"] == "A301"
        assert "phone" not in session["teachers"][0]

    def test_teacher_0001_mine_sees_data_analysis_but_not_0002_crime(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0001"], "teacher-0001@example.invalid"))

        response = _week_sessions(api_client)

        assert response.status_code == 200
        results = response.data["results"]  # type: ignore[index]
        assert len(results) == 1
        session = results[0]
        assert session["course_code"] == "C-DATA"
        assert session["start_period"] == 3
        assert session["end_period"] == 4
        assert session["start_at"] == "2026-10-26T10:00:00+08:00"
        assert session["is_mine"] is True
        assert all(item["course_code"] != "C-CRIME" for item in results)

    def test_same_teaching_class_teacher_sees_other_offering_but_is_not_mine(
        self,
        api_client,
        generated_demo,
    ) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "same-class@example.invalid"))
        data = generated_demo.offerings["OFF-DATA-2026A"]

        response = _week_sessions(
            api_client,
            scope="teaching_class",
            teaching_class_id=data.teaching_class_id,
        )

        assert response.status_code == 200
        results = response.data["results"]  # type: ignore[index]
        assert {item["course_code"] for item in results} == {"C-CRIME", "C-DATA"}
        data_session = next(item for item in results if item["course_code"] == "C-DATA")
        crime_session = next(item for item in results if item["course_code"] == "C-CRIME")
        assert data_session["is_mine"] is False
        assert crime_session["is_mine"] is True

    def test_offering_scope_returns_only_the_requested_visible_offering(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "offering-scope@example.invalid"))
        data = generated_demo.offerings["OFF-DATA-2026A"]

        response = _week_sessions(api_client, scope="offering", offering_id=data.id)

        assert response.status_code == 200
        results = response.data["results"]  # type: ignore[index]
        assert len(results) == 1
        assert results[0]["offering_id"] == str(data.id)
        assert results[0]["course_code"] == "C-DATA"
        assert results[0]["is_mine"] is False

    def test_unrelated_offering_and_class_are_hidden_and_return_404(self, api_client, generated_demo) -> None:
        outsider = factories.make_teacher(generated_demo.school, "0003", "无关示例教师")
        other_offering = factories.make_offering(
            generated_demo.term,
            outsider,
            offering_code="OFF-OUTSIDE-2026A",
            course_code="C-OUTSIDE",
            course_name="无关教学班课程（示例）",
            class_code="TC-OUTSIDE",
            class_name="无关教学班（示例）",
        )
        factories.make_rule(
            other_offering,
            generated_demo.classrooms["B201"],
            (outsider,),
            rule_code="R-OUTSIDE-MON",
            weekday=1,
            start_period=5,
            end_period=6,
            weeks=(8,),
        )
        generate_sessions_for_term(generated_demo.term)
        outside_session = ClassSession.objects.get(rule__rule_code="R-OUTSIDE-MON")

        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "outside-scope@example.invalid"))
        list_response = _week_sessions(api_client)
        assert list_response.status_code == 200
        assert all(item["id"] != str(outside_session.id) for item in list_response.data["results"])  # type: ignore[index]

        detail_response = api_client.get(f"/api/v1/sessions/{outside_session.id}/")
        assert detail_response.status_code == 404

        offering_response = _week_sessions(api_client, scope="offering", offering_id=other_offering.id)
        assert offering_response.status_code == 404

        class_response = _week_sessions(
            api_client,
            scope="teaching_class",
            teaching_class_id=other_offering.teaching_class_id,
        )
        assert class_response.status_code == 404


class TestCancelledAndMoved:
    def test_replace_source_sessions_are_moved_to_saturday_week_5(self, api_client, generated_demo) -> None:
        """Actual B11 data: 10-05 sessions move to 10-10; 10-05 itself is empty.

        The task prose said the two 10-05 rows were cancelled.  Generation keeps
        one row per stable generation key and a replace moves that row, so the
        source day has no row to mark cancelled.  The API exposes the real
        contract (actual date, origin date, teaching_week and is_moved).
        """
        _authenticate(api_client, _link_user(generated_demo.teachers["0001"], "moved-week@example.invalid"))

        response = _week_sessions(
            api_client,
            **{"from": "2026-10-05", "to": "2026-10-12"},
        )

        assert response.status_code == 200
        results = response.data["results"]  # type: ignore[index]
        assert len(results) == 2
        assert {item["date"] for item in results} == {"2026-10-10"}
        assert all(item["weekday"] == 6 for item in results)
        assert all(item["teaching_week"] == 5 for item in results)
        assert all(item["is_moved"] is True for item in results)
        assert all(item["origin_teaching_date"] == "2026-10-05" for item in results)
        assert all(item["status"] == "scheduled" for item in results)
        assert all(item["start_at"].startswith("2026-10-10T") for item in results)

    def test_include_cancelled_false_excludes_cancelled_sessions(self, api_client, generated_demo) -> None:
        cancelled = _source_session(generated_demo, "R-DATA-MON", date(2026, 10, 19))
        cancelled.status = SessionStatus.CANCELLED
        cancelled.actual_hours = Decimal("0")
        cancelled.status_reason = "虚构测试停课"
        cancelled.save(update_fields=["status", "actual_hours", "status_reason", "updated_at"])

        _authenticate(api_client, _link_user(generated_demo.teachers["0001"], "cancelled-filter@example.invalid"))
        params = {"from": "2026-10-19", "to": "2026-10-26"}
        with_cancelled = api_client.get(_session_url(params))
        without_cancelled = api_client.get(_session_url({**params, "include_cancelled": "false"}))

        assert with_cancelled.status_code == 200
        assert any(item["id"] == str(cancelled.id) for item in with_cancelled.data["results"])  # type: ignore[index]
        assert all(item["id"] != str(cancelled.id) for item in without_cancelled.data["results"])  # type: ignore[index]


class TestAuthenticationAndTeacherStatus:
    def test_unauthenticated_is_401(self, api_client, generated_demo) -> None:
        response = _week_sessions(api_client)

        assert response.status_code == 401
        assert response["WWW-Authenticate"].startswith("Bearer")

    def test_unlinked_account_is_403_teacher_not_linked(self, api_client, generated_demo) -> None:
        user = get_user_model().objects.create_user(email="no-teacher@example.invalid", password="test-pass-123")
        _authenticate(api_client, user)

        response = _week_sessions(api_client)

        assert response.status_code == 403
        assert response.data["code"] == "TEACHER_NOT_LINKED"  # type: ignore[index]
        assert response.data["field_errors"] == {}  # type: ignore[index]

    def test_disabled_teacher_profile_is_403_teacher_disabled(self, api_client, generated_demo) -> None:
        teacher = generated_demo.teachers["0002"]
        user = _link_user(teacher, "disabled-profile@example.invalid")
        teacher.is_active = False
        teacher.teacher_status = TeacherStatus.DISABLED
        teacher.save(update_fields=["is_active", "teacher_status", "updated_at"])
        _authenticate(api_client, user)

        response = _week_sessions(api_client)

        assert response.status_code == 403
        assert response.data["code"] == "TEACHER_DISABLED"  # type: ignore[index]
        assert response.data["field_errors"] == {}  # type: ignore[index]


class TestQueryValidation:
    @pytest.mark.parametrize(
        "params",
        [
            {"from": "2026-10-26"},
            {"to": "2026-11-02"},
            {},
            {"from": "2026-10-26", "to": "2026-10-26"},
            {"from": "2026-10-26", "to": "2026-10-20"},
            {"from": "2026-10-26", "to": "2026-12-08"},
        ],
    )
    def test_invalid_date_ranges_return_400_invalid_date_range(
        self,
        api_client,
        generated_demo,
        params,
    ) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "invalid-range@example.invalid"))

        response = api_client.get(_session_url(params))

        assert response.status_code == 400
        assert response.data["code"] == "INVALID_DATE_RANGE"  # type: ignore[index]
        assert response.data["detail"]  # type: ignore[index]
        assert response.data["field_errors"]  # type: ignore[index]

    def test_offering_scope_without_offering_id_returns_400(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "missing-offering@example.invalid"))

        response = _week_sessions(api_client, scope="offering")

        assert response.status_code == 400
        assert response.data["code"] == "VALIDATION_ERROR"  # type: ignore[index]
        assert "offering_id" in response.data["field_errors"]  # type: ignore[index]

    def test_teaching_class_scope_without_id_returns_400(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "missing-class@example.invalid"))

        response = _week_sessions(api_client, scope="teaching_class")

        assert response.status_code == 400
        assert response.data["code"] == "VALIDATION_ERROR"  # type: ignore[index]
        assert "teaching_class_id" in response.data["field_errors"]  # type: ignore[index]


class TestSessionDetail:
    def test_detail_has_hours_enrolled_count_and_offering_metadata(self, api_client, generated_demo) -> None:
        session = _source_session(generated_demo, "R-CRIME-EVEN", date(2026, 10, 26))
        teacher = generated_demo.teachers["0002"]
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
        # Withdrawn at the start of 10-26: [from, until) means this student no
        # longer counts on that day.
        Enrollment.objects.create(
            offering=session.offering,
            student=generated_demo.students["000003"],
            effective_from=date(2026, 9, 7),
            effective_until=date(2026, 10, 26),
        )
        _authenticate(api_client, _link_user(teacher, "detail@example.invalid"))

        response = api_client.get(f"/api/v1/sessions/{session.id}/")

        assert response.status_code == 200
        body = response.data
        assert body["planned_hours"] == "2.0"  # type: ignore[index]
        assert body["actual_hours"] == "2.0"  # type: ignore[index]
        assert body["completed_at"].endswith("+08:00")  # type: ignore[index]
        assert body["enrolled_count"] == 2  # type: ignore[index]
        assert body["offering"]["target_hours"] == "40.0"  # type: ignore[index]
        assert body["offering"]["lead_teacher"] == {  # type: ignore[index]
            "id": str(generated_demo.teachers["0001"].id),
            "name": "示例教师甲",
        }
        assert body["term"] == {  # type: ignore[index]
            "id": str(generated_demo.term.id),
            "code": generated_demo.term.code,
            "schedule_version": generated_demo.term.schedule_version,
        }
        assert body["teachers"] == [{"id": str(teacher.id), "name": "示例教师乙"}]  # type: ignore[index]
        assert body["is_mine"] is True  # type: ignore[index]


class TestCalendarAndTerms:
    def test_terms_mark_today_as_current_in_school_timezone(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "terms@example.invalid"))
        fixed_now = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        with patch("django.utils.timezone.now", return_value=fixed_now):
            response = api_client.get("/api/v1/terms/")

        assert response.status_code == 200
        assert len(response.data) == 1  # type: ignore[arg-type]
        body = response.data[0]  # type: ignore[index]
        assert body == {
            "id": str(generated_demo.term.id),
            "code": generated_demo.term.code,
            "name": generated_demo.term.name,
            "start_date": "2026-09-07",
            "end_date": "2027-01-24",
            "first_week_monday": "2026-09-07",
            "teaching_week_count": 20,
            "timezone": "Asia/Shanghai",
            "schedule_version": generated_demo.term.schedule_version,
            "is_current": True,
        }

    def test_calendar_has_periods_exceptions_and_twenty_weeks(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "calendar@example.invalid"))

        response = api_client.get(f"/api/v1/terms/{generated_demo.term.id}/calendar/")

        assert response.status_code == 200
        body = response.data
        assert len(body["periods"]) == 8  # type: ignore[index]
        assert body["periods"][0] == {  # type: ignore[index]
            "period_number": 1,
            "start_time": "08:00",
            "end_time": "08:45",
            "block_code": "morning",
        }
        assert len(body["exceptions"]) == 2  # type: ignore[index]
        assert {item["kind"] for item in body["exceptions"]} == {"cancel", "replace"}  # type: ignore[index]
        assert len(body["weeks"]) == 20  # type: ignore[index]
        assert body["weeks"][7] == {  # type: ignore[index]
            "week": 8,
            "monday": "2026-10-26",
            "sunday": "2026-11-01",
        }
        assert body["weeks"][0]["monday"] == "2026-09-07"  # type: ignore[index]
        assert body["weeks"][19]["sunday"] == "2027-01-24"  # type: ignore[index]

    def test_range_outside_every_term_returns_null_term_and_empty_results(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "outside-term@example.invalid"))

        response = api_client.get(_session_url({"from": "2030-01-01", "to": "2030-01-02"}))

        assert response.status_code == 200
        assert response.data == {
            "from": "2030-01-01",
            "to": "2030-01-02",
            "term": None,
            "results": [],
        }


class TestOfferings:
    def test_offerings_are_paginated_and_mark_same_class_assignments(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "offerings@example.invalid"))
        crime = generated_demo.offerings["OFF-CRIME-2026A"]
        data = generated_demo.offerings["OFF-DATA-2026A"]

        response = api_client.get("/api/v1/offerings/", {"term_id": str(generated_demo.term.id)})

        assert response.status_code == 200
        body = response.data
        assert set(body) == {"count", "next", "previous", "results"}  # type: ignore[arg-type]
        assert body["count"] == 2  # type: ignore[index]
        by_id = {item["id"]: item for item in body["results"]}  # type: ignore[index]
        crime_row = by_id[str(crime.id)]
        data_row = by_id[str(data.id)]
        assert crime_row["is_assigned"] is True
        assert data_row["is_assigned"] is False
        assert crime_row["target_hours"] == "40.0"
        assert crime_row["lead_teacher"] == {
            "id": str(generated_demo.teachers["0001"].id),
            "name": "示例教师甲",
        }
        assert crime_row["teachers"] == [
            {"id": str(generated_demo.teachers["0001"].id), "name": "示例教师甲", "role": "lead"},
            {"id": str(generated_demo.teachers["0002"].id), "name": "示例教师乙", "role": "teacher"},
        ]

    def test_offerings_exclude_unrelated_teaching_classes(self, api_client, generated_demo) -> None:
        outsider = factories.make_teacher(generated_demo.school, "0003", "无关示例教师")
        outside_offering = factories.make_offering(
            generated_demo.term,
            outsider,
            offering_code="OFF-HIDDEN-2026A",
            course_code="C-HIDDEN",
            course_name="不可见课程（示例）",
            class_code="TC-HIDDEN",
            class_name="不可见教学班（示例）",
        )
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "offerings-hidden@example.invalid"))

        response = api_client.get("/api/v1/offerings/", {"term_id": str(generated_demo.term.id)})

        assert response.status_code == 200
        assert response.data["count"] == 2  # type: ignore[index]
        assert all(row["id"] != str(outside_offering.id) for row in response.data["results"])  # type: ignore[index]

    def test_offerings_default_to_current_term_and_empty_without_one(self, api_client, generated_demo) -> None:
        _authenticate(api_client, _link_user(generated_demo.teachers["0002"], "offerings-current@example.invalid"))
        fixed_now = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        with patch("django.utils.timezone.now", return_value=fixed_now):
            current_response = api_client.get("/api/v1/offerings/")

        assert current_response.status_code == 200
        assert current_response.data["count"] == 2  # type: ignore[index]

        with patch(
            "django.utils.timezone.now",
            return_value=datetime(2030, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ):
            empty_response = api_client.get("/api/v1/offerings/")

        assert empty_response.status_code == 200
        assert empty_response.data == {"count": 0, "next": None, "previous": None, "results": []}

    def test_offerings_schema_documents_term_id_summary_and_description(self, api_client) -> None:
        response = api_client.get("/api/v1/schema/", {"format": "json"})

        assert response.status_code == 200
        operation = response.json()["paths"]["/api/v1/offerings/"]["get"]
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert {"page", "term_id"} <= set(parameters)
        assert parameters["term_id"]["in"] == "query"
        assert parameters["term_id"]["schema"] == {"type": "string", "format": "uuid"}
        assert operation["summary"] == "本人可见的课程开设"
        assert "当前学期" in operation["description"]
        response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert response_ref.endswith("/PaginatedOfferingList")


class TestAssignmentValidity:
    FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def test_expired_assignment_loses_class_and_offerings_but_keeps_own_sessions(
        self,
        api_client,
        generated_demo,
    ) -> None:
        teacher = generated_demo.teachers["0002"]
        crime = generated_demo.offerings["OFF-CRIME-2026A"]
        data = generated_demo.offerings["OFF-DATA-2026A"]
        assignment = TeachingAssignment.objects.get(offering=crime, teacher=teacher)
        assignment.effective_until = date(2026, 10, 25)
        assignment.save(update_fields=["effective_until", "updated_at"])
        own_session = _source_session(generated_demo, "R-CRIME-EVEN", date(2026, 10, 26))
        _authenticate(api_client, _link_user(teacher, "expired-assignment@example.invalid"))

        with patch("django.utils.timezone.now", return_value=self.FIXED_NOW):
            class_response = _week_sessions(
                api_client,
                scope="teaching_class",
                teaching_class_id=data.teaching_class_id,
            )
            offerings_response = api_client.get(
                "/api/v1/offerings/",
                {"term_id": str(generated_demo.term.id)},
            )
            mine_response = _week_sessions(api_client)
            detail_response = api_client.get(f"/api/v1/sessions/{own_session.id}/")

        assert class_response.status_code == 404
        assert offerings_response.status_code == 200
        assert offerings_response.data["count"] == 0  # type: ignore[index]
        assert mine_response.status_code == 200
        assert [item["id"] for item in mine_response.data["results"]] == [str(own_session.id)]  # type: ignore[index]
        assert detail_response.status_code == 200
        assert detail_response.data["id"] == str(own_session.id)  # type: ignore[index]

    def test_expired_assignment_cannot_open_reschedule_options(self, api_client, generated_demo) -> None:
        teacher = generated_demo.teachers["0002"]
        crime = generated_demo.offerings["OFF-CRIME-2026A"]
        assignment = TeachingAssignment.objects.get(offering=crime, teacher=teacher)
        assignment.effective_until = date(2026, 10, 25)
        assignment.save(update_fields=["effective_until", "updated_at"])
        other_teacher_session = _source_session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        _authenticate(api_client, _link_user(teacher, "expired-reschedule@example.invalid"))

        with patch("django.utils.timezone.now", return_value=self.FIXED_NOW):
            response = api_client.post(
                f"/api/v1/sessions/{other_teacher_session.id}/reschedule-options/",
                {},
                format="json",
            )

        assert response.status_code == 403

    def test_future_effective_from_still_grants_assignment_visibility(self, api_client, generated_demo) -> None:
        teacher = generated_demo.teachers["0002"]
        crime = generated_demo.offerings["OFF-CRIME-2026A"]
        data = generated_demo.offerings["OFF-DATA-2026A"]
        assignment = TeachingAssignment.objects.get(offering=crime, teacher=teacher)
        assignment.effective_from = date(2026, 11, 1)
        assignment.effective_until = None
        assignment.save(update_fields=["effective_from", "effective_until", "updated_at"])
        _authenticate(api_client, _link_user(teacher, "future-assignment@example.invalid"))

        with patch("django.utils.timezone.now", return_value=self.FIXED_NOW):
            class_response = _week_sessions(
                api_client,
                scope="teaching_class",
                teaching_class_id=data.teaching_class_id,
            )
            offerings_response = api_client.get(
                "/api/v1/offerings/",
                {"term_id": str(generated_demo.term.id)},
            )

        assert class_response.status_code == 200
        assert {item["course_code"] for item in class_response.data["results"]} == {"C-CRIME", "C-DATA"}  # type: ignore[index]
        assert offerings_response.status_code == 200
        assert offerings_response.data["count"] == 2  # type: ignore[index]
        crime_row = next(item for item in offerings_response.data["results"] if item["id"] == str(crime.id))  # type: ignore[index]
        assert crime_row["is_assigned"] is True

    def test_is_assigned_uses_the_same_active_assignment_window(self, api_client, generated_demo) -> None:
        teacher = generated_demo.teachers["0002"]
        crime = generated_demo.offerings["OFF-CRIME-2026A"]
        data = generated_demo.offerings["OFF-DATA-2026A"]
        expired = TeachingAssignment.objects.get(offering=crime, teacher=teacher)
        expired.effective_until = date(2026, 10, 25)
        expired.save(update_fields=["effective_until", "updated_at"])
        TeachingAssignment.objects.create(
            offering=data,
            teacher=teacher,
            role="teacher",
            effective_from=generated_demo.term.start_date,
            effective_until=None,
        )
        _authenticate(api_client, _link_user(teacher, "active-flag@example.invalid"))

        with patch("django.utils.timezone.now", return_value=self.FIXED_NOW):
            response = api_client.get("/api/v1/offerings/", {"term_id": str(generated_demo.term.id)})

        assert response.status_code == 200
        by_id = {item["id"]: item for item in response.data["results"]}  # type: ignore[index]
        assert by_id[str(crime.id)]["is_assigned"] is False
        assert by_id[str(data.id)]["is_assigned"] is True
