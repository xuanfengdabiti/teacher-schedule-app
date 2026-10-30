"""B31 reschedule requests, approvals and formal changes (A08/A09/A15/A19).

All service calls use the fixed fictional clock 2026-10-26 07:00 +08:00.  HTTP
tests patch the view's ``timezone.now`` for the same determinism as B30 tests.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import CoverageCompleteness, CoverageKind, DataCoverage, TeacherStatus
from apps.notifications.models import Notification, NotificationType
from apps.notifications.services import notify
from apps.scheduling.changes import ScheduleChangeError, apply_session_change
from apps.scheduling.models import (
    ChangeEntityType,
    ChangeLog,
    ClassSession,
    RescheduleRequest,
    RescheduleStatus,
    SchoolSyncState,
    SessionChange,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.requests import (
    RescheduleRequestError,
    approve_request,
    create_request,
    force_approve_request,
    reject_request,
    withdraw_request,
)
from apps.scheduling.reschedule_options import (
    ELIGIBILITY_AVAILABLE,
    ELIGIBILITY_CONFLICT,
    ELIGIBILITY_UNVERIFIED,
    OptionQuery,
    find_reschedule_options,
)
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 26, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
TARGET_DATE = date(2026, 10, 27)
PASSWORD = "test-pass-123"


@pytest.fixture
def generated_demo(demo_term):
    generate_sessions_for_term(demo_term.term)
    return demo_term


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _add_school_coverage(
    term,
    *,
    kinds: tuple[str, ...] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[DataCoverage]:
    selected_kinds = kinds or tuple(CoverageKind.values)
    from_date = start or term.start_date
    until_date = end or term.end_date + timedelta(days=1)
    rows = []
    for kind in selected_kinds:
        rows.append(
            DataCoverage.objects.create(
                school=term.school,
                term=term,
                kind=kind,
                scope_key="school",
                from_date=from_date,
                until_date=until_date,
                completeness=CoverageCompleteness.COMPLETE,
                source_updated_at=NOW,
                confirmation_ref="B31-TEST-CONFIRMATION",
            )
        )
    return rows


def _source(demo, rule_code: str, day: date) -> ClassSession:
    return ClassSession.objects.select_related(
        "offering__term",
        "offering__course",
        "offering__teaching_class",
        "classroom",
    ).get(rule=demo.rules[rule_code], origin_teaching_date=day)


def _link_user(teacher, email: str):
    user = get_user_model().objects.create_user(email=email, password=PASSWORD)
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user


def _staff_user(email: str = "staff@example.invalid"):
    return get_user_model().objects.create_superuser(email=email, password=PASSWORD)


def _authenticate(client: APIClient, user) -> None:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def _target_payload(
    session: ClassSession,
    *,
    day: date = TARGET_DATE,
    start_period: int = 5,
    end_period: int = 6,
    classroom,
    submit: bool = True,
) -> dict:
    return {
        "session_id": str(session.id),
        "target_date": day.isoformat(),
        "target_start_period": start_period,
        "target_end_period": end_period,
        "target_classroom_id": str(classroom.id),
        "reason": "因病需要调整到另一时段（虚构测试）",
        "submit": submit,
    }


def _post_request(client: APIClient, payload: dict):
    with patch("apps.scheduling.views.timezone.now", return_value=NOW):
        return client.post("/api/v1/reschedule-requests/", payload, format="json")


def _create_service_request(
    demo,
    source: ClassSession,
    *,
    applicant,
    day: date,
    start_period: int,
    end_period: int,
    classroom,
    submit: bool = True,
) -> RescheduleRequest:
    return create_request(
        applicant,
        source,
        target_date=day,
        target_start_period=start_period,
        target_end_period=end_period,
        target_classroom=classroom,
        reason="虚构调课说明",
        submit=submit,
        now=NOW,
    )


class TestA09FormalEffect:
    def test_submit_keeps_timetable_and_approval_moves_same_session(self, generated_demo, api_client) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        teacher = demo.teachers["0002"]
        _authenticate(api_client, _link_user(teacher, "a09@example.invalid"))
        target_classroom = demo.classrooms["B201"]

        with patch("apps.scheduling.views.timezone.now", return_value=NOW):
            response = api_client.post(
                "/api/v1/reschedule-requests/",
                _target_payload(source, classroom=target_classroom),
                format="json",
            )
        assert response.status_code == 201
        request_id = response.data["id"]
        assert response.data["status"] == RescheduleStatus.SUBMITTED

        before_response = api_client.get(
            "/api/v1/sessions/",
            {"from": "2026-10-26", "to": "2026-10-27"},
        )
        assert before_response.status_code == 200
        assert str(source.id) in {item["id"] for item in before_response.data["results"]}
        source.refresh_from_db()
        assert source.start_at.astimezone(ZoneInfo(demo.term.timezone)).date() == date(2026, 10, 26)

        old_version = source.version
        old_schedule_version = demo.term.schedule_version
        cursor = SchoolSyncState.objects.get(school=demo.school).last_seq
        request_obj = RescheduleRequest.objects.get(pk=request_id)
        approved = approve_request(
            request_obj,
            _staff_user(),
            decision_ref="",
            note="教务核对通过",
            now=NOW,
        )
        assert approved.status == RescheduleStatus.APPROVED

        source.refresh_from_db()
        demo.term.refresh_from_db()
        assert source.id == request_obj.session_id
        assert source.version == old_version + 1
        assert demo.term.schedule_version == old_schedule_version + 1
        assert source.start_at.astimezone(ZoneInfo(demo.term.timezone)).date() == TARGET_DATE
        assert source.start_period == 5
        assert source.classroom_id == target_classroom.id

        change = SessionChange.objects.get(session=source)
        assert change.before_json["date"] == "2026-10-26"
        assert change.before_json["start_period"] == 1
        assert change.before_json["classroom_code"] == "A301"
        assert change.after_json["date"] == "2026-10-27"
        assert change.after_json["start_period"] == 5
        assert change.after_json["classroom_code"] == "B201"

        logs = list(ChangeLog.objects.filter(entity_id__in=[source.id, demo.term.id]).order_by("change_seq"))
        assert any(log.entity_type == ChangeEntityType.SESSION and log.version == source.version for log in logs)
        assert any(
            log.entity_type == ChangeEntityType.TERM and log.version == demo.term.schedule_version for log in logs
        )

        after_response = api_client.get(
            "/api/v1/sessions/",
            {"from": "2026-10-27", "to": "2026-10-28"},
        )
        assert after_response.status_code == 200
        row = next(item for item in after_response.data["results"] if item["id"] == str(source.id))
        assert row["start_period"] == 5
        assert row["classroom"]["classroom_code"] == "B201"
        assert row["version"] == old_version + 1

        old_date_response = api_client.get(
            "/api/v1/sessions/",
            {"from": "2026-10-26", "to": "2026-10-27"},
        )
        assert str(source.id) not in {item["id"] for item in old_date_response.data["results"]}

        sync_response = api_client.get("/api/v1/sync/changes/", {"cursor": str(cursor)})
        assert sync_response.status_code == 200
        assert str(source.id) in {item["id"] for item in sync_response.data["sessions"]}


class TestApprovalRechecksSource:
    def test_source_version_change_is_rechecked_and_can_still_be_approved(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        request_obj = _create_service_request(
            demo,
            source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        staff = _staff_user()
        apply_session_change(
            source.id,
            target_date=date(2026, 10, 28),
            start_period=5,
            end_period=6,
            classroom_id=demo.classrooms["A302"].id,
            actor=staff,
            reason="教务先行手工调整",
            approval_ref="MANUAL-001",
            now=NOW,
        )
        source.refresh_from_db()
        assert source.version == 2
        assert request_obj.base_session_version == 1

        approved = approve_request(request_obj, staff, decision_ref="", note="重新核对后批准", now=NOW)
        source.refresh_from_db()
        assert approved.status == RescheduleStatus.APPROVED
        assert source.version == 3
        assert source.start_at.astimezone(ZoneInfo(demo.term.timezone)).date() == TARGET_DATE
        assert source.classroom_id == demo.classrooms["B201"].id

    def test_source_no_longer_scheduled_returns_session_changed(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        request_obj = _create_service_request(
            demo,
            source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        source.status = SessionStatus.CANCELLED
        source.actual_hours = Decimal("0")
        source.save(update_fields=["status", "actual_hours", "updated_at"])

        with pytest.raises(ScheduleChangeError) as exc_info:
            approve_request(request_obj, _staff_user(), decision_ref="", note="课次已取消", now=NOW)
        assert exc_info.value.code == "SESSION_CHANGED"
        request_obj.refresh_from_db()
        assert request_obj.status == RescheduleStatus.SUBMITTED
        assert not SessionChange.objects.exists()


class TestA08Competition:
    def test_second_approval_is_blocked_and_keeps_request_submitted(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        target_room = demo.classrooms["B201"]
        crime_source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        data_source = _source(demo, "R-DATA-MON", date(2026, 10, 26))
        crime_request = _create_service_request(
            demo,
            crime_source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=target_room,
        )
        data_request = _create_service_request(
            demo,
            data_source,
            applicant=demo.teachers["0001"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=target_room,
        )
        assert crime_request.status == data_request.status == RescheduleStatus.SUBMITTED
        staff = _staff_user()

        approve_request(crime_request, staff, decision_ref="", note="先到先得", now=NOW)
        with pytest.raises(ScheduleChangeError) as exc_info:
            approve_request(data_request, staff, decision_ref="", note="后到", now=NOW)

        assert exc_info.value.code == "SCHEDULE_CONFLICT"
        data_request.refresh_from_db()
        data_source.refresh_from_db()
        assert data_request.status == RescheduleStatus.SUBMITTED
        assert data_request.check_summary["eligibility"] == ELIGIBILITY_CONFLICT
        assert any(conflict["category"] == "classroom" for conflict in data_request.check_summary["conflicts"])
        assert data_source.start_at.astimezone(ZoneInfo(demo.term.timezone)).date() == date(2026, 10, 26)


@pytest.mark.django_db(transaction=True)
def test_concurrent_approvals_for_same_room_have_exactly_one_winner(demo_term) -> None:
    generate_sessions_for_term(demo_term.term)
    demo = demo_term
    _add_school_coverage(demo.term)
    target_room = demo.classrooms["B201"]
    crime_source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
    data_source = _source(demo, "R-DATA-MON", date(2026, 10, 26))
    crime_request = _create_service_request(
        demo,
        crime_source,
        applicant=demo.teachers["0002"],
        day=TARGET_DATE,
        start_period=5,
        end_period=6,
        classroom=target_room,
    )
    data_request = _create_service_request(
        demo,
        data_source,
        applicant=demo.teachers["0001"],
        day=TARGET_DATE,
        start_period=5,
        end_period=6,
        classroom=target_room,
    )
    staff_id = _staff_user().id
    barrier = threading.Barrier(2)

    def worker(request_id):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            request_obj = RescheduleRequest.objects.get(pk=request_id)
            actor = get_user_model().objects.get(pk=staff_id)
            approve_request(request_obj, actor, decision_ref="", note="并发审批", now=NOW)
            return "approved"
        except ScheduleChangeError as exc:
            return exc.code
        except Exception as exc:  # noqa: BLE001 - surfaced in the assertion below
            return f"unexpected:{exc!r}"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=30)
            for future in (executor.submit(worker, crime_request.id), executor.submit(worker, data_request.id))
        ]

    assert outcomes.count("approved") == 1, outcomes
    statuses = list(RescheduleRequest.objects.order_by("id").values_list("status", flat=True))
    assert statuses.count(RescheduleStatus.APPROVED) == 1
    assert statuses.count(RescheduleStatus.SUBMITTED) == 1


class TestSubmissionRules:
    def test_conflict_cannot_submit_but_can_be_draft(self, generated_demo, api_client) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        _authenticate(api_client, _link_user(demo.teachers["0002"], "conflict@example.invalid"))
        payload = _target_payload(
            source,
            day=date(2026, 10, 26),
            start_period=3,
            end_period=4,
            classroom=demo.classrooms["B201"],
            submit=True,
        )

        response = _post_request(api_client, payload)
        assert response.status_code == 409
        assert response.data["code"] == "RESCHEDULE_CONFLICT"
        assert response.data["evaluation"]["eligibility"] == ELIGIBILITY_CONFLICT
        assert response.data["evaluation"]["conflicts"]
        assert not RescheduleRequest.objects.exists()

        payload["submit"] = False
        response = _post_request(api_client, payload)
        assert response.status_code == 201
        draft = RescheduleRequest.objects.get()
        assert draft.status == RescheduleStatus.DRAFT
        assert draft.check_summary["eligibility"] == ELIGIBILITY_CONFLICT

    def test_unverified_submits_and_requires_force_approval(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(
            demo.term, kinds=tuple(kind for kind in CoverageKind.values if kind != CoverageKind.STUDENT_SCHEDULE)
        )
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        request_obj = _create_service_request(
            demo,
            source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        assert request_obj.status == RescheduleStatus.SUBMITTED
        assert request_obj.needs_verification is True
        assert request_obj.check_summary["eligibility"] == ELIGIBILITY_UNVERIFIED

        staff = _staff_user()
        with pytest.raises(ScheduleChangeError) as exc_info:
            approve_request(request_obj, staff, decision_ref="", note="普通批准", now=NOW)
        assert exc_info.value.code == "DATA_UNVERIFIED"
        request_obj.refresh_from_db()
        assert request_obj.status == RescheduleStatus.SUBMITTED
        assert request_obj.check_summary["eligibility"] == ELIGIBILITY_UNVERIFIED

        approved = force_approve_request(
            request_obj,
            staff,
            decision_ref="SCHOOL-FORCED-2026-001",
            note="学校书面批准",
            now=NOW,
        )
        assert approved.status == RescheduleStatus.APPROVED
        assert approved.forced_override is True
        change = SessionChange.objects.get(request=approved)
        assert change.forced_override is True
        assert change.approval_ref == "SCHOOL-FORCED-2026-001"
        assert change.after_json["risk"]["forced_override"] is True
        assert change.after_json["risk"]["evaluation"]["eligibility"] == ELIGIBILITY_UNVERIFIED
        result_notification = Notification.objects.get(
            recipient=demo.teachers["0002"],
            event_key=f"reschedule_request:{approved.id}:approved",
        )
        assert "学校强制安排" in result_notification.body
        assert "SCHOOL-FORCED-2026-001" in result_notification.body

    def test_force_approval_requires_school_reference(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        request_obj = _create_service_request(
            demo,
            source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        with pytest.raises(RescheduleRequestError) as exc_info:
            force_approve_request(
                request_obj,
                _staff_user(),
                decision_ref="   ",
                note="没有编号",
                now=NOW,
            )
        assert exc_info.value.code == "APPROVAL_REF_REQUIRED"
        request_obj.refresh_from_db()
        assert request_obj.status == RescheduleStatus.SUBMITTED
        assert not SessionChange.objects.exists()

    def test_pending_request_blocks_another_submit_but_not_a_draft(self, generated_demo, api_client) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        teacher = demo.teachers["0002"]
        _authenticate(api_client, _link_user(teacher, "pending@example.invalid"))
        _create_service_request(
            demo,
            source,
            applicant=teacher,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )

        response = _post_request(
            api_client,
            _target_payload(source, classroom=demo.classrooms["B201"]),
        )
        assert response.status_code == 409
        assert response.data["code"] == "REQUEST_ALREADY_PENDING"

        response = _post_request(
            api_client,
            _target_payload(source, classroom=demo.classrooms["A302"], submit=False),
        )
        assert response.status_code == 201
        draft_id = response.data["id"]
        with patch("apps.scheduling.views.timezone.now", return_value=NOW):
            response = api_client.post(f"/api/v1/reschedule-requests/{draft_id}/submit/", {}, format="json")
        assert response.status_code == 409
        assert response.data["code"] == "REQUEST_ALREADY_PENDING"


class TestWithdrawalRejection:
    def test_draft_submit_and_withdraw_via_api(self, generated_demo, api_client) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        _authenticate(api_client, _link_user(demo.teachers["0002"], "api-transitions@example.invalid"))

        response = _post_request(
            api_client,
            _target_payload(source, classroom=demo.classrooms["B201"], submit=False),
        )
        assert response.status_code == 201
        assert response.data["status"] == RescheduleStatus.DRAFT
        assert response.data["can_withdraw"] is True
        request_id = response.data["id"]

        with patch("apps.scheduling.views.timezone.now", return_value=NOW):
            response = api_client.post(f"/api/v1/reschedule-requests/{request_id}/submit/", {}, format="json")
        assert response.status_code == 200
        assert response.data["status"] == RescheduleStatus.SUBMITTED

        response = api_client.post(f"/api/v1/reschedule-requests/{request_id}/withdraw/", {}, format="json")
        assert response.status_code == 200
        assert response.data["status"] == RescheduleStatus.WITHDRAWN
        assert response.data["can_withdraw"] is False

        response = api_client.post(f"/api/v1/reschedule-requests/{request_id}/withdraw/", {}, format="json")
        assert response.status_code == 409
        assert response.data["code"] == "REQUEST_NOT_WITHDRAWABLE"

    def test_withdraw_rules_and_non_applicant(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        applicant = demo.teachers["0002"]
        draft = _create_service_request(
            demo,
            source,
            applicant=applicant,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
            submit=False,
        )
        assert withdraw_request(draft, applicant).status == RescheduleStatus.WITHDRAWN

        submitted = _create_service_request(
            demo,
            source,
            applicant=applicant,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        with pytest.raises(RescheduleRequestError) as exc_info:
            withdraw_request(submitted, demo.teachers["0001"])
        assert exc_info.value.status_code == 403
        assert withdraw_request(submitted, applicant).status == RescheduleStatus.WITHDRAWN

    def test_approved_request_cannot_be_withdrawn(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        applicant = demo.teachers["0002"]
        request_obj = _create_service_request(
            demo,
            source,
            applicant=applicant,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        approve_request(request_obj, _staff_user(), decision_ref="", note="通过", now=NOW)
        with pytest.raises(RescheduleRequestError) as exc_info:
            withdraw_request(request_obj, applicant)
        assert exc_info.value.code == "REQUEST_NOT_WITHDRAWABLE"
        assert exc_info.value.status_code == 409

    def test_rejection_keeps_timetable_and_notifies_applicant(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        applicant = demo.teachers["0002"]
        request_obj = _create_service_request(
            demo,
            source,
            applicant=applicant,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        original_start = source.start_at
        rejected = reject_request(request_obj, _staff_user(), note="教务未批准", now=NOW)

        source.refresh_from_db()
        assert rejected.status == RescheduleStatus.REJECTED
        assert source.start_at == original_start
        assert not SessionChange.objects.exists()
        notification = Notification.objects.get(
            recipient=applicant,
            event_key=f"reschedule_request:{request_obj.id}:rejected",
        )
        assert notification.type == NotificationType.RESCHEDULE_RESULT
        assert "驳回" in notification.title


class TestA15ReleasedOldRoom:
    def test_old_room_slot_becomes_available_after_move(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        crime_source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        data_source = _source(demo, "R-DATA-MON", date(2026, 10, 26))
        query = OptionQuery(
            date_from=date(2026, 10, 26),
            date_until=date(2026, 10, 27),
            allowed_block_codes={"morning"},
            classroom_ids=[demo.classrooms["A301"].id],
        )

        before = find_reschedule_options(data_source, query, now=NOW)
        old_room_before = next(
            option for option in before.options if option.start_period == 1 and option.classroom_code == "A301"
        )
        assert old_room_before.eligibility == ELIGIBILITY_CONFLICT
        assert any(conflict.category == "classroom" for conflict in old_room_before.conflicts)

        request_obj = _create_service_request(
            demo,
            crime_source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["A302"],
        )
        approve_request(request_obj, _staff_user(), decision_ref="", note="调到 A302", now=NOW)

        after = find_reschedule_options(data_source, query, now=NOW)
        old_room_after = next(
            option for option in after.options if option.start_period == 1 and option.classroom_code == "A301"
        )
        assert old_room_after.eligibility == ELIGIBILITY_AVAILABLE
        assert old_room_after.conflicts == ()


class TestA19Notifications:
    def test_each_session_teacher_and_applicant_get_deduplicated_messages(self, generated_demo) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        SessionTeacher.objects.get_or_create(session=source, teacher=demo.teachers["0001"])
        applicant = demo.teachers["0002"]
        request_obj = _create_service_request(
            demo,
            source,
            applicant=applicant,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        approve_request(request_obj, _staff_user(), decision_ref="", note="通过", now=NOW)
        change = SessionChange.objects.get(request=request_obj)

        expected_teachers = set(SessionTeacher.objects.filter(session=source).values_list("teacher_id", flat=True))
        changed_notifications = Notification.objects.filter(
            event_key=f"session_change:{change.id}",
            type=NotificationType.SCHEDULE_CHANGED,
        )
        assert set(changed_notifications.values_list("recipient_id", flat=True)) == expected_teachers
        assert changed_notifications.count() == len(expected_teachers)
        result_notification = Notification.objects.get(
            recipient=applicant,
            event_key=f"reschedule_request:{request_obj.id}:approved",
            type=NotificationType.RESCHEDULE_RESULT,
        )
        assert "B201" in result_notification.body
        assert "2026-10-27" in result_notification.body

        before_count = Notification.objects.count()
        with transaction.atomic():
            for teacher in [demo.teachers["0001"], demo.teachers["0002"]]:
                notify(
                    teacher,
                    type=NotificationType.SCHEDULE_CHANGED,
                    object_type="session",
                    object_id=source.id,
                    title="课表调整已生效",
                    body="重试不应新增",
                    event_key=f"session_change:{change.id}",
                )
            notify(
                applicant,
                type=NotificationType.RESCHEDULE_RESULT,
                object_type="reschedule_request",
                object_id=request_obj.id,
                title="调课申请已批准",
                body="重试不应新增",
                event_key=f"reschedule_request:{request_obj.id}:approved",
            )
        assert Notification.objects.count() == before_count


class TestHardConditions:
    @pytest.mark.parametrize("condition", ["cross_noon", "past", "calendar_exception", "capacity", "equipment"])
    def test_hard_conditions_return_code_and_do_not_save(self, generated_demo, api_client, condition: str) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        _authenticate(api_client, _link_user(demo.teachers["0002"], f"hard-{condition}@example.invalid"))
        target_day = TARGET_DATE
        start_period = 5
        end_period = 6
        classroom = demo.classrooms["B201"]

        if condition == "cross_noon":
            start_period, end_period = 4, 5
        elif condition == "past":
            target_day = date(2026, 10, 23)
            start_period, end_period = 1, 2
        elif condition == "calendar_exception":
            target_day = date(2026, 10, 28)
            start_period, end_period = 1, 2
            factories.make_exception(demo.term, date=target_day, kind="cancel", reason="虚构停课")
        elif condition == "capacity":
            classroom.capacity = 1
            classroom.save(update_fields=["capacity", "updated_at"])
        elif condition == "equipment":
            classroom.equipment = []
            classroom.save(update_fields=["equipment", "updated_at"])

        response = _post_request(
            api_client,
            _target_payload(
                source,
                day=target_day,
                start_period=start_period,
                end_period=end_period,
                classroom=classroom,
            ),
        )
        assert response.status_code == 409
        assert response.data["code"] == "TARGET_NOT_ALLOWED"
        assert response.data["detail"]
        assert not RescheduleRequest.objects.exists()


class TestPermissionsAndScopes:
    def test_create_permissions_and_related_scope(self, generated_demo, api_client) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        payload = _target_payload(source, classroom=demo.classrooms["B201"])

        unlinked_user = get_user_model().objects.create_user(
            email="unlinked-b31@example.invalid",
            password=PASSWORD,
        )
        unlinked_client = APIClient()
        _authenticate(unlinked_client, unlinked_user)
        response = _post_request(unlinked_client, payload)
        assert response.status_code == 403
        assert response.data["code"] == "TEACHER_NOT_LINKED"

        outsider_teacher = factories.make_teacher(demo.school, "0003", "外部示例教师")
        disabled_user = _link_user(outsider_teacher, "disabled-b31@example.invalid")
        outsider_teacher.is_active = False
        outsider_teacher.save(update_fields=["is_active", "updated_at"])
        disabled_client = APIClient()
        _authenticate(disabled_client, disabled_user)
        response = _post_request(disabled_client, payload)
        assert response.status_code == 403
        assert response.data["code"] == "TEACHER_DISABLED"

        outsider_teacher.is_active = True
        outsider_teacher.teacher_status = TeacherStatus.ACTIVE
        outsider_teacher.save(update_fields=["is_active", "teacher_status", "updated_at"])
        outsider_client = APIClient()
        _authenticate(outsider_client, disabled_user)
        response = _post_request(outsider_client, payload)
        assert response.status_code == 404

        applicant = demo.teachers["0002"]
        request_obj = _create_service_request(
            demo,
            source,
            applicant=applicant,
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        same_class_client = APIClient()
        _authenticate(same_class_client, _link_user(demo.teachers["0001"], "same-class@example.invalid"))
        response = same_class_client.get("/api/v1/reschedule-requests/", {"scope": "related"})
        assert response.status_code == 200
        assert [item["id"] for item in response.data["results"]] == [str(request_obj.id)]
        assert response.data["results"][0]["can_withdraw"] is False

        response = outsider_client.get("/api/v1/reschedule-requests/", {"scope": "related"})
        assert response.status_code == 200
        assert response.data["results"] == []

        response = outsider_client.post(
            f"/api/v1/reschedule-requests/{request_obj.id}/withdraw/",
            {},
            format="json",
        )
        assert response.status_code == 404


class TestSharedEvaluator:
    def test_evaluate_target_query_count_is_constant(self, generated_demo) -> None:
        from apps.scheduling.reschedule_options import evaluate_target

        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        classroom = demo.classrooms["B201"]

        with CaptureQueriesContext(connection) as first_queries:
            first = evaluate_target(
                source,
                start_period=5,
                end_period=6,
                day=TARGET_DATE,
                classroom=classroom,
                now=NOW,
            )
        with CaptureQueriesContext(connection) as second_queries:
            second = evaluate_target(
                source,
                start_period=5,
                end_period=6,
                day=date(2026, 10, 28),
                classroom=classroom,
                now=NOW,
            )

        assert first.eligibility == ELIGIBILITY_AVAILABLE
        assert second.eligibility == ELIGIBILITY_AVAILABLE
        assert len(first_queries) == len(second_queries)
        assert len(first_queries) <= 20


class TestAdminActions:
    def test_approve_action_uses_service_after_confirmation_page(self, generated_demo, client) -> None:
        demo = generated_demo
        _add_school_coverage(demo.term)
        source = _source(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        request_obj = _create_service_request(
            demo,
            source,
            applicant=demo.teachers["0002"],
            day=TARGET_DATE,
            start_period=5,
            end_period=6,
            classroom=demo.classrooms["B201"],
        )
        staff = _staff_user("admin-b31@example.invalid")
        client.force_login(staff)

        detail = client.get(f"/admin/scheduling/reschedulerequest/{request_obj.id}/change/")
        assert detail.status_code == 200
        detail_html = detail.content.decode()
        assert "检查摘要" in detail_html
        assert "源课次" in detail_html
        assert "目标安排" in detail_html

        first = client.post(
            "/admin/scheduling/reschedulerequest/",
            {
                "action": "approve_requests",
                "_selected_action": [str(request_obj.id)],
                "index": "0",
            },
        )
        assert first.status_code == 200
        assert "登记调课审批结果" in first.content.decode()

        with patch("apps.scheduling.admin.timezone.now", return_value=NOW):
            second = client.post(
                "/admin/scheduling/reschedulerequest/",
                {
                    "action": "approve_requests",
                    "_selected_action": [str(request_obj.id)],
                    "index": "0",
                    "reschedule_confirm": "1",
                    "decision_ref": "",
                    "decision_note": "Admin 确认",
                },
            )
        assert second.status_code == 302
        request_obj.refresh_from_db()
        assert request_obj.status == RescheduleStatus.APPROVED
        assert SessionChange.objects.filter(request=request_obj).exists()


class TestSchema:
    def test_reschedule_request_endpoints_are_in_schema(self, api_client) -> None:
        response = api_client.get("/api/v1/schema/", {"format": "json"})
        assert response.status_code == 200
        schema = response.json()
        assert "/api/v1/reschedule-requests/" in schema["paths"]
        assert "/api/v1/reschedule-requests/{request_id}/submit/" in schema["paths"]
        assert "/api/v1/reschedule-requests/{request_id}/withdraw/" in schema["paths"]
