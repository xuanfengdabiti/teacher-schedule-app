"""Append-only change-log allocation for App incremental synchronisation.

**Every future write path that changes a session, term or school calendar must
call** :func:`record_changes` **inside the same database transaction as the
object update** (B12 imports, B31 formal rescheduling, B22 completion
confirmation and Admin calendar edits).  Otherwise the mobile App's
``sync/changes/`` cursor advances without the changed object, and the device
silently keeps stale data until a full bootstrap.

The caller owns the transaction.  This module deliberately does not open one:
opening a separate transaction here would make the change log visible before
(or survive after) a failed object write and break the snapshot guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db import transaction

from apps.scheduling.models import ChangeEntityType, ChangeLog, SchoolSyncState


@dataclass(frozen=True, slots=True)
class ChangeEntry:
    """One object state to append, in caller-defined order."""

    entity_type: str
    entity_id: UUID
    version: int
    offering_id: UUID | None = None
    tombstone: bool = False


def record_changes(school_id: UUID, entries: list[ChangeEntry]) -> int:
    """Append ``entries`` and return the school's new ``last_seq``.

    ``SchoolSyncState`` is locked with ``select_for_update`` even when the
    caller already locked it (the generation service does so as its first
    write lock); PostgreSQL permits re-locking the same row in one transaction.
    This keeps a caller that forgot the lock correct rather than merely
    documenting the obligation.

    Allocation follows the caller's list order and is contiguous:
    ``last_seq + 1`` through ``last_seq + len(entries)``.  Logs are inserted in
    one ``bulk_create``, so the statement count does not grow with a generated
    term's session count.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("record_changes 必须在已有数据库事务中调用。")

    # get_or_create handles the first-write race for a school without a state
    # row; the following select_for_update makes the allocation lock explicit.
    # Reading last_seq only after this lock is what guarantees consecutive,
    # non-overlapping sequences across concurrent transactions.
    SchoolSyncState.objects.get_or_create(school_id=school_id)
    state = SchoolSyncState.objects.select_for_update().get(school_id=school_id)

    logs: list[ChangeLog] = []
    next_seq = state.last_seq
    for entry in entries:
        entity_type = ChangeEntityType(entry.entity_type)
        if entry.version < 1:
            raise ValueError("ChangeEntry.version 必须是正整数。")
        next_seq += 1
        logs.append(
            ChangeLog(
                school_id=school_id,
                change_seq=next_seq,
                entity_type=entity_type,
                entity_id=entry.entity_id,
                offering_id=entry.offering_id,
                version=entry.version,
                tombstone=entry.tombstone,
            )
        )

    if logs:
        ChangeLog.objects.bulk_create(logs)
        state.last_seq = next_seq
        state.save(update_fields=["last_seq", "updated_at"])
    return next_seq
