"""Batch session writer for B12 imports.

The importer must not issue one lock, insert, change-log entry and notification
per session: a first import can contain tens of thousands of sessions.  This
module therefore performs one bulk write pass for a whole batch:

* lock the affected pending requests and sessions once, in project order;
* bulk-create new sessions and their SessionTeacher rows;
* bulk-update changed/cancelled/restored sessions and bulk-create SessionChange
  rows with ``source=import``;
* raise each affected Term once and append one ChangeLog allocation;
* send at most one summary message per affected teacher, pending request and
  pending handoff.

All functions assume the caller already holds the import transaction and the
SchoolSyncState/Term/ImportBatch locks.  Import code never writes ClassSession
directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import F
from django.utils import timezone as django_timezone

from apps.academics.models import TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.changes import ScheduleChangeError
from apps.scheduling.models import (
    ChangeEntityType,
    ClassSession,
    RescheduleRequest,
    RescheduleStatus,
    SessionChange,
    SessionChangeSource,
    SessionStatus,
    SessionTeacher,
)

IMPORT_NEW = "new"
IMPORT_UPDATE = "update"
IMPORT_CANCEL = "cancel"
IMPORT_RESTORE = "restore"

_BULK_UPDATE_FIELDS = [
    "start_at",
    "end_at",
    "start_period",
    "end_period",
    "classroom",
    "planned_hours",
    "status",
    "status_reason",
    "actual_hours",
    "completed_by_teacher",
    "completed_at",
    "completion_ref",
    "recorded_by_user",
    "version",
    "updated_at",
]


@dataclass(frozen=True)
class ImportSessionTarget:
    """The complete desired state of one session from a B12 import."""

    term_code: str
    offering_id: UUID
    rule_id: UUID | None
    origin_teaching_date: date | None
    origin_period: int | None
    start_at: datetime
    end_at: datetime
    start_period: int
    end_period: int
    classroom_id: UUID
    teacher_ids: tuple[UUID, ...]
    planned_hours: Decimal
    status: str
    status_reason: str = ""
    actual_hours: Decimal | None = None
    completed_by_teacher_id: UUID | None = None
    completed_at: datetime | None = None
    completion_ref: str = ""


@dataclass(frozen=True)
class ImportSessionWrite:
    """One session write decided by the import preview.

    ``identity_target`` is an opaque caller-owned token (the import projection
    object) returned with the created session so the caller can create its
    ImportIdentity after the bulk insert.  Scheduling never inspects it.
    """

    action: str
    target: ImportSessionTarget
    session_id: UUID | None = None
    change_ref: str = ""
    forced: bool = False
    identity_target: Any = field(default=None, compare=False)


@dataclass
class ImportBatchWriteResult:
    created_sessions: list[ClassSession]
    session_changes: list[SessionChange]
    sessions_new: int = 0
    sessions_updated: int = 0
    sessions_cancelled: int = 0
    sessions_restored: int = 0
    scope_bumped_teacher_ids: set[UUID] = field(default_factory=set)


def _import_reason(batch, change_ref: str) -> str:
    label = f"导入批次 {batch.pk}（{batch.source_system}）"
    if change_ref:
        return f"{label}：{change_ref}"
    return f"{label}：规则、校历或节次更新"


def _state(
    session: ClassSession,
    *,
    teacher_ids: set[UUID],
    teacher_codes: dict[UUID, str],
    classroom_codes: dict[UUID, str],
    tzinfo,
) -> dict[str, object]:
    local_start = session.start_at.astimezone(tzinfo)
    return {
        "date": local_start.date().isoformat(),
        "start_period": session.start_period,
        "end_period": session.end_period,
        "start_at": local_start.isoformat(),
        "end_at": session.end_at.astimezone(tzinfo).isoformat(),
        "classroom_id": str(session.classroom_id),
        "classroom_code": classroom_codes.get(session.classroom_id, str(session.classroom_id)),
        "teacher_codes": [
            teacher_codes.get(teacher_id, str(teacher_id)) for teacher_id in sorted(teacher_ids, key=str)
        ],
        "status": session.status,
        "status_reason": session.status_reason,
        "planned_hours": str(session.planned_hours),
        "actual_hours": str(session.actual_hours) if session.actual_hours is not None else None,
        "version": session.version,
    }


def _summary_body(counts: dict[str, int]) -> str:
    return (
        "本次导入更新了你的课表："
        f"新增 {counts.get('new', 0)} 节、"
        f"调整 {counts.get('updated', 0)} 节、"
        f"取消 {counts.get('cancelled', 0)} 节、"
        f"恢复 {counts.get('restored', 0)} 节。"
    )


def apply_import_session_batch(
    writes: list[ImportSessionWrite],
    *,
    batch,
    actor,
    now: datetime,
    terms_by_code: dict[str, Term],
    definition_term_codes: set[str] | None = None,
    term_log_codes: set[str] | None = None,
    extra_entries: list[ChangeEntry] | Callable[[], list[ChangeEntry]] | None = None,
) -> ImportBatchWriteResult:
    """Apply all session writes of one import batch in a fixed number of queries."""
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")
    definition_term_codes = definition_term_codes or set()
    term_log_codes = term_log_codes or set()
    extra_entries = extra_entries or []

    if not writes and not definition_term_codes and not term_log_codes and not extra_entries:
        return ImportBatchWriteResult(created_sessions=[], session_changes=[])

    existing_ids = sorted({write.session_id for write in writes if write.session_id is not None}, key=str)
    pending_requests = list(
        RescheduleRequest.objects.select_for_update()
        .filter(session_id__in=existing_ids, status=RescheduleStatus.SUBMITTED)
        .select_related("applicant", "session__offering__term")
        .order_by("id")
    )
    sessions = {
        session.id: session
        for session in ClassSession.objects.select_for_update()
        .filter(pk__in=existing_ids)
        .select_related("offering__term", "offering__course", "classroom")
        .order_by("id")
    }
    if len(sessions) != len(existing_ids):
        raise ScheduleChangeError(
            "SESSION_CHANGED",
            "导入预览中的课次已不存在，请重新预览。",
            status_code=409,
        )

    links = list(
        SessionTeacher.objects.filter(session_id__in=existing_ids)
        .select_related("teacher")
        .order_by("session_id", "teacher__teacher_no")
    )
    current_teachers: dict[UUID, set[UUID]] = {}
    teacher_objects: dict[UUID, TeacherProfile] = {}
    links_by_session: dict[UUID, list[SessionTeacher]] = {}
    for link in links:
        current_teachers.setdefault(link.session_id, set()).add(link.teacher_id)
        teacher_objects[link.teacher_id] = link.teacher
        links_by_session.setdefault(link.session_id, []).append(link)

    target_teacher_ids: set[UUID] = set()
    target_classroom_ids: set[UUID] = set()
    for write in writes:
        target_teacher_ids.update(write.target.teacher_ids)
        target_classroom_ids.add(write.target.classroom_id)
    teacher_objects.update(
        {teacher.id: teacher for teacher in TeacherProfile.objects.filter(pk__in=target_teacher_ids)}
    )
    classrooms = {classroom.id: classroom for classroom in Classroom.objects.filter(pk__in=target_classroom_ids)}
    classroom_codes = {classroom.id: classroom.classroom_code for classroom in classrooms.values()}
    for session in sessions.values():
        classroom_codes.setdefault(session.classroom_id, session.classroom.classroom_code)
    teacher_codes = {teacher_id: teacher.teacher_no for teacher_id, teacher in teacher_objects.items()}

    created: list[ClassSession] = []
    changed: list[ClassSession] = []
    change_rows: list[SessionChange] = []
    additions: list[SessionTeacher] = []
    removed_link_ids: list[UUID] = []
    scope_ids: set[UUID] = set()
    affected_session_ids: set[UUID] = set()
    teacher_counts: dict[UUID, dict[str, int]] = {}
    first_session: dict[UUID, UUID] = {}

    def mark_teacher(teacher_id: UUID, kind: str, session_id: UUID) -> None:
        bucket = teacher_counts.setdefault(teacher_id, {})
        bucket[kind] = bucket.get(kind, 0) + 1
        first_session.setdefault(teacher_id, session_id)

    for write in writes:
        target = write.target
        classroom = classrooms.get(target.classroom_id)
        if classroom is None:
            raise ScheduleChangeError("CLASSROOM_NOT_FOUND", "导入目标教室不存在，请重新预览。", status_code=409)
        desired_teachers = set(target.teacher_ids)

        if write.action == IMPORT_NEW:
            if target.status not in {SessionStatus.SCHEDULED, SessionStatus.COMPLETED, SessionStatus.CANCELLED}:
                raise ScheduleChangeError("INVALID_IMPORT_TARGET", "导入新增课次状态无效。", status_code=400)
            session = ClassSession(
                id=uuid.uuid4(),
                offering_id=target.offering_id,
                rule_id=target.rule_id,
                origin_teaching_date=target.origin_teaching_date,
                origin_period=target.origin_period,
                start_at=target.start_at,
                end_at=target.end_at,
                start_period=target.start_period,
                end_period=target.end_period,
                classroom_id=target.classroom_id,
                status=target.status,
                status_reason=target.status_reason,
                planned_hours=target.planned_hours,
                actual_hours=target.actual_hours,
                completed_by_teacher_id=target.completed_by_teacher_id,
                completed_at=target.completed_at,
                completion_ref=target.completion_ref,
                recorded_by_user=actor if target.status == SessionStatus.COMPLETED else None,
            )
            if target.status == SessionStatus.CANCELLED:
                session.actual_hours = Decimal("0")
            created.append(session)
            for teacher_id in desired_teachers:
                additions.append(SessionTeacher(session_id=session.id, teacher_id=teacher_id))
            for teacher_id in desired_teachers:
                mark_teacher(teacher_id, "new", session.id)
            continue

        session = sessions.get(write.session_id)
        if session is None:
            raise ScheduleChangeError("SESSION_CHANGED", "导入目标课次不存在，请重新预览。", status_code=409)
        before_teachers = current_teachers.get(session.id, set())
        if write.action == IMPORT_UPDATE:
            if session.status != SessionStatus.SCHEDULED:
                raise ScheduleChangeError(
                    "SESSION_CHANGED",
                    "只有待上课的课次可以按导入结果调整。",
                    status_code=409,
                )
            if target.status not in {SessionStatus.SCHEDULED, SessionStatus.COMPLETED}:
                raise ScheduleChangeError("INVALID_IMPORT_TARGET", "导入调整状态无效。", status_code=400)
        elif write.action == IMPORT_CANCEL:
            if session.status != SessionStatus.SCHEDULED:
                raise ScheduleChangeError(
                    "SESSION_CHANGED",
                    "只有待上课的课次可以按导入结果取消。",
                    status_code=409,
                )
        elif write.action == IMPORT_RESTORE:
            if session.status != SessionStatus.CANCELLED:
                raise ScheduleChangeError(
                    "SESSION_CHANGED",
                    "只有已取消的课次可以恢复。",
                    status_code=409,
                )
            if target.status != SessionStatus.SCHEDULED:
                raise ScheduleChangeError("INVALID_IMPORT_TARGET", "恢复目标必须为待上课。", status_code=400)
        else:
            raise ScheduleChangeError("INVALID_IMPORT_TARGET", "未知导入动作。", status_code=400)

        before = _state(
            session,
            teacher_ids=before_teachers,
            teacher_codes=teacher_codes,
            classroom_codes=classroom_codes,
            tzinfo=session.offering.term.tzinfo,
        )
        session.start_at = target.start_at
        session.end_at = target.end_at
        session.start_period = target.start_period
        session.end_period = target.end_period
        session.classroom_id = target.classroom_id
        session.planned_hours = target.planned_hours
        if write.action == IMPORT_CANCEL:
            session.status = SessionStatus.CANCELLED
            session.status_reason = target.status_reason or "导入更新：课次被取消"
            session.actual_hours = Decimal("0")
            session.completed_by_teacher_id = None
            session.completed_at = None
            session.completion_ref = ""
            session.recorded_by_user = None
            mark_teacher_ids = before_teachers | desired_teachers
        elif write.action == IMPORT_RESTORE:
            session.status = SessionStatus.SCHEDULED
            session.status_reason = ""
            session.actual_hours = None
            session.completed_by_teacher_id = None
            session.completed_at = None
            session.completion_ref = ""
            session.recorded_by_user = None
            mark_teacher_ids = before_teachers | desired_teachers
        else:
            session.status = target.status
            session.status_reason = target.status_reason
            session.actual_hours = target.actual_hours if target.status == SessionStatus.COMPLETED else None
            session.completed_by_teacher_id = (
                target.completed_by_teacher_id if target.status == SessionStatus.COMPLETED else None
            )
            session.completed_at = target.completed_at if target.status == SessionStatus.COMPLETED else None
            session.completion_ref = target.completion_ref if target.status == SessionStatus.COMPLETED else ""
            session.recorded_by_user = actor if target.status == SessionStatus.COMPLETED else None
            mark_teacher_ids = before_teachers | desired_teachers
        session.version += 1
        session.updated_at = now
        changed.append(session)
        affected_session_ids.add(session.id)

        removed = before_teachers - desired_teachers
        added = desired_teachers - before_teachers
        removed_link_ids.extend(link.id for link in links_by_session.get(session.id, ()) if link.teacher_id in removed)
        for teacher_id in added:
            additions.append(SessionTeacher(session_id=session.id, teacher_id=teacher_id))
        scope_ids.update(removed)
        scope_ids.update(added)
        for teacher_id in mark_teacher_ids:
            kind = {
                IMPORT_UPDATE: "updated",
                IMPORT_CANCEL: "cancelled",
                IMPORT_RESTORE: "restored",
            }[write.action]
            mark_teacher(teacher_id, kind, session.id)

        after = _state(
            session,
            teacher_ids=desired_teachers,
            teacher_codes=teacher_codes,
            classroom_codes=classroom_codes,
            tzinfo=session.offering.term.tzinfo,
        )
        change_rows.append(
            SessionChange(
                session=session,
                request=None,
                session_version=session.version,
                before_json=before,
                after_json=after,
                source=SessionChangeSource.IMPORT,
                actor=actor,
                reason=_import_reason(batch, write.change_ref),
                approval_ref=(write.change_ref or "").strip(),
                forced_override=write.forced,
            )
        )

    if created:
        ClassSession.objects.bulk_create(created)
    if changed:
        ClassSession.objects.bulk_update(changed, _BULK_UPDATE_FIELDS)
    if removed_link_ids:
        SessionTeacher.objects.filter(pk__in=removed_link_ids).delete()
    if additions:
        SessionTeacher.objects.bulk_create(additions)
    if change_rows:
        SessionChange.objects.bulk_create(change_rows)

    bump_codes = {write.target.term_code for write in writes} | set(definition_term_codes)
    term_entries: dict[UUID, ChangeEntry] = {}
    bumped_terms: list[Term] = []
    for term_code in sorted(bump_codes):
        term = terms_by_code.get(term_code)
        if term is None:
            continue
        term.schedule_version += 1
        term.updated_at = now
        bumped_terms.append(term)
        term_entries[term.id] = ChangeEntry(
            entity_type=ChangeEntityType.TERM,
            entity_id=term.id,
            version=term.schedule_version,
        )
    for term_code in sorted(term_log_codes):
        term = terms_by_code.get(term_code)
        if term is None or term.id in term_entries:
            continue
        term_entries[term.id] = ChangeEntry(
            entity_type=ChangeEntityType.TERM,
            entity_id=term.id,
            version=term.schedule_version,
        )
    if bumped_terms:
        Term.objects.bulk_update(bumped_terms, ["schedule_version", "updated_at"])

    log_entries: dict[tuple[str, UUID], ChangeEntry] = {
        (entry.entity_type, entry.entity_id): entry for entry in term_entries.values()
    }
    for session in [*created, *changed]:
        entry = ChangeEntry(
            entity_type=ChangeEntityType.SESSION,
            entity_id=session.id,
            version=session.version,
            offering_id=session.offering_id,
        )
        log_entries[(entry.entity_type, entry.entity_id)] = entry
    resolved_extra = extra_entries() if callable(extra_entries) else (extra_entries or [])
    for entry in resolved_extra:
        log_entries[(entry.entity_type, entry.entity_id)] = entry
    if log_entries:
        record_changes(batch.school_id, list(log_entries.values()))

    if scope_ids:
        TeacherProfile.objects.filter(pk__in=scope_ids).update(
            assignment_scope_version=F("assignment_scope_version") + 1,
            updated_at=now,
        )

    if pending_requests:
        for request in pending_requests:
            summary = dict(request.check_summary or {})
            summary["import_update"] = {
                "batch_id": str(batch.pk),
                "message": "课次已被导入更新，请重新核对目标时间、教室和冲突。",
                "updated_at": now.isoformat(),
            }
            request.check_summary = summary
            request.updated_at = now
        RescheduleRequest.objects.bulk_update(pending_requests, ["check_summary", "updated_at"])
        for request in pending_requests:
            notify(
                request.applicant,
                type=NotificationType.RESCHEDULE_RESULT,
                object_type="reschedule_request",
                object_id=request.id,
                title="课次已被导入更新",
                body="你提交的调课申请所对应课次已被导入更新；申请仍保持待审批，请重新核对目标时间与教室。",
                event_key=f"import:{batch.pk}:request:{request.id}",
            )

    # Local import: teaching.models imports scheduling.models.
    from apps.teaching.models import Handoff, HandoffStatus

    handoffs = list(
        Handoff.objects.filter(
            target_session_id__in=affected_session_ids,
            status=HandoffStatus.PENDING,
        )
        .select_related("sender", "offering__course")
        .order_by("id")
    )
    for handoff in handoffs:
        notify(
            handoff.sender,
            type=NotificationType.HANDOFF_CHECK,
            object_type="handoff",
            object_id=handoff.id,
            title="请核对交接接收人",
            body=(
                f"交接《{handoff.offering.course.name}》的目标课次已被导入更新。"
                "请核对接收人是否仍合适；系统不会自动更改接收人。"
            ),
            event_key=f"import:{batch.pk}:handoff:{handoff.id}",
        )

    for teacher_id in sorted(teacher_counts, key=lambda value: teacher_codes.get(value, str(value))):
        teacher = teacher_objects.get(teacher_id)
        if teacher is None:
            continue
        counts = teacher_counts[teacher_id]
        notify(
            teacher,
            type=NotificationType.SCHEDULE_CHANGED,
            object_type="session",
            object_id=first_session.get(teacher_id),
            title="课表已由导入更新",
            body=_summary_body(counts),
            event_key=f"import:{batch.pk}:{teacher_id}",
        )

    return ImportBatchWriteResult(
        created_sessions=created,
        session_changes=change_rows,
        sessions_new=len(created),
        sessions_updated=sum(1 for write in writes if write.action == IMPORT_UPDATE),
        sessions_cancelled=sum(1 for write in writes if write.action == IMPORT_CANCEL),
        sessions_restored=sum(1 for write in writes if write.action == IMPORT_RESTORE),
        scope_bumped_teacher_ids=scope_ids,
    )
