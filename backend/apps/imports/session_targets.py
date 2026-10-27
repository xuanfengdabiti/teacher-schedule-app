"""Session target projection and plan-to-existing pairing for B12 imports.

This module only builds desired session states from the effective timetable and
pairs them with the UUIDs that were saved by an earlier generation.  It never
classifies decisions, evaluates conflicts or writes a formal table.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from apps.academics.models import Term
from apps.imports.parsing import ParsedRow
from apps.imports.projection.base import ImportObject
from apps.imports.projection.planning import TermPlanData, configured_week
from apps.scheduling.calendar import PlannedSession
from apps.scheduling.import_changes import ImportSessionTarget
from apps.scheduling.models import ClassSession, SessionStatus

_STATUS_LABELS = {
    SessionStatus.SCHEDULED: "待上课",
    SessionStatus.COMPLETED: "已完成",
    SessionStatus.CANCELLED: "已取消",
}
_CONFLICT_LABELS = {
    "teacher": "教师冲突",
    "student": "学生冲突",
    "classroom": "教室冲突",
}


@dataclass
class ReconcileTarget:
    term_code: str
    offering_id: UUID
    rule_id: UUID | None
    origin_teaching_date: date | None
    origin_period: int | None
    session_date: date
    start_period: int
    end_period: int
    start_at: datetime
    end_at: datetime
    classroom_id: UUID
    teacher_ids: tuple[UUID, ...]
    status: str
    status_reason: str
    planned_hours: Decimal
    actual_hours: Decimal | None
    completed_by_teacher_id: UUID | None
    completed_at: datetime | None
    completion_ref: str
    external_session_id: str
    existing: ClassSession | None
    explicit_change: bool = False
    change_ref: str = ""
    row: ParsedRow | None = None
    import_object: ImportObject | None = None
    from_row: bool = False

    def write_target(self) -> ImportSessionTarget:
        return ImportSessionTarget(
            term_code=self.term_code,
            offering_id=self.offering_id,
            rule_id=self.rule_id,
            origin_teaching_date=self.origin_teaching_date,
            origin_period=self.origin_period,
            start_at=self.start_at,
            end_at=self.end_at,
            start_period=self.start_period,
            end_period=self.end_period,
            classroom_id=self.classroom_id,
            teacher_ids=self.teacher_ids,
            planned_hours=self.planned_hours,
            status=self.status,
            status_reason=self.status_reason,
            actual_hours=self.actual_hours,
            completed_by_teacher_id=self.completed_by_teacher_id,
            completed_at=self.completed_at,
            completion_ref=self.completion_ref,
        )


def _local_date(value: datetime, term: Term) -> date:
    return value.astimezone(term.tzinfo).date()


def _time_text(value: datetime, term: Term) -> str:
    return value.astimezone(term.tzinfo).strftime("%H:%M")


def _state_payload(
    *,
    session: ClassSession | None = None,
    target: ReconcileTarget | None = None,
    teacher_codes: dict[UUID, str],
    classroom_codes: dict[UUID, str],
    teachers_by_session: dict[UUID, set[UUID]],
    term: Term,
    external_session_id: str,
) -> dict:
    if session is not None:
        teacher_ids = tuple(sorted(teachers_by_session.get(session.id, set()), key=str))
        return {
            "session_id": str(session.id),
            "external_session_id": external_session_id,
            "date": _local_date(session.start_at, term).isoformat(),
            "start_period": session.start_period,
            "end_period": session.end_period,
            "start_time": _time_text(session.start_at, term),
            "end_time": _time_text(session.end_at, term),
            "classroom_code": classroom_codes.get(session.classroom_id, str(session.classroom_id)),
            "teacher_codes": [teacher_codes.get(teacher_id, str(teacher_id)) for teacher_id in teacher_ids],
            "status": session.status,
            "status_label": _STATUS_LABELS.get(session.status, session.status),
            "planned_hours": str(session.planned_hours),
            "actual_hours": str(session.actual_hours) if session.actual_hours is not None else None,
        }
    assert target is not None
    return {
        "session_id": str(target.existing.pk) if target.existing is not None else None,
        "external_session_id": external_session_id,
        "date": target.session_date.isoformat(),
        "start_period": target.start_period,
        "end_period": target.end_period,
        "start_time": _time_text(target.start_at, term),
        "end_time": _time_text(target.end_at, term),
        "classroom_code": classroom_codes.get(target.classroom_id, str(target.classroom_id)),
        "teacher_codes": [teacher_codes.get(teacher_id, str(teacher_id)) for teacher_id in target.teacher_ids],
        "status": target.status,
        "status_label": _STATUS_LABELS.get(target.status, target.status),
        "planned_hours": str(target.planned_hours),
        "actual_hours": str(target.actual_hours) if target.actual_hours is not None else None,
    }


def _target_from_plan(
    *,
    term: Term,
    term_code: str,
    planned: PlannedSession,
    existing: ClassSession | None,
    external_session_id: str,
) -> ReconcileTarget:
    return ReconcileTarget(
        term_code=term_code,
        offering_id=planned.offering_id,
        rule_id=planned.rule_id,
        origin_teaching_date=planned.origin_teaching_date,
        origin_period=planned.origin_period,
        session_date=planned.session_date,
        start_period=planned.start_period,
        end_period=planned.end_period,
        start_at=planned.start_at,
        end_at=planned.end_at,
        classroom_id=planned.classroom_id,
        teacher_ids=tuple(sorted(set(planned.teacher_ids), key=str)),
        status=planned.status,
        status_reason=planned.status_reason,
        planned_hours=planned.planned_hours,
        actual_hours=Decimal("0") if planned.status == SessionStatus.CANCELLED else None,
        completed_by_teacher_id=None,
        completed_at=None,
        completion_ref="",
        external_session_id=external_session_id,
        existing=existing,
    )


def _target_cancel(
    *,
    term_code: str,
    existing: ClassSession,
    external_session_id: str,
    reason: str,
    teachers_by_session: dict[UUID, set[UUID]],
) -> ReconcileTarget:
    tzinfo = existing.offering.term.tzinfo
    local_start = existing.start_at.astimezone(tzinfo)
    return ReconcileTarget(
        term_code=term_code,
        offering_id=existing.offering_id,
        rule_id=existing.rule_id,
        origin_teaching_date=existing.origin_teaching_date,
        origin_period=existing.origin_period,
        session_date=local_start.date(),
        start_period=existing.start_period,
        end_period=existing.end_period,
        start_at=existing.start_at,
        end_at=existing.end_at,
        classroom_id=existing.classroom_id,
        teacher_ids=tuple(sorted(teachers_by_session.get(existing.id, set()), key=str)),
        status=SessionStatus.CANCELLED,
        status_reason=reason,
        planned_hours=existing.planned_hours,
        actual_hours=Decimal("0"),
        completed_by_teacher_id=None,
        completed_at=None,
        completion_ref="",
        external_session_id=external_session_id,
        existing=existing,
    )


def _target_from_row(term: Term, obj: ImportObject) -> ReconcileTarget:
    data: ClassSession = obj.data
    tzinfo = term.tzinfo
    local_start = data.start_at.astimezone(tzinfo)
    return ReconcileTarget(
        term_code=obj.metadata.get("term_code", ""),
        offering_id=data.offering_id,
        rule_id=data.rule_id,
        origin_teaching_date=data.origin_teaching_date,
        origin_period=data.origin_period,
        session_date=local_start.date(),
        start_period=data.start_period,
        end_period=data.end_period,
        start_at=data.start_at,
        end_at=data.end_at,
        classroom_id=data.classroom_id,
        teacher_ids=tuple(sorted(set(obj.metadata.get("teacher_ids", ())), key=str)),
        status=data.status,
        status_reason=data.status_reason,
        planned_hours=data.planned_hours,
        actual_hours=data.actual_hours,
        completed_by_teacher_id=data.completed_by_teacher_id,
        completed_at=data.completed_at,
        completion_ref=data.completion_ref,
        external_session_id=obj.external_id,
        existing=obj.existing if isinstance(obj.existing, ClassSession) else None,
        explicit_change=bool(obj.metadata.get("explicit_change")),
        change_ref=obj.metadata.get("change_ref", ""),
        row=obj.rows[0] if obj.rows else None,
        import_object=obj,
        from_row=True,
    )


@dataclass
class _PlanPairs:
    by_plan: dict[tuple[UUID, date, int], ClassSession]
    by_existing: dict[UUID, PlannedSession]


def _pair_plan_to_existing(term_plan: TermPlanData) -> _PlanPairs:
    by_plan: dict[tuple[UUID, date, int], ClassSession] = {}
    by_existing: dict[UUID, PlannedSession] = {}
    planned_by_rule: dict[UUID, list[PlannedSession]] = defaultdict(list)
    existing_by_rule: dict[UUID, list[ClassSession]] = defaultdict(list)
    for planned in term_plan.planned:
        planned_by_rule[planned.rule_id].append(planned)
    for session in term_plan.existing_sessions:
        if (
            session.rule_id is not None
            and session.origin_teaching_date is not None
            and session.origin_period is not None
        ):
            existing_by_rule[session.rule_id].append(session)

    for rule_id, planned_rows in planned_by_rule.items():
        existing_rows = existing_by_rule.get(rule_id, [])
        exact = {
            (session.origin_teaching_date, session.origin_period): session
            for session in existing_rows
            if session.origin_teaching_date is not None and session.origin_period is not None
        }
        unpaired_existing = list(existing_rows)
        unpaired_planned: list[PlannedSession] = []
        for planned in planned_rows:
            key = (planned.origin_teaching_date, planned.origin_period)
            session = exact.get(key)
            if session is None:
                unpaired_planned.append(planned)
                continue
            by_plan[(planned.rule_id, planned.origin_teaching_date, planned.origin_period)] = session
            by_existing[session.id] = planned
            if session in unpaired_existing:
                unpaired_existing.remove(session)

        existing_by_week: dict[int, list[ClassSession]] = defaultdict(list)
        for session in unpaired_existing:
            week = configured_week(
                term_plan.term,
                session.origin_teaching_date,
                session.origin_teaching_date.isoweekday(),
            )
            existing_by_week[week].append(session)
        for planned in unpaired_planned:
            candidates = existing_by_week.get(planned.week, [])
            if not candidates:
                continue
            session = candidates.pop(0)
            by_plan[(planned.rule_id, planned.origin_teaching_date, planned.origin_period)] = session
            by_existing[session.id] = planned
    return _PlanPairs(by_plan=by_plan, by_existing=by_existing)


def _target_diff(
    existing: ClassSession,
    target: ReconcileTarget,
    term: Term,
    teachers_by_session: dict[UUID, set[UUID]],
) -> set[str]:
    diff: set[str] = set()
    if _local_date(existing.start_at, term) != target.session_date:
        diff.add("date")
    if existing.start_period != target.start_period:
        diff.add("start_period")
    if existing.end_period != target.end_period:
        diff.add("end_period")
    if existing.start_at != target.start_at:
        diff.add("start_at")
    if existing.end_at != target.end_at:
        diff.add("end_at")
    if existing.classroom_id != target.classroom_id:
        diff.add("classroom")
    if teachers_by_session.get(existing.id, set()) != set(target.teacher_ids):
        diff.add("teachers")
    plan_progress_status = (
        existing.status == SessionStatus.COMPLETED and target.status == SessionStatus.SCHEDULED and not target.from_row
    )
    if existing.status != target.status and not plan_progress_status:
        diff.add("status")
    if existing.planned_hours != target.planned_hours:
        diff.add("planned_hours")
    if existing.status == SessionStatus.COMPLETED and target.status == SessionStatus.COMPLETED:
        if existing.actual_hours != target.actual_hours:
            diff.add("actual_hours")
        if existing.completed_by_teacher_id != target.completed_by_teacher_id:
            diff.add("completed_by")
        if existing.completed_at != target.completed_at:
            diff.add("completed_at")
        if existing.completion_ref != target.completion_ref:
            diff.add("completion_ref")
    return diff


_LAYOUT_FIELDS = {
    "date",
    "start_period",
    "end_period",
    "start_at",
    "end_at",
    "classroom",
}
