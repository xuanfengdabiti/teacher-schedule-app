"""Serializers and JSON-safe payload builders for teaching notes (B20)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.scheduling.selectors import session_local_date
from apps.teaching.models import TeachingNote


def _iso(value, note: TeachingNote) -> str | None:
    if value is None:
        return None
    return value.astimezone(note.session.offering.term.tzinfo).isoformat()


def _teacher_name(teacher) -> str:
    return teacher.display_name or teacher.formal_name


def draft_payload(note: TeachingNote) -> dict[str, Any]:
    return {
        "covered": note.draft_covered,
        "unfinished": note.draft_unfinished,
        "next_plan": note.draft_next_plan,
    }


def published_payload(note: TeachingNote) -> dict[str, Any] | None:
    if note.published_at is None:
        return None
    return {
        "covered": note.published_covered,
        "unfinished": note.published_unfinished,
        "next_plan": note.published_next_plan,
    }


def mine_payload(note: TeachingNote) -> dict[str, Any]:
    """The canonical ``mine`` object, also stored verbatim in ClientMutation."""
    return {
        "id": str(note.id),
        "version": note.version,
        "draft": draft_payload(note),
        "published": published_payload(note),
        "published_at": _iso(note.published_at, note),
        "published_version": note.published_version,
        "updated_at": _iso(note.updated_at, note),
    }


def shared_payload(note: TeachingNote) -> dict[str, Any]:
    return {
        "author": {
            "id": str(note.author_id),
            "name": _teacher_name(note.author),
        },
        "published": published_payload(note),
        "published_at": _iso(note.published_at, note),
        "published_version": note.published_version,
    }


def previous_session_payload(session) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "date": session_local_date(session).isoformat(),
        "start_period": session.start_period,
        "end_period": session.end_period,
        "course_name": session.offering.course.name,
    }


def previous_note_payload(note: TeachingNote) -> dict[str, Any]:
    return {
        "author": {
            "id": str(note.author_id),
            "name": _teacher_name(note.author),
        },
        "published": published_payload(note),
        "published_at": _iso(note.published_at, note),
    }


def sync_note_payload(note: TeachingNote, teacher) -> dict[str, Any]:
    """Sync shape keeps the owning teacher's draft and hides everyone else's."""
    include_draft = note.author_id == teacher.id
    return {
        "id": str(note.id),
        "session_id": str(note.session_id),
        "author": {
            "id": str(note.author_id),
            "name": _teacher_name(note.author),
        },
        "version": note.version,
        "draft": draft_payload(note) if include_draft else None,
        "published": published_payload(note),
        "published_at": _iso(note.published_at, note),
        "published_version": note.published_version,
        "updated_at": _iso(note.updated_at, note),
    }


# ---------------------------------------------------------------------------
# HTTP/OpenAPI schemas
# ---------------------------------------------------------------------------


class NoteTextSerializer(serializers.Serializer):
    covered = serializers.CharField(allow_null=True, allow_blank=True)
    unfinished = serializers.CharField(allow_null=True, allow_blank=True)
    next_plan = serializers.CharField(allow_null=True, allow_blank=True)


class NoteAuthorSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class MineNoteSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version = serializers.IntegerField()
    draft = NoteTextSerializer()
    published = NoteTextSerializer(allow_null=True)
    published_at = serializers.DateTimeField(allow_null=True)
    published_version = serializers.IntegerField(allow_null=True)
    updated_at = serializers.DateTimeField()


class SharedNoteSerializer(serializers.Serializer):
    author = NoteAuthorSerializer()
    published = NoteTextSerializer()
    published_at = serializers.DateTimeField()
    published_version = serializers.IntegerField()


class PreviousSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    date = serializers.DateField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()
    course_name = serializers.CharField()


class PreviousNoteSerializer(serializers.Serializer):
    author = NoteAuthorSerializer()
    published = NoteTextSerializer()
    published_at = serializers.DateTimeField()


class PreviousNotesSerializer(serializers.Serializer):
    session = PreviousSessionSerializer()
    notes = PreviousNoteSerializer(many=True)


class SessionNotesResponseSerializer(serializers.Serializer):
    mine = MineNoteSerializer(allow_null=True)
    others = SharedNoteSerializer(many=True)
    previous = PreviousNotesSerializer(allow_null=True)


class SyncTeachingNoteSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    session_id = serializers.UUIDField()
    author = NoteAuthorSerializer()
    version = serializers.IntegerField()
    draft = NoteTextSerializer(allow_null=True)
    published = NoteTextSerializer(allow_null=True)
    published_at = serializers.DateTimeField(allow_null=True)
    published_version = serializers.IntegerField(allow_null=True)
    updated_at = serializers.DateTimeField()


class NoteDraftWriteSerializer(serializers.Serializer):
    covered = serializers.CharField(max_length=5000, allow_null=True, allow_blank=True)
    unfinished = serializers.CharField(max_length=5000, allow_null=True, allow_blank=True)
    next_plan = serializers.CharField(max_length=5000, allow_null=True, allow_blank=True)
    base_version = serializers.IntegerField(min_value=0)
    client_mutation_id = serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=False)


class NotePublishSerializer(serializers.Serializer):
    base_version = serializers.IntegerField(min_value=0)
    client_mutation_id = serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=False)


class NoteErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    current = serializers.JSONField(required=False, allow_null=True)
