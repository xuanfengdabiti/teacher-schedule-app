"""Serializers and JSON payload builders for B21 teaching handoffs."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.scheduling.selectors import session_local_date
from apps.teaching.models import Handoff, HandoffStatus


def _teacher_name(teacher) -> str:
    return teacher.display_name or teacher.formal_name


def _iso(value, term) -> str | None:
    if value is None:
        return None
    return value.astimezone(term.tzinfo).isoformat()


def teacher_brief(teacher) -> dict[str, str]:
    return {"id": str(teacher.id), "name": _teacher_name(teacher)}


def session_brief(session) -> dict[str, Any] | None:
    if session is None:
        return None
    return {
        "id": str(session.id),
        "date": session_local_date(session).isoformat(),
        "start_period": session.start_period,
        "end_period": session.end_period,
    }


def handoff_payload(handoff: Handoff, teacher) -> dict[str, Any]:
    """One list/detail item; flags are scoped to the requesting teacher."""
    term = handoff.offering.term
    offering = handoff.offering
    return {
        "id": str(handoff.id),
        "offering": {
            "id": str(offering.id),
            "course_name": offering.course.name,
            "teaching_class": {
                "id": str(offering.teaching_class_id),
                "name": offering.teaching_class.name,
            },
        },
        "sender": teacher_brief(handoff.sender),
        "recipient": teacher_brief(handoff.recipient),
        "source_session": session_brief(handoff.source_session),
        "target_session": session_brief(handoff.target_session),
        "content_snapshot": handoff.content_snapshot,
        "note_published_version": handoff.note_published_version,
        "status": handoff.status,
        "read_at": _iso(handoff.read_at, term),
        "acknowledged_at": _iso(handoff.acknowledged_at, term),
        "withdrawn_at": _iso(handoff.withdrawn_at, term),
        "created_at": _iso(handoff.created_at, term),
        "can_acknowledge": handoff.status == HandoffStatus.PENDING and handoff.recipient_id == teacher.id,
        "can_withdraw": handoff.status == HandoffStatus.PENDING and handoff.sender_id == teacher.id,
    }


def prefill_content(note) -> str:
    """Join published unfinished/next-plan text under Chinese headings.

    Only published columns are read; a draft alone never prefills a handoff.
    Blank sections are omitted instead of emitting an empty heading.
    """
    if note is None or note.published_at is None:
        return ""
    sections: list[str] = []
    unfinished = (note.published_unfinished or "").strip()
    next_plan = (note.published_next_plan or "").strip()
    if unfinished:
        sections.append(f"未完成内容：\n{unfinished}")
    if next_plan:
        sections.append(f"下次计划：\n{next_plan}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# HTTP/OpenAPI schemas
# ---------------------------------------------------------------------------


class HandoffCreateSerializer(serializers.Serializer):
    offering_id = serializers.UUIDField()
    recipient_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=10,
        allow_empty=False,
    )
    content = serializers.CharField(max_length=5000, allow_blank=False)
    source_session_id = serializers.UUIDField(required=False, allow_null=True)
    target_session_id = serializers.UUIDField(required=False, allow_null=True)
    note_id = serializers.UUIDField(required=False, allow_null=True)


class HandoffListQuerySerializer(serializers.Serializer):
    box = serializers.ChoiceField(choices=("inbox", "sent"), default="inbox")
    status = serializers.ChoiceField(choices=HandoffStatus.choices, required=False)


class HandoffPrefillQuerySerializer(serializers.Serializer):
    source_session_id = serializers.UUIDField()


class HandoffTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class HandoffTeachingClassSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class HandoffOfferingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    course_name = serializers.CharField()
    teaching_class = HandoffTeachingClassSerializer()


class HandoffSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    date = serializers.DateField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()


class HandoffSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    offering = HandoffOfferingSerializer()
    sender = HandoffTeacherSerializer()
    recipient = HandoffTeacherSerializer()
    source_session = HandoffSessionSerializer(allow_null=True)
    target_session = HandoffSessionSerializer(allow_null=True)
    content_snapshot = serializers.CharField()
    note_published_version = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=HandoffStatus.choices)
    read_at = serializers.DateTimeField(allow_null=True)
    acknowledged_at = serializers.DateTimeField(allow_null=True)
    withdrawn_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    can_acknowledge = serializers.BooleanField()
    can_withdraw = serializers.BooleanField()


class PaginatedHandoffListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = HandoffSerializer(many=True)


class HandoffPrefillRecipientSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    is_suggested = serializers.BooleanField()


class HandoffPrefillSerializer(serializers.Serializer):
    recipients = HandoffPrefillRecipientSerializer(many=True)
    suggested_target_session = HandoffSessionSerializer(allow_null=True)
    content = serializers.CharField()
    note_id = serializers.UUIDField(allow_null=True)
    note_published_version = serializers.IntegerField(allow_null=True)


class HandoffErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
