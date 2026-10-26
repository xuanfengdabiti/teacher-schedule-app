"""Fictional inquiry fixtures and HTTP helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.academics.models import TeacherProfile
from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching.tests.helpers import auth_client, link_user

TZ = ZoneInfo("Asia/Shanghai")
DAY = datetime(2026, 10, 28, 0, 0, tzinfo=TZ)
FROM_AT = DAY
TO_AT = datetime(2026, 10, 28, 23, 59, tzinfo=TZ)
FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=TZ)


def add_teacher(demo, teacher_no: str) -> TeacherProfile:
    teacher, _ = TeacherProfile.objects.get_or_create(
        school=demo.school,
        teacher_no=teacher_no,
        defaults={
            "formal_name": f"示例教师{teacher_no}",
            "department": "虚构侦查教研室",
            "email": f"t{teacher_no}@example.invalid",
        },
    )
    return teacher


def linked_users(demo):
    teachers = {number: demo.teachers[number] for number in ("0001", "0002")}
    for number in ("0003", "0004", "0005"):
        teachers[number] = add_teacher(demo, number)
    users = {number: link_user(teacher, f"inquiry-{number}@example.invalid") for number, teacher in teachers.items()}
    return teachers, users


def make_candidate_sessions(demo, teacher_numbers=("0002", "0003", "0004")):
    """Create 2026-10-28 sessions in A301 for the requested teachers."""
    sessions = []
    for index, teacher_no in enumerate(teacher_numbers):
        start = DAY + timedelta(hours=8 + index * 2)
        session = ClassSession.objects.create(
            offering=demo.offerings["OFF-DATA-2026A"],
            start_period=1 + index * 2,
            end_period=2 + index * 2,
            start_at=start,
            end_at=start + timedelta(minutes=100),
            classroom=demo.classrooms["A301"],
            status=SessionStatus.SCHEDULED,
            planned_hours=Decimal("2.0"),
        )
        session.teachers.add(demo.teachers.get(teacher_no) or add_teacher(demo, teacher_no))
        sessions.append(session)
    return sessions


def candidate_query(classroom):
    return {
        "classroom_id": str(classroom.id),
        "from_at": FROM_AT.isoformat(),
        "to_at": TO_AT.isoformat(),
    }


def create_inquiry(client, classroom, recipient_ids, description="虚构遗失物品：一只水杯"):
    return client.post(
        "/api/v1/inquiries/",
        {
            "classroom_id": str(classroom.id),
            "from_at": FROM_AT.isoformat(),
            "to_at": TO_AT.isoformat(),
            "item_description": description,
            "recipient_ids": [str(recipient_id) for recipient_id in recipient_ids],
        },
        format="json",
    )


def user_client(users, number: str):
    return auth_client(users[number])
