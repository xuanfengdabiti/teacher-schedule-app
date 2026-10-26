"""Shared fictional builders for B22/B23 plan-progress tests."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.utils import timezone as django_timezone

from apps.academics.models import CourseOffering, TeacherProfile
from apps.classrooms.models import Classroom
from apps.scheduling.models import ClassSession, SessionStatus, SessionTeacher
from apps.scheduling.tests import factories


def make_course(
    demo,
    suffix: str,
    *,
    lead: TeacherProfile,
    co_teachers: tuple[TeacherProfile, ...] = (),
    target_hours: Decimal = Decimal("40.0"),
    class_code: str | None = None,
) -> CourseOffering:
    return factories.make_offering(
        demo.term,
        lead,
        offering_code=f"OFF-T11-{suffix}",
        course_code=f"C-T11-{suffix}",
        course_name=f"T11 示例课程 {suffix}",
        class_code=class_code or f"TC-T11-{suffix}",
        class_name=f"T11 示例教学班 {suffix}",
        target_hours=target_hours,
        co_teachers=co_teachers,
    )


def make_session(
    offering: CourseOffering,
    *,
    day: date,
    start: time = time(8, 0),
    end: time = time(9, 40),
    teachers: tuple[TeacherProfile, ...],
    status: str = SessionStatus.SCHEDULED,
    actual_hours: Decimal | None = None,
    planned_hours: Decimal = Decimal("2.0"),
    classroom: Classroom | None = None,
) -> ClassSession:
    start_at = datetime.combine(day, start, tzinfo=offering.term.tzinfo)
    end_at = datetime.combine(day, end, tzinfo=offering.term.tzinfo)
    if status == SessionStatus.CANCELLED and actual_hours is None:
        actual_hours = Decimal("0.0")
    room = classroom or Classroom.objects.filter(school=offering.term.school).order_by("classroom_code").first()
    session = ClassSession.objects.create(
        offering=offering,
        start_period=1,
        end_period=2,
        start_at=start_at,
        end_at=end_at,
        classroom=room,
        status=status,
        planned_hours=planned_hours,
        actual_hours=actual_hours,
        completed_by_teacher=teachers[0] if status == SessionStatus.COMPLETED else None,
        completed_at=django_timezone.now() if status == SessionStatus.COMPLETED else None,
        completion_ref="TEST-SETUP" if status == SessionStatus.COMPLETED else "",
    )
    for teacher in teachers:
        SessionTeacher.objects.create(session=session, teacher=teacher)
    return session


def plan_item_payload(
    title: str,
    planned_hours: str | Decimal = "4.0",
    *,
    item_id=None,
    due_date: date | None = None,
    due_session_id=None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "planned_hours": planned_hours,
    }
    if item_id is not None:
        payload["id"] = item_id
    if due_date is not None:
        payload["due_date"] = due_date.isoformat()
    if due_session_id is not None:
        payload["due_session_id"] = due_session_id
    return payload


def save_plan_api(client, offering: CourseOffering, items: list[dict], *, base_version: int = 0):
    return client.put(
        f"/api/v1/offerings/{offering.id}/plan/",
        {"items": items, "base_version": base_version},
        format="json",
    )


def completion_api(
    client,
    session: ClassSession,
    *,
    action: str,
    actual_hours: str | Decimal | None = None,
    allocations: list[dict] | None = None,
    reason: str = "",
    base_version: int = 1,
):
    payload: dict[str, Any] = {
        "action": action,
        "reason": reason,
        "base_version": base_version,
    }
    if actual_hours is not None:
        payload["actual_hours"] = actual_hours
    if allocations is not None:
        payload["allocations"] = allocations
    return client.put(
        f"/api/v1/sessions/{session.id}/completion/",
        payload,
        format="json",
    )
