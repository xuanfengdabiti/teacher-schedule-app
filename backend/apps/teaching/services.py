"""Transactional write services for teaching notes and offline mutations.

Every note write and its ChangeLog row commit together.  The school sync row is
locked first, matching the project-wide lock order; the mutation ledger and note
row are then handled under that school lock.  A retry with the same
account/key/hash returns the exact stored response and never runs a second save
or publish (A23/A25).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction

from apps.academics.models import TeacherProfile
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import ChangeEntityType, ClassSession, SchoolSyncState
from apps.teaching.exceptions import TeachingNoteError
from apps.teaching.models import ClientMutation, ClientMutationOperation, TeachingNote
from apps.teaching.serializers import mine_payload


def mutation_request_hash(
    operation: str,
    session_id: UUID,
    *,
    base_version: int,
    covered: str | None = None,
    unfinished: str | None = None,
    next_plan: str | None = None,
) -> str:
    """Hash the operation, session, base and body in a canonical JSON form."""
    body = None
    if operation == ClientMutationOperation.SAVE_DRAFT:
        body = {
            "covered": covered,
            "unfinished": unfinished,
            "next_plan": next_plan,
        }
    canonical = json.dumps(
        {
            "operation": operation,
            "session_id": str(session_id),
            "base_version": base_version,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _lock_school_sync_state(school_id: UUID) -> None:
    # The normal write order starts with the school row.  Keeping it here means
    # note writes cannot deadlock with another sync-relevant path that follows
    # the documented State -> object order.
    SchoolSyncState.objects.get_or_create(school_id=school_id)
    SchoolSyncState.objects.select_for_update().get(school_id=school_id)


def _current_payload(note: TeachingNote | None) -> dict[str, Any] | None:
    if note is None:
        return None
    return dict(mine_payload(note))


def _version_conflict(note: TeachingNote | None) -> TeachingNoteError:
    return TeachingNoteError(
        code="NOTE_VERSION_CONFLICT",
        detail="备注已在其他设备更新。请保留本地文本，与服务器当前内容核对后再保存。",
        status_code=409,
        current=_current_payload(note),
        include_current=True,
    )


def _mutation_key_reused(note: TeachingNote | None) -> TeachingNoteError:
    return TeachingNoteError(
        code="MUTATION_KEY_REUSED",
        detail="client_mutation_id 已被另一请求使用；请为新内容生成新的 ID。",
        status_code=409,
        current=_current_payload(note),
        include_current=True,
    )


def _nothing_to_publish(note: TeachingNote | None) -> TeachingNoteError:
    return TeachingNoteError(
        code="NOTHING_TO_PUBLISH",
        detail="三段草稿去掉首尾空白后都为空，不能发布。",
        status_code=409,
        current=_current_payload(note),
        include_current=True,
    )


def _drafts_are_blank(note: TeachingNote) -> bool:
    return all(
        value is None or not value.strip()
        for value in (note.draft_covered, note.draft_unfinished, note.draft_next_plan)
    )


def _note_queryset():
    return TeachingNote.objects.select_for_update().select_related(
        "session__offering__term",
        "author",
    )


def _existing_note(teacher: TeacherProfile, session: ClassSession) -> TeachingNote | None:
    return _note_queryset().filter(session=session, author=teacher).first()


def _new_note(teacher: TeacherProfile, session: ClassSession) -> TeachingNote:
    return TeachingNote(session=session, author=teacher, version=1)


def _finish_mutation(
    mutation: ClientMutation,
    note: TeachingNote,
    response: dict[str, Any],
    *,
    now: datetime,
) -> None:
    mutation.result_object_id = note.id
    mutation.result_version = note.version
    mutation.response_json = response
    mutation.completed_at = now
    mutation.save(
        update_fields=[
            "result_object_id",
            "result_version",
            "response_json",
            "completed_at",
            "updated_at",
        ]
    )


def _record_note_change(note: TeachingNote) -> None:
    record_changes(
        note.session.offering.term.school_id,
        [
            ChangeEntry(
                entity_type=ChangeEntityType.NOTE,
                entity_id=note.id,
                version=note.version,
                offering_id=note.session.offering_id,
            )
        ],
    )


def save_draft(
    teacher: TeacherProfile,
    session: ClassSession,
    *,
    covered: str | None,
    unfinished: str | None,
    next_plan: str | None,
    base_version: int,
    client_mutation_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Save only draft columns; publication and completion remain separate."""
    request_hash = mutation_request_hash(
        ClientMutationOperation.SAVE_DRAFT,
        session.id,
        base_version=base_version,
        covered=covered,
        unfinished=unfinished,
        next_plan=next_plan,
    )
    with transaction.atomic():
        _lock_school_sync_state(session.offering.term.school_id)
        mutation, created = ClientMutation.objects.get_or_create(
            user=teacher.user,
            client_mutation_id=client_mutation_id,
            defaults={
                "operation": ClientMutationOperation.SAVE_DRAFT,
                "request_hash": request_hash,
                "completed_at": now,
            },
        )
        if not created:
            if mutation.request_hash == request_hash:
                return dict(mutation.response_json)
            note = _existing_note(teacher, session)
            raise _mutation_key_reused(note)

        note = _existing_note(teacher, session)
        if note is None:
            if base_version != 0:
                raise _version_conflict(None)
            note = _new_note(teacher, session)
            note.draft_covered = covered
            note.draft_unfinished = unfinished
            note.draft_next_plan = next_plan
            note.save()
        else:
            if base_version != note.version:
                raise _version_conflict(note)
            note.draft_covered = covered
            note.draft_unfinished = unfinished
            note.draft_next_plan = next_plan
            note.version += 1
            note.save(
                update_fields=[
                    "draft_covered",
                    "draft_unfinished",
                    "draft_next_plan",
                    "version",
                    "updated_at",
                ]
            )

        response = dict(mine_payload(note))
        _record_note_change(note)
        _finish_mutation(mutation, note, response, now=now)
    return response


def publish(
    teacher: TeacherProfile,
    session: ClassSession,
    *,
    base_version: int,
    client_mutation_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Copy the current draft into the published snapshot and bump version."""
    request_hash = mutation_request_hash(
        ClientMutationOperation.PUBLISH,
        session.id,
        base_version=base_version,
    )
    with transaction.atomic():
        _lock_school_sync_state(session.offering.term.school_id)
        mutation = (
            ClientMutation.objects.select_for_update()
            .filter(
                user=teacher.user,
                client_mutation_id=client_mutation_id,
            )
            .first()
        )
        if mutation is not None:
            if mutation.request_hash == request_hash:
                return dict(mutation.response_json)
            note = _existing_note(teacher, session)
            raise _mutation_key_reused(note)

        note = _existing_note(teacher, session)
        if note is None or _drafts_are_blank(note):
            raise _nothing_to_publish(note)
        if base_version != note.version:
            raise _version_conflict(note)

        mutation = ClientMutation.objects.create(
            user=teacher.user,
            client_mutation_id=client_mutation_id,
            operation=ClientMutationOperation.PUBLISH,
            request_hash=request_hash,
            completed_at=now,
        )
        note.published_covered = note.draft_covered
        note.published_unfinished = note.draft_unfinished
        note.published_next_plan = note.draft_next_plan
        note.version += 1
        note.published_version = note.version
        note.published_at = now
        note.save(
            update_fields=[
                "published_covered",
                "published_unfinished",
                "published_next_plan",
                "version",
                "published_version",
                "published_at",
                "updated_at",
            ]
        )

        response = dict(mine_payload(note))
        _record_note_change(note)
        _finish_mutation(mutation, note, response, now=now)
    return response
