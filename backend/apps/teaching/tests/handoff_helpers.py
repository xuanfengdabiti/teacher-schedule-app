"""Shared fixtures/helpers for B21 handoff tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from rest_framework.test import APIClient

from apps.academics.models import AssignmentRole, TeacherProfile, TeachingAssignment
from apps.scheduling.models import ClassSession, SessionStatus
from apps.scheduling.selectors import current_school_date
from apps.scheduling.tests import factories
from apps.scheduling.tests.demo_data import DemoTermData
from apps.teaching.models import TeachingNote
from apps.teaching.tests.helpers import auth_client, link_user, note_for, publish_note, save_note, session_for

CRIME_SOURCE_DAY = date(2026, 10, 12)
CRIME_NEXT_DAY = date(2026, 10, 19)


@dataclass(frozen=True)
class HandoffFlow:
    demo: DemoTermData
    teacher1: TeacherProfile
    teacher2: TeacherProfile
    client1: APIClient
    client2: APIClient


def make_handoff_flow(demo: DemoTermData) -> HandoffFlow:
    teacher1 = demo.teachers["0001"]
    teacher2 = demo.teachers["0002"]
    user1 = link_user(teacher1, "t0001@example.invalid")
    user2 = link_user(teacher2, "t0002@example.invalid")
    return HandoffFlow(
        demo=demo,
        teacher1=teacher1,
        teacher2=teacher2,
        client1=auth_client(user1),
        client2=auth_client(user2),
    )


def add_teacher(
    demo,
    teacher_no: str,
    *,
    offering_key: str = "OFF-CRIME-2026A",
    ended: bool = False,
):
    """Add a non-demo teacher with one assignment.

    ``ended`` creates an assignment whose exclusive end is today, so it must not
    grant recipient eligibility.
    """
    teacher = factories.make_teacher(demo.school, teacher_no, f"示例教师{teacher_no}")
    if ended:
        today = current_school_date()
        effective_from = today - timedelta(days=30)
        effective_until = today
    else:
        effective_from = demo.term.start_date
        effective_until = demo.term.end_date + timedelta(days=1)
    TeachingAssignment.objects.create(
        offering=demo.offerings[offering_key],
        teacher=teacher,
        role=AssignmentRole.TEACHER,
        effective_from=effective_from,
        effective_until=effective_until,
    )
    return teacher


def crime_source(demo) -> ClassSession:
    """The even-week crime session taught by teacher 0002 (A14 source)."""
    return session_for(demo, "R-CRIME-EVEN", CRIME_SOURCE_DAY)


def next_scheduled_session(session: ClassSession) -> ClassSession | None:
    return (
        ClassSession.objects.filter(
            offering_id=session.offering_id,
            status=SessionStatus.SCHEDULED,
            start_at__gt=session.start_at,
        )
        .order_by("start_at", "id")
        .first()
    )


def publish_own_note(
    client,
    session: ClassSession,
    teacher,
    *,
    key: str,
    covered: str = "已讲内容",
    unfinished: str = "未完成内容",
    next_plan: str = "下次计划",
) -> TeachingNote:
    saved = save_note(
        client,
        session,
        covered=covered,
        unfinished=unfinished,
        next_plan=next_plan,
        base_version=0,
        client_mutation_id=f"{key}-draft",
    )
    assert saved.status_code == 200, saved.content
    published = publish_note(
        client,
        session,
        base_version=saved.json()["version"],
        client_mutation_id=f"{key}-publish",
    )
    assert published.status_code == 200, published.content
    return note_for(teacher, session)


def send_handoff(
    client,
    offering,
    recipients,
    *,
    content: str = "交接内容",
    source_session: ClassSession | None = None,
    target_session: ClassSession | None = None,
    note: TeachingNote | None = None,
):
    payload = {
        "offering_id": str(offering.id),
        "recipient_ids": [str(recipient.id) for recipient in recipients],
        "content": content,
    }
    if source_session is not None:
        payload["source_session_id"] = str(source_session.id)
    if target_session is not None:
        payload["target_session_id"] = str(target_session.id)
    if note is not None:
        payload["note_id"] = str(note.id)
    return client.post("/api/v1/handoffs/", payload, format="json")
