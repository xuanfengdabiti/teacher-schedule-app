"""自由教室查询：真实占用、边界、覆盖和批量查询（B12 演示增强）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.academics.models import CoverageCompleteness, CoverageKind, DataCoverage
from apps.classrooms.models import Classroom, RoomBlock
from apps.scheduling.changes import apply_session_change
from apps.scheduling.models import SessionStatus
from apps.teaching.tests.helpers import auth_client, link_user, session_for

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _complete_coverage(term, *, scope_key: str = "school") -> None:
    for kind in (CoverageKind.CLASSROOM_SCHEDULE, CoverageKind.ROOM_BLOCKS):
        DataCoverage.objects.get_or_create(
            school=term.school,
            term=term,
            kind=kind,
            scope_key=scope_key,
            from_date=term.start_date,
            until_date=term.end_date.replace(year=term.end_date.year + 1),
            defaults={
                "completeness": CoverageCompleteness.COMPLETE,
                "source_updated_at": NOW,
                "confirmation_ref": "AVAILABILITY-TEST",
            },
        )


def _get(client, *, day: date = date(2026, 10, 26), start: str = "08:00", end: str = "09:40", **params):
    query = {"date": day.isoformat(), "start_time": start, "end_time": end, **params}
    return client.get("/api/v1/classrooms/availability/", query)


def _codes(payload: dict) -> list[str]:
    return [row["classroom_code"] for row in payload["results"]]


def test_sessions_room_blocks_and_boundary_touching(generated_demo):
    demo = generated_demo
    _complete_coverage(demo.term)
    client = auth_client(link_user(demo.teachers["0001"], "availability-boundary@example.invalid"))

    response = _get(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-10-26"
    assert payload["start_time"] == "08:00"
    assert payload["end_time"] == "09:40"
    assert payload["checked_at"]
    # R-CRIME-EVEN occupies A301 08:00–09:40; the other two rooms are free.
    assert _codes(payload) == ["A302", "B201"]
    assert payload["available_count"] == 2
    assert payload["unverified_count"] == 0
    assert payload["count"] == 2
    assert {row["availability"] for row in payload["results"]} == {"available"}
    assert {row["data_completeness"] for row in payload["results"]} == {"complete"}

    RoomBlock.objects.create(
        classroom=demo.classrooms["B201"],
        start_at=datetime(2026, 10, 26, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_at=datetime(2026, 10, 26, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
        reason="虚构维修占用",
        block_code="AVAILABILITY-BLOCK-1026",
    )
    blocked = _get(client).json()
    assert _codes(blocked) == ["A302"]

    # Query starts exactly when the crime session and the block end, and stops
    # exactly when the data session starts: touching boundaries are free.
    boundary = _get(client, start="09:40", end="10:00").json()
    assert _codes(boundary) == ["A301", "A302", "B201"]
    assert boundary["available_count"] == 3


def test_cancelled_and_formally_moved_sessions_update_occupancy(generated_demo):
    demo = generated_demo
    _complete_coverage(demo.term)
    actor = link_user(demo.teachers["0001"], "availability-move@example.invalid")
    client = auth_client(actor)

    source = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
    apply_session_change(
        source.id,
        target_date=date(2026, 10, 28),
        start_period=5,
        end_period=6,
        classroom_id=demo.classrooms["A302"].id,
        actor=actor,
        reason="虚构正式调课",
        approval_ref="AVAILABILITY-MOVE-001",
        forced=True,
        now=NOW,
    )

    old_slot = _get(client).json()
    assert "A301" in _codes(old_slot)
    assert "A302" in _codes(old_slot)

    new_slot = _get(client, day=date(2026, 10, 28), start="14:00", end="15:40").json()
    assert "A302" not in _codes(new_slot)
    assert "A301" in _codes(new_slot)

    cancelled = session_for(demo, "R-DATA-MON", date(2026, 10, 26))
    cancelled.status = SessionStatus.CANCELLED
    cancelled.actual_hours = Decimal("0")
    cancelled.status_reason = "虚构停课"
    cancelled.save(update_fields=["status", "actual_hours", "status_reason", "updated_at"])

    data_slot = _get(client, start="10:00", end="11:40").json()
    assert "A302" in _codes(data_slot)


def test_coverage_complete_partial_missing_and_known_occupancy(generated_demo):
    demo = generated_demo
    client = auth_client(link_user(demo.teachers["0001"], "availability-coverage@example.invalid"))

    missing = _get(client).json()
    # A301 is occupied regardless of coverage; it must never appear in results.
    assert _codes(missing) == ["A302", "B201"]
    assert missing["available_count"] == 0
    assert missing["unverified_count"] == 2
    assert {row["availability"] for row in missing["results"]} == {"unverified"}
    assert {row["data_completeness"] for row in missing["results"]} == {"incomplete"}

    _complete_coverage(demo.term)
    complete = _get(client).json()
    assert complete["available_count"] == 2
    assert {row["availability"] for row in complete["results"]} == {"available"}

    DataCoverage.objects.filter(
        term=demo.term,
        kind=CoverageKind.CLASSROOM_SCHEDULE,
        scope_key="school",
    ).update(completeness=CoverageCompleteness.PARTIAL, confirmation_ref="")
    degraded = _get(client).json()
    assert degraded["available_count"] == 0
    assert degraded["unverified_count"] == 2
    assert _codes(degraded) == ["A302", "B201"]

    _complete_coverage(demo.term, scope_key="classroom:B201")
    mixed = _get(client).json()
    by_code = {row["classroom_code"]: row for row in mixed["results"]}
    assert by_code["B201"]["availability"] == "available"
    assert by_code["A302"]["availability"] == "unverified"


def test_filters_capacity_active_search_and_empty_result(generated_demo):
    demo = generated_demo
    _complete_coverage(demo.term)
    client = auth_client(link_user(demo.teachers["0001"], "availability-filters@example.invalid"))

    capacity = _get(client, min_capacity=35).json()
    assert _codes(capacity) == ["B201"]

    building = _get(client, q="示例教学楼B").json()
    assert _codes(building) == ["B201"]

    campus = _get(client, q="虚构校区").json()
    assert _codes(campus) == ["A302", "B201"]

    catalogue = client.get("/api/v1/classrooms/", {"q": "虚构校区"}).json()
    assert [row["classroom_code"] for row in catalogue["results"]] == ["A301", "A302", "B201"]

    demo.classrooms["A302"].is_active = False
    demo.classrooms["A302"].save(update_fields=["is_active", "updated_at"])
    inactive = _get(client, q="虚构校区").json()
    assert _codes(inactive) == ["B201"]

    empty = _get(client, q="不存在的教室").json()
    assert empty["count"] == 0
    assert empty["results"] == []

    invalid = _get(client, start="09:40", end="08:00")
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    assert "end_time" in invalid.json()["field_errors"]


def test_query_count_does_not_grow_with_classroom_count(generated_demo):
    demo = generated_demo
    _complete_coverage(demo.term)
    client = auth_client(link_user(demo.teachers["0001"], "availability-queries@example.invalid"))

    with CaptureQueriesContext(connection) as first:
        assert _get(client).status_code == 200

    for index in range(5):
        Classroom.objects.create(
            school=demo.school,
            classroom_code=f"C{index:03d}",
            campus="虚构校区",
            building="示例教学楼C",
            room_no=str(100 + index),
            capacity=25,
            equipment=["projector"],
        )

    with CaptureQueriesContext(connection) as second:
        assert _get(client).status_code == 200

    assert len(second) == len(first)
