"""A15 and data completeness for classroom usage (B32)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone as django_timezone

from apps.academics.models import CoverageCompleteness, CoverageKind, DataCoverage
from apps.classrooms.models import RoomBlock
from apps.scheduling.changes import apply_session_change
from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching.tests.helpers import auth_client, link_user, session_for

pytestmark = pytest.mark.django_db


def _usage(client, classroom, day: date):
    return client.get(f"/api/v1/classrooms/{classroom.id}/usage/?date={day.isoformat()}")


def test_a15_moved_out_cancelled_and_room_block(generated_demo):
    """A15: moved-out/cancelled never look like live occupancy; changes remain visible."""
    demo = generated_demo
    actor = link_user(demo.teachers["0001"], "a15-actor@example.invalid")
    client = auth_client(actor)

    source = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
    apply_session_change(
        source.id,
        target_date=date(2026, 10, 28),
        start_period=5,
        end_period=6,
        classroom_id=demo.classrooms["A302"].id,
        actor=actor,
        reason="A15 正式调课",
        approval_ref="SCHOOL-A15-001",
        forced=True,
        now=datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    source.refresh_from_db()

    cancelled = session_for(demo, "R-CRIME-EVEN", date(2026, 11, 9))
    cancelled.status = SessionStatus.CANCELLED
    cancelled.actual_hours = Decimal("0")
    cancelled.status_reason = "A15 虚构停课"
    cancelled.save(update_fields=["status", "actual_hours", "status_reason", "updated_at"])

    RoomBlock.objects.create(
        classroom=demo.classrooms["A301"],
        start_at=datetime(2026, 10, 28, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_at=datetime(2026, 10, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        reason="A15 维修占用",
        block_code="A15-MAINT-1028",
    )

    old_room_response = _usage(client, demo.classrooms["A301"], date(2026, 10, 26))
    assert old_room_response.status_code == 200
    old_entries = old_room_response.json()["entries"]
    moved = [entry for entry in old_entries if entry["kind"] == "moved_out"]
    assert [entry["session_id"] for entry in moved] == [str(source.id)]
    assert not [entry for entry in old_entries if entry["kind"] == "session" and entry["session_id"] == str(source.id)]
    assert moved[0]["start_at"].startswith("2026-10-26T08:00:00")
    assert moved[0]["current_start_at"].startswith("2026-10-28T14:00:00")
    assert moved[0]["current_classroom_code"] == "A302"
    assert old_room_response.json()["notice"] == "排定课程记录，不代表实际到场或物品去向。"

    new_room_response = _usage(client, demo.classrooms["A302"], date(2026, 10, 28))
    new_sessions = [
        entry
        for entry in new_room_response.json()["entries"]
        if entry["kind"] == "session" and entry["session_id"] == str(source.id)
    ]
    assert len(new_sessions) == 1
    assert new_sessions[0]["is_moved"] is True

    cancelled_response = _usage(client, demo.classrooms["A301"], date(2026, 11, 9))
    cancelled_entries = [
        entry
        for entry in cancelled_response.json()["entries"]
        if entry["kind"] == "cancelled_session" and entry["session_id"] == str(cancelled.id)
    ]
    assert len(cancelled_entries) == 1
    assert cancelled_entries[0]["status_reason"] == "A15 虚构停课"

    block_response = _usage(client, demo.classrooms["A301"], date(2026, 10, 28))
    blocks = [entry for entry in block_response.json()["entries"] if entry["kind"] == "room_block"]
    assert len(blocks) == 1
    assert blocks[0]["reason"] == "A15 维修占用"


def test_usage_data_completeness_uses_b30_coverage_rules(generated_demo):
    """No coverage → incomplete; both school complete → complete; only partial → incomplete."""
    demo = generated_demo
    actor = link_user(demo.teachers["0001"], "coverage-viewer@example.invalid")
    client = auth_client(actor)
    day = date(2026, 10, 26)

    response = _usage(client, demo.classrooms["A301"], day)
    assert response.status_code == 200
    assert response.json()["data_completeness"] == "incomplete"

    for kind in (CoverageKind.CLASSROOM_SCHEDULE, CoverageKind.ROOM_BLOCKS):
        DataCoverage.objects.create(
            school=demo.school,
            term=demo.term,
            kind=kind,
            scope_key="school",
            from_date=day,
            until_date=date(2026, 10, 27),
            completeness=CoverageCompleteness.COMPLETE,
            source_updated_at=django_timezone.now(),
            confirmation_ref="A15 school confirmation",
        )
    response = _usage(client, demo.classrooms["A301"], day)
    assert response.json()["data_completeness"] == "complete"

    DataCoverage.objects.all().delete()
    for kind in (CoverageKind.CLASSROOM_SCHEDULE, CoverageKind.ROOM_BLOCKS):
        DataCoverage.objects.create(
            school=demo.school,
            term=demo.term,
            kind=kind,
            scope_key="school",
            from_date=day,
            until_date=date(2026, 10, 27),
            completeness=CoverageCompleteness.PARTIAL,
            source_updated_at=django_timezone.now(),
        )
    response = _usage(client, demo.classrooms["A301"], day)
    assert response.json()["data_completeness"] == "incomplete"


def test_usage_query_count_is_constant(generated_demo, django_assert_max_num_queries):
    """A15/B32: adding sessions must not add per-entry queries."""
    demo = generated_demo
    actor = link_user(demo.teachers["0001"], "usage-count@example.invalid")
    client = auth_client(actor)
    day = date(2026, 10, 28)
    offering = demo.offerings["OFF-DATA-2026A"]
    start = datetime(2026, 10, 28, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def add_session(index: int):
        session = ClassSession.objects.create(
            offering=offering,
            start_period=5,
            end_period=6,
            start_at=start.replace(hour=8 + index),
            end_at=start.replace(hour=9 + index),
            classroom=demo.classrooms["A301"],
            status=SessionStatus.SCHEDULED,
            planned_hours=Decimal("2.0"),
        )
        session.teachers.add(demo.teachers["0001"])
        return session

    add_session(0)
    with django_assert_max_num_queries(30) as first:
        first_response = _usage(client, demo.classrooms["A301"], day)
    assert first_response.status_code == 200
    first_count = len(first)

    for index in range(1, 5):
        add_session(index)
    with django_assert_max_num_queries(30) as second:
        second_response = _usage(client, demo.classrooms["A301"], day)
    assert second_response.status_code == 200
    # 1 session vs 5 sessions: 14 SQL statements in both cases (including JWT
    # user/profile authentication and the batched contact visibility queries).
    assert len(second) == first_count
