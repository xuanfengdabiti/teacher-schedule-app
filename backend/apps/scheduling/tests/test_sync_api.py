"""Bootstrap/changes contract, snapshot and query-count tests (B14)."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import TeacherStatus
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import (
    ChangeEntityType,
    ChangeLog,
    ClassSession,
    SchoolSyncState,
)
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.sync import read_only_repeatable_snapshot
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db(transaction=True)

FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
OUTSIDE_NOW = datetime(2030, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


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


@pytest.fixture
def teacher_client(api_client, generated_demo):
    user = _link_user(generated_demo.teachers["0002"], "sync-0002@example.invalid")
    return _authenticate(api_client, user)


def _bootstrap(client: APIClient, now: datetime = FIXED_NOW):
    with patch("django.utils.timezone.now", return_value=now):
        return client.get("/api/v1/sync/bootstrap/")


def _changes(client: APIClient, params: dict | None = None, now: datetime = FIXED_NOW):
    with patch("django.utils.timezone.now", return_value=now):
        return client.get("/api/v1/sync/changes/", params or {})


class TestBootstrap:
    def test_returns_current_term_visible_offerings_and_all_visible_sessions(
        self,
        teacher_client,
        generated_demo,
    ) -> None:
        response = _bootstrap(teacher_client)

        assert response.status_code == 200
        body = response.data
        assert body["cursor"] == "41"
        assert body["assignment_scope_version"] == 1
        assert body["term"]["id"] == str(generated_demo.term.id)
        assert body["term"]["schedule_version"] == 2
        assert len(body["term"]["weeks"]) == 20
        assert body["generated_at"] == "2026-10-26T12:00:00+08:00"
        assert {row["id"] for row in body["offerings"]} == {
            str(generated_demo.offerings["OFF-CRIME-2026A"].id),
            str(generated_demo.offerings["OFF-DATA-2026A"].id),
        }
        assert len(body["sessions"]) == 40
        assert sum(1 for row in body["sessions"] if row["is_mine"]) == 10
        assert {row["course_code"] for row in body["sessions"]} == {"C-CRIME", "C-DATA"}
        assert body["sessions"][0]["start_at"].endswith("+08:00")

    def test_no_current_term_returns_null_term_and_empty_lists(self, teacher_client, generated_demo) -> None:
        response = _bootstrap(teacher_client, now=OUTSIDE_NOW)

        assert response.status_code == 200
        assert response.data["term"] is None
        assert response.data["offerings"] == []
        assert response.data["sessions"] == []
        assert response.data["cursor"] == "41"
        assert response.data["generated_at"] == "2030-01-01T12:00:00+08:00"

    def test_snapshot_sets_repeatable_read_before_any_data_query(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            with read_only_repeatable_snapshot():
                with connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_isolation")
                    assert cursor.fetchone()[0] == "repeatable read"
                    cursor.execute("SHOW transaction_read_only")
                    assert cursor.fetchone()[0] == "on"

        statements = [query["sql"] for query in captured.captured_queries]
        # Django 5.2 + psycopg 3 emits BEGIN when autocommit is switched off
        # on entering atomic(); older/other drivers may defer it.  Either way,
        # SET TRANSACTION must be the first statement before any data query.
        if statements[0] == "BEGIN":
            set_index = 1
        else:
            set_index = 0
        assert statements[set_index] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        assert not any(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements[:set_index])


class TestSyncPermissions:
    def test_unlinked_account_is_403_with_existing_code(self, api_client, generated_demo) -> None:
        user = get_user_model().objects.create_user(email="sync-unlinked@example.invalid", password="test-pass-123")
        _authenticate(api_client, user)

        bootstrap = _bootstrap(api_client)
        changes = _changes(api_client, {"cursor": "0"})

        assert bootstrap.status_code == 403
        assert bootstrap.data["code"] == "TEACHER_NOT_LINKED"
        assert changes.status_code == 403
        assert changes.data["code"] == "TEACHER_NOT_LINKED"

    def test_disabled_teacher_is_403_with_existing_code(self, api_client, generated_demo) -> None:
        teacher = generated_demo.teachers["0002"]
        user = _link_user(teacher, "sync-disabled@example.invalid")
        teacher.is_active = False
        teacher.teacher_status = TeacherStatus.DISABLED
        teacher.save(update_fields=["is_active", "teacher_status", "updated_at"])
        _authenticate(api_client, user)

        bootstrap = _bootstrap(api_client)
        changes = _changes(api_client, {"cursor": "0"})

        assert bootstrap.status_code == 403
        assert bootstrap.data["code"] == "TEACHER_DISABLED"
        assert changes.status_code == 403
        assert changes.data["code"] == "TEACHER_DISABLED"


class TestChanges:
    def test_new_visible_session_is_returned_invisible_session_is_filtered_and_cursor_advances(
        self,
        teacher_client,
        generated_demo,
    ) -> None:
        old_cursor = _bootstrap(teacher_client).data["cursor"]
        own_rule = factories.make_rule(
            generated_demo.offerings["OFF-CRIME-2026A"],
            generated_demo.classrooms["A301"],
            (generated_demo.teachers["0002"],),
            rule_code="R-CRIME-SYNC-NEW",
            weekday=3,
            start_period=5,
            end_period=6,
            weeks=(1,),
        )
        outsider = factories.make_teacher(generated_demo.school, "0003", "同步测试外班教师")
        outside_offering = factories.make_offering(
            generated_demo.term,
            outsider,
            offering_code="OFF-SYNC-OUTSIDE",
            course_code="C-SYNC-OUTSIDE",
            course_name="同步不可见课程",
            class_code="TC-SYNC-OUTSIDE",
            class_name="同步不可见教学班",
        )
        outside_rule = factories.make_rule(
            outside_offering,
            generated_demo.classrooms["B201"],
            (outsider,),
            rule_code="R-SYNC-OUTSIDE",
            weekday=3,
            start_period=7,
            end_period=8,
            weeks=(1,),
        )
        generate_sessions_for_term(generated_demo.term)
        own_session = ClassSession.objects.get(rule=own_rule)
        outside_session = ClassSession.objects.get(rule=outside_rule)
        last_seq = SchoolSyncState.objects.get(school=generated_demo.school).last_seq

        response = _changes(teacher_client, {"cursor": old_cursor})

        assert response.status_code == 200
        body = response.data
        assert body["reset_required"] is False
        assert body["next_cursor"] == str(last_seq) == "44"
        assert [row["id"] for row in body["sessions"]] == [str(own_session.id)]
        assert body["sessions"][0]["is_mine"] is True
        assert str(outside_session.id) not in {row["id"] for row in body["sessions"]}
        assert len(body["terms"]) == 1
        assert body["terms"][0]["id"] == str(generated_demo.term.id)
        assert body["terms"][0]["schedule_version"] == 3
        assert body["has_more"] is False

    def test_cursor_advances_when_the_only_log_is_invisible(self, teacher_client, generated_demo) -> None:
        outsider = factories.make_teacher(generated_demo.school, "0003", "游标过滤教师")
        outside_offering = factories.make_offering(
            generated_demo.term,
            outsider,
            offering_code="OFF-CURSOR-OUTSIDE",
            course_code="C-CURSOR-OUTSIDE",
            course_name="游标过滤课程",
            class_code="TC-CURSOR-OUTSIDE",
            class_name="游标过滤教学班",
        )
        with transaction.atomic():
            record_changes(
                generated_demo.school.id,
                [
                    ChangeEntry(
                        entity_type=ChangeEntityType.SESSION,
                        entity_id=uuid4(),
                        version=1,
                        offering_id=outside_offering.id,
                    )
                ],
            )

        response = _changes(teacher_client, {"cursor": "41"})

        assert response.status_code == 200
        assert response.data["sessions"] == []
        assert response.data["terms"] == []
        assert response.data["next_cursor"] == "42"
        assert response.data["has_more"] is False

    def test_same_entity_in_one_page_is_returned_once_with_current_state(self, teacher_client, generated_demo) -> None:
        session = ClassSession.objects.get(
            rule=generated_demo.rules["R-CRIME-EVEN"],
            origin_teaching_date=date(2026, 10, 26),
        )
        session.version = 2
        session.save(update_fields=["version", "updated_at"])
        entry = ChangeEntry(
            entity_type=ChangeEntityType.SESSION,
            entity_id=session.id,
            version=2,
            offering_id=session.offering_id,
        )
        with transaction.atomic():
            record_changes(generated_demo.school.id, [entry])
            record_changes(generated_demo.school.id, [entry])

        response = _changes(teacher_client, {"cursor": "41"})

        assert response.status_code == 200
        assert response.data["next_cursor"] == "43"
        assert len(response.data["sessions"]) == 1
        assert response.data["sessions"][0]["id"] == str(session.id)
        assert response.data["sessions"][0]["version"] == 2

    def test_visible_tombstone_is_returned_without_entity_details(self, teacher_client, generated_demo) -> None:
        session = ClassSession.objects.get(
            rule=generated_demo.rules["R-CRIME-EVEN"],
            origin_teaching_date=date(2026, 10, 26),
        )
        with transaction.atomic():
            record_changes(
                generated_demo.school.id,
                [
                    ChangeEntry(
                        entity_type=ChangeEntityType.SESSION,
                        entity_id=session.id,
                        version=2,
                        offering_id=session.offering_id,
                        tombstone=True,
                    )
                ],
            )

        response = _changes(teacher_client, {"cursor": "41"})

        assert response.status_code == 200
        assert response.data["sessions"] == [{"id": str(session.id), "tombstone": True}]
        assert response.data["next_cursor"] == "42"

    def test_term_log_is_serialized_as_current_calendar(self, teacher_client, generated_demo) -> None:
        response = _changes(teacher_client, {"cursor": "0"})

        assert response.status_code == 200
        assert response.data["next_cursor"] == "41"
        assert len(response.data["sessions"]) == 40
        assert len(response.data["terms"]) == 1
        term = response.data["terms"][0]
        assert term["id"] == str(generated_demo.term.id)
        assert term["schedule_version"] == 2
        assert len(term["periods"]) == 8
        assert len(term["weeks"]) == 20

    @pytest.mark.parametrize("params", [{"cursor": "abc"}, {"cursor": "-1"}, {"cursor": "42"}, {}])
    def test_invalid_or_out_of_range_cursor_requires_reset(self, teacher_client, generated_demo, params) -> None:
        response = _changes(teacher_client, params)

        assert response.status_code == 200
        assert response.data["reset_required"] is True
        assert response.data["sessions"] == []
        assert response.data["terms"] == []
        assert response.data["next_cursor"] == "41"

    def test_cursor_before_retained_logs_requires_reset(self, teacher_client, generated_demo) -> None:
        ChangeLog.objects.filter(school=generated_demo.school, change_seq__lte=10).delete()

        response = _changes(teacher_client, {"cursor": "5"})

        assert response.status_code == 200
        assert response.data["reset_required"] is True
        assert response.data["sessions"] == []
        assert response.data["terms"] == []

    def test_more_than_500_logs_are_paged_and_second_page_continues(self, teacher_client, generated_demo) -> None:
        with transaction.atomic():
            record_changes(
                generated_demo.school.id,
                [
                    ChangeEntry(
                        entity_type=ChangeEntityType.SESSION,
                        entity_id=uuid4(),
                        version=1,
                    )
                    for _ in range(600)
                ],
            )

        first = _changes(teacher_client, {"cursor": "41"})
        assert first.status_code == 200
        assert first.data["has_more"] is True
        assert first.data["next_cursor"] == "541"
        assert len(first.data["sessions"]) == 0

        second = _changes(teacher_client, {"cursor": first.data["next_cursor"]})
        assert second.status_code == 200
        assert second.data["has_more"] is False
        assert second.data["next_cursor"] == "641"
        assert len(second.data["sessions"]) == 0


class TestQueryCounts:
    def _bootstrap_query_count(self, client: APIClient) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = _bootstrap(client)
        assert response.status_code == 200
        return len(captured)

    def _changes_query_count(self, client: APIClient, cursor: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = _changes(client, {"cursor": cursor})
        assert response.status_code == 200
        return len(captured)

    def test_bootstrap_query_count_does_not_grow_with_session_count(self, teacher_client, generated_demo) -> None:
        before = self._bootstrap_query_count(teacher_client)

        for index in range(5):
            factories.make_rule(
                generated_demo.offerings["OFF-CRIME-2026A"],
                generated_demo.classrooms["A301"],
                (generated_demo.teachers["0002"],),
                rule_code=f"R-SYNC-QC-BOOT-{index}",
                weekday=4,
                start_period=3,
                end_period=4,
                weeks=(1, 2, 3),
            )
        generate_sessions_for_term(generated_demo.term)
        after = self._bootstrap_query_count(teacher_client)

        print(f"bootstrap queries: {before} -> {after}")
        assert before == after

    def test_changes_query_count_does_not_grow_with_session_count(self, teacher_client, generated_demo) -> None:
        before = self._changes_query_count(teacher_client, "0")

        for index in range(5):
            factories.make_rule(
                generated_demo.offerings["OFF-CRIME-2026A"],
                generated_demo.classrooms["A301"],
                (generated_demo.teachers["0002"],),
                rule_code=f"R-SYNC-QC-CHG-{index}",
                weekday=5,
                start_period=3,
                end_period=4,
                weeks=(1, 2, 3),
            )
        generate_sessions_for_term(generated_demo.term)
        after = self._changes_query_count(teacher_client, "0")

        print(f"changes queries: {before} -> {after}")
        assert before == after
