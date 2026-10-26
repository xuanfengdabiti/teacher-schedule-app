"""HTTP/ORM helpers shared by B20 tests."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import TeacherStatus
from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching.models import TeachingNote

PASSWORD = "test-pass-123"
FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def link_user(teacher, email: str):
    if teacher.user_id:
        return teacher.user
    user = get_user_model().objects.create_user(email=email, password=PASSWORD)
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user


def auth_client(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def session_for(demo, rule_code: str, day: date) -> ClassSession:
    return ClassSession.objects.select_related(
        "offering__term",
        "offering__course",
        "offering__teaching_class",
        "classroom",
    ).get(rule=demo.rules[rule_code], origin_teaching_date=day)


def complete_session(session: ClassSession, teacher) -> ClassSession:
    """Apply the legal DB combination B22 will own later."""
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
    return session


def note_payload(
    *,
    covered: str = "已讲内容",
    unfinished: str = "未完成内容",
    next_plan: str = "下次计划",
    base_version: int = 0,
    client_mutation_id: str,
) -> dict:
    return {
        "covered": covered,
        "unfinished": unfinished,
        "next_plan": next_plan,
        "base_version": base_version,
        "client_mutation_id": client_mutation_id,
    }


def save_note(client: APIClient, session: ClassSession, **kwargs):
    return client.put(
        f"/api/v1/sessions/{session.id}/notes/mine/",
        note_payload(**kwargs),
        format="json",
    )


def publish_note(
    client: APIClient,
    session: ClassSession,
    *,
    base_version: int,
    client_mutation_id: str,
):
    return client.post(
        f"/api/v1/sessions/{session.id}/notes/mine/publish/",
        {"base_version": base_version, "client_mutation_id": client_mutation_id},
        format="json",
    )


def get_notes(client: APIClient, session: ClassSession):
    return client.get(f"/api/v1/sessions/{session.id}/notes/")


def note_for(teacher, session: ClassSession) -> TeachingNote:
    return TeachingNote.objects.select_related("session__offering__term", "author").get(
        author=teacher,
        session=session,
    )
