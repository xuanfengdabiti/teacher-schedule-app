"""Context assembly and draft persistence for the AI lesson-preparation slice.

The preparation draft is private to one session teacher.  Generating content
never writes the draft and never touches notes, completion, hours or handoffs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.academics.models import TeacherProfile
from apps.scheduling.models import ClassSession
from apps.scheduling.selectors import session_local_date
from apps.teaching.exceptions import preparation_version_conflict
from apps.teaching.hours import hours_string
from apps.teaching.lesson_ai import generate_lesson_sections
from apps.teaching.models import LessonPreparation, PlanItem
from apps.teaching.previous_notes import previous_note_previews

MAX_PLAN_TOPICS = 5


def ai_available() -> bool:
    """True when the deployment has enough configuration to call a model."""
    return all(
        str(getattr(settings, name, "") or "").strip()
        for name in (
            "TEACHING_AI_API_BASE_URL",
            "TEACHING_AI_MODEL",
            "TEACHING_AI_API_KEY",
        )
    )


def teaching_minutes(session: ClassSession) -> int:
    """Teaching time only: planned hours × minutes per hour, no breaks."""
    minutes_per_hour = session.offering.minutes_per_hour or session.offering.term.hour_minutes
    total = Decimal(session.planned_hours) * Decimal(minutes_per_hour or 0)
    return int(total)


def plan_items_summary(session: ClassSession) -> list[dict[str, Any]]:
    items = PlanItem.objects.filter(plan__offering=session.offering, archived=False).order_by("position", "id")[
        :MAX_PLAN_TOPICS
    ]
    return [
        {
            "title": item.title,
            "planned_hours": hours_string(item.planned_hours),
        }
        for item in items
    ]


def lesson_context(teacher: TeacherProfile, session: ClassSession) -> dict[str, Any]:
    previous_note = previous_note_previews(teacher, [session]).get(session.id, "")
    return {
        "course_name": session.offering.course.name,
        "date": session_local_date(session).isoformat(),
        "start_period": session.start_period,
        "end_period": session.end_period,
        "teaching_minutes": teaching_minutes(session),
        "previous_note": previous_note,
        "plan_items": plan_items_summary(session),
    }


def preparation_payload(prep: LessonPreparation) -> dict[str, Any]:
    return {
        "id": str(prep.id),
        "topic": prep.topic,
        "outline": prep.outline,
        "case_study": prep.case_study,
        "discussion_questions": prep.discussion_questions,
        "preparation": prep.preparation,
        "version": prep.version,
        "generated_model": prep.generated_model,
        "generated_at": prep.generated_at.isoformat() if prep.generated_at else None,
        "updated_at": prep.updated_at.isoformat(),
    }


def current_preparation(teacher: TeacherProfile, session: ClassSession) -> LessonPreparation | None:
    return LessonPreparation.objects.filter(session=session, teacher=teacher).first()


def generate_preparation(
    teacher: TeacherProfile,
    session: ClassSession,
    topic: str,
) -> dict[str, Any]:
    """Call the configured model. Nothing is written to the database here."""
    context = lesson_context(teacher, session)
    sections, model = generate_lesson_sections(context, topic)
    return {
        "topic": topic,
        "outline": sections["outline"],
        "case_study": sections["case_study"],
        "discussion_questions": sections["discussion_questions"],
        "preparation": sections["preparation"],
        "generated_model": model,
        "generated_at": timezone.now().isoformat(),
    }


def save_preparation(
    teacher: TeacherProfile,
    session: ClassSession,
    *,
    base_version: int,
    topic: str,
    outline: str,
    case_study: str,
    discussion_questions: str,
    preparation: str,
    generated_model: str,
    generated_at: datetime | None,
) -> dict[str, Any]:
    with transaction.atomic():
        existing = LessonPreparation.objects.select_for_update().filter(session=session, teacher=teacher).first()
        if existing is None:
            if base_version != 0:
                raise preparation_version_conflict(None)
            prep = LessonPreparation.objects.create(
                session=session,
                teacher=teacher,
                topic=topic,
                outline=outline,
                case_study=case_study,
                discussion_questions=discussion_questions,
                preparation=preparation,
                generated_model=generated_model,
                generated_at=generated_at,
                version=1,
            )
        else:
            if base_version != existing.version:
                raise preparation_version_conflict(preparation_payload(existing))
            existing.topic = topic
            existing.outline = outline
            existing.case_study = case_study
            existing.discussion_questions = discussion_questions
            existing.preparation = preparation
            existing.generated_model = generated_model
            existing.generated_at = generated_at
            existing.version += 1
            existing.save(
                update_fields=[
                    "topic",
                    "outline",
                    "case_study",
                    "discussion_questions",
                    "preparation",
                    "generated_model",
                    "generated_at",
                    "version",
                    "updated_at",
                ]
            )
            prep = existing
    return preparation_payload(prep)
