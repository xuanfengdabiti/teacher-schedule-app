"""Reschedule target evaluation and candidate generation (B30/B31).

There is one conflict algorithm for the whole project.  :class:`_TargetEvaluator`
evaluates a single ``(day, period run, classroom)`` target from a preloaded data
snapshot.  :func:`evaluate_target` is the public single-target entry point used
by B31 submission/approval, :func:`evaluate_import_targets` evaluates all import
moves/restores from one preloaded snapshot per term, and
:func:`find_reschedule_options` builds one snapshot and calls the same evaluator
for every candidate.  There is deliberately no second implementation in the
request or change service.

Candidate generation answers three separate questions for every target:

* is there a known teacher/student/classroom conflict?
* is the data needed to trust a "no conflict" answer complete for that date?
* is the target structurally allowed at all (periods, calendar, capacity,
  equipment, active room, future time and the source session's length)?

All schedule, enrolment, block and coverage rows are fetched in a fixed number
of queries before the in-memory candidate loop.  Candidate count (14 vs 60
days) must not change the query count; see A04-A08, A21, A22 and A26 in
``docs/domain-design.md`` section 10.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone as django_timezone

from apps.academics.models import (
    CalendarException,
    CoverageCompleteness,
    CoverageKind,
    DataCoverage,
    Enrollment,
    Period,
    TeacherProfile,
    Term,
)
from apps.classrooms.models import Classroom, RoomBlock
from apps.scheduling.calendar import teaching_week
from apps.scheduling.models import (
    ClassSession,
    PersonBlock,
    SessionStatus,
    SessionTeacher,
)

ELIGIBILITY_AVAILABLE = "available"
ELIGIBILITY_CONFLICT = "conflict"
ELIGIBILITY_UNVERIFIED = "unverified"
# Public single-target evaluations use this value for a structurally invalid
# target.  B30 never emits it as an option: invalid candidates are omitted.
ELIGIBILITY_NOT_ALLOWED = "not_allowed"

TARGET_NOT_ALLOWED = "TARGET_NOT_ALLOWED"
RESCHEDULE_CONFLICT = "RESCHEDULE_CONFLICT"
DATA_UNVERIFIED = "DATA_UNVERIFIED"

# B30/B31 use the conservative reschedule policy: a target must be in the
# future, inside an ordinary teaching day, within one period block and keep the
# source session's period count.  B12 imports a school-confirmed timetable, so
# they only need the teacher/student/classroom conflict computation.  Keeping
# the policy as an explicit argument prevents imports from weakening B30/B31 by
# accidentally sharing a "relaxed" code path.
POLICY_RESCHEDULE = "reschedule"
POLICY_IMPORT = "import"

CONFLICT_TEACHER = "teacher"
CONFLICT_STUDENT = "student"
CONFLICT_CLASSROOM = "classroom"

_ELIGIBILITY_ORDER = {
    ELIGIBILITY_AVAILABLE: 0,
    ELIGIBILITY_CONFLICT: 1,
    ELIGIBILITY_UNVERIFIED: 2,
}

_MISSING_LABELS = {
    CoverageKind.CALENDAR: "校历与节次",
    CoverageKind.TEACHER_ASSIGNMENTS: "任课关系",
    CoverageKind.TEACHER_SCHEDULE: "教师全校排课",
    CoverageKind.STUDENT_ENROLLMENTS: "实际选课名单",
    CoverageKind.STUDENT_SCHEDULE: "学生全校排课",
    CoverageKind.CLASSROOM_SCHEDULE: "教室课程占用",
    CoverageKind.ROOM_BLOCKS: "教室非课程占用",
    CoverageKind.PERSON_BLOCKS: "人员不可用",
}

_MISSING_ORDER = tuple(_MISSING_LABELS)


class RescheduleOptionsError(Exception):
    """Small business error owned by the reschedule query/change services.

    The view catches this exception and renders the unified
    ``code/detail/field_errors`` body.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int,
        field_errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.field_errors = field_errors or {}

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "detail": self.detail,
            "field_errors": self.field_errors,
        }


@dataclass(frozen=True)
class OptionQuery:
    """Validated input for one candidate query.

    Dates form a half-open interval ``[date_from, date_until)``.  ``None``
    means "all blocks" / "source room plus every active room in the school";
    an empty classroom list means no room and therefore no candidate.
    """

    date_from: date
    date_until: date
    allowed_block_codes: set[str] | None = None
    classroom_ids: list[UUID] | None = None
    allow_weekend: bool = False


@dataclass(frozen=True)
class ConflictDetail:
    category: str
    session_id: UUID | None = None
    course_name: str | None = None
    reason: str | None = None
    student_count: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "session_id": str(self.session_id) if self.session_id is not None else None,
            "course_name": self.course_name,
            "reason": self.reason,
            "student_count": self.student_count,
        }


@dataclass(frozen=True)
class TargetEvaluation:
    """Outcome of evaluating one exact target.

    ``eligibility`` is ``available``/``conflict``/``unverified`` for a
    structurally valid target and ``not_allowed`` when a hard condition failed.
    ``code``/``detail`` explain the action to take; they do not replace the
    structured conflict and missing-data lists.
    """

    eligibility: str
    conflicts: tuple[ConflictDetail, ...]
    missing_data: tuple[str, ...]
    student_conflict_count: int
    reasons: tuple[str, ...]
    start_at: datetime | None
    end_at: datetime | None
    code: str | None = None
    detail: str = ""
    field_errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.eligibility == ELIGIBILITY_AVAILABLE

    @property
    def is_allowed(self) -> bool:
        return self.eligibility != ELIGIBILITY_NOT_ALLOWED

    def as_dict(self) -> dict[str, object]:
        return {
            "eligibility": self.eligibility,
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "missing_data": list(self.missing_data),
            "student_conflict_count": self.student_conflict_count,
            "reasons": list(self.reasons),
            "start_at": self.start_at.isoformat() if self.start_at is not None else None,
            "end_at": self.end_at.isoformat() if self.end_at is not None else None,
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ImportEvaluationRequest:
    """One import target evaluated against a shared batch snapshot."""

    session: ClassSession
    day: date
    start_period: int
    end_period: int
    classroom: Classroom
    start_at: datetime | None = None
    end_at: datetime | None = None
    teacher_ids: tuple[UUID, ...] | None = None
    status: str = SessionStatus.SCHEDULED


@dataclass(frozen=True)
class ImportOccupancyTarget:
    """One target session that will occupy a resource in the batch result."""

    id: UUID
    offering_id: UUID
    start_at: datetime
    end_at: datetime
    classroom_id: UUID
    teacher_ids: tuple[UUID, ...]
    status: str = SessionStatus.SCHEDULED
    course_name: str = ""


@dataclass(frozen=True)
class RescheduleOption:
    start_at: datetime
    end_at: datetime
    start_period: int
    end_period: int
    classroom_id: UUID
    classroom_code: str
    eligibility: str
    student_conflict_count: int
    conflicts: tuple[ConflictDetail, ...]
    missing_data: tuple[str, ...]
    reasons: tuple[str, ...]

    @classmethod
    def from_evaluation(
        cls,
        evaluation: TargetEvaluation,
        *,
        start_period: int,
        end_period: int,
        classroom: Classroom,
    ) -> RescheduleOption:
        # Callers only construct an option after the evaluator returned a
        # valid target, so the target times are present.
        assert evaluation.start_at is not None and evaluation.end_at is not None
        return cls(
            start_at=evaluation.start_at,
            end_at=evaluation.end_at,
            start_period=start_period,
            end_period=end_period,
            classroom_id=classroom.id,
            classroom_code=classroom.classroom_code,
            eligibility=evaluation.eligibility,
            student_conflict_count=evaluation.student_conflict_count,
            conflicts=evaluation.conflicts,
            missing_data=evaluation.missing_data,
            reasons=evaluation.reasons,
        )


@dataclass(frozen=True)
class OptionResult:
    session_id: UUID
    schedule_version: int
    checked_at: datetime
    data_completeness: str
    options: tuple[RescheduleOption, ...]


@dataclass(frozen=True)
class _PeriodSlot:
    start_period: int
    end_period: int
    start_time: time
    end_time: time
    block_code: str


@dataclass(frozen=True)
class _CandidateSlot:
    day: date
    start_period: int
    end_period: int
    start_at: datetime
    end_at: datetime
    block_code: str


@dataclass(frozen=True)
class _EnrollmentInterval:
    student_id: UUID
    student_no: str
    effective_from: date
    effective_until: date | None

    def active_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_until is None or self.effective_until > day)


class _CoverageLookup:
    """Date-aware DataCoverage matcher with conservative overlap handling."""

    def __init__(self, rows: list[tuple[str, str, date, date, str]]) -> None:
        self._rows: dict[tuple[str, str], list[tuple[date, date, str]]] = defaultdict(list)
        for kind, scope_key, from_date, until_date, completeness in rows:
            self._rows[(kind, scope_key)].append((from_date, until_date, completeness))

    def scope_complete(self, kind: str, scope_key: str, day: date) -> bool:
        """Whether ``scope_key`` is complete on ``day``.

        A matching partial/missing declaration always wins over a matching
        complete row.  A school can explicitly say a scope is only partly known;
        another complete row for the same scope must not erase that warning.
        """
        rows = self._rows.get((kind, scope_key), ())
        saw_coverage = False
        for from_date, until_date, completeness in rows:
            if from_date <= day < until_date:
                saw_coverage = True
                if completeness != CoverageCompleteness.COMPLETE:
                    return False
        return saw_coverage


@dataclass(frozen=True)
class _EvaluationData:
    """All rows needed to evaluate any target in a fixed time window.

    Building this snapshot is the only place that queries the database.  The
    evaluator and the B30 candidate loop are pure in-memory operations, which is
    what keeps one query count for 14/60 days and a constant count for one
    ``evaluate_target`` call.
    """

    session: ClassSession
    term: Term
    periods: dict[int, Period]
    exception_dates: frozenset[date]
    classrooms: tuple[Classroom, ...]
    target_days: tuple[date, ...]
    occupancy_by_day: dict[date, tuple[ClassSession, ...]]
    occupancy_by_day_room: dict[tuple[date, UUID], tuple[ClassSession, ...]]
    teachers_by_session: dict[UUID, set[UUID]]
    teachers_by_id: dict[UUID, TeacherProfile]
    source_teachers: tuple[TeacherProfile, ...]
    enrolments_by_offering: dict[UUID, list[_EnrollmentInterval]]
    student_numbers: dict[UUID, str]
    person_blocks_by_day: dict[date, tuple[PersonBlock, ...]]
    room_blocks_by_day_room: dict[tuple[date, UUID], tuple[RoomBlock, ...]]
    coverage: _CoverageLookup
    required_equipment: frozenset[str]
    excluded_session_id: UUID | None = None


def _check_source_status(session: ClassSession) -> None:
    if session.status != SessionStatus.SCHEDULED:
        raise RescheduleOptionsError(
            "SESSION_NOT_RESCHEDULABLE",
            "只有待上课的课次可以查询或申请调课，已完成或已取消的课次不能调课。",
            status_code=409,
        )


def _clip_date_range(term, query: OptionQuery) -> tuple[date, date]:
    """Validate the requested span and clip it to the source term."""
    if query.date_until <= query.date_from:
        raise RescheduleOptionsError(
            "INVALID_DATE_RANGE",
            "结束日期必须晚于开始日期。",
            status_code=400,
            field_errors={"date_until": ["结束日期必须晚于开始日期。"]},
        )

    term_days = (term.end_date - term.start_date).days + 1
    if (query.date_until - query.date_from).days > term_days:
        raise RescheduleOptionsError(
            "INVALID_DATE_RANGE",
            "查询区间不能超过一个学期。",
            status_code=400,
            field_errors={"date_until": ["查询区间不能超过一个学期。"]},
        )

    start = max(query.date_from, term.start_date)
    end = min(query.date_until, term.end_date + timedelta(days=1))
    return start, end


def _period_slots(
    periods: dict[int, Period],
    span: int,
    allowed_block_codes: set[str] | None,
) -> list[_PeriodSlot]:
    """Return every contiguous period run of the source session's length.

    All periods in a run must exist and share one ``block_code``.  A run that
    crosses the morning/afternoon boundary is therefore impossible even when
    both blocks exist in the timetable.
    """
    slots: list[_PeriodSlot] = []
    for start_number in sorted(periods):
        selected = [periods.get(number) for number in range(start_number, start_number + span)]
        if any(period is None for period in selected):
            continue
        assert all(period is not None for period in selected)
        block_code = selected[0].block_code
        if any(period.block_code != block_code for period in selected):
            continue
        if allowed_block_codes and block_code not in allowed_block_codes:
            continue
        start_time = selected[0].start_time
        end_time = selected[-1].end_time
        if end_time <= start_time:
            continue
        slots.append(
            _PeriodSlot(
                start_period=start_number,
                end_period=start_number + span - 1,
                start_time=start_time,
                end_time=end_time,
                block_code=block_code,
            )
        )
    return slots


def _candidate_slots(
    *,
    term,
    start_date: date,
    end_date: date,
    source: ClassSession,
    periods: dict[int, Period],
    exception_dates: set[date],
    allowed_block_codes: set[str] | None,
    allow_weekend: bool,
    now: datetime,
) -> list[_CandidateSlot]:
    span = source.end_period - source.start_period + 1
    period_slots = _period_slots(periods, span, allowed_block_codes)
    tzinfo = ZoneInfo(term.timezone)
    candidates: list[_CandidateSlot] = []

    for offset in range((end_date - start_date).days):
        day = start_date + timedelta(days=offset)
        if teaching_week(term, day) is None:
            continue
        if not allow_weekend and day.weekday() >= 5:
            continue
        if day in exception_dates:
            # This includes replace targets.  The simpler conservative rule is
            # intentional and documented in domain-design 4.2: a target date
            # that executes another teaching day's schedule is not offered as
            # a free target for an unrelated course.
            continue
        for slot in period_slots:
            start_at = datetime.combine(day, slot.start_time, tzinfo=tzinfo)
            end_at = datetime.combine(day, slot.end_time, tzinfo=tzinfo)
            if start_at <= now:
                continue
            candidates.append(
                _CandidateSlot(
                    day=day,
                    start_period=slot.start_period,
                    end_period=slot.end_period,
                    start_at=start_at,
                    end_at=end_at,
                    block_code=slot.block_code,
                )
            )
    return candidates


def _overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def _scope_complete_for_any(coverage: _CoverageLookup, kind: str, keys: tuple[str, ...], day: date) -> bool:
    return any(coverage.scope_complete(kind, key, day) for key in keys)


def classroom_day_coverage_complete(*, school_id: UUID, classroom_code: str, day: date) -> bool:
    """Whether B30's coverage rules prove room schedule and blocks complete.

    Classroom history must use the same conservative matcher as reschedule
    candidates: a matching partial/missing declaration always wins, and a
    blank scope is never treated as complete.  ``school`` or the classroom's
    own ``classroom:{code}`` scope may supply the declaration.
    """
    kinds = (CoverageKind.CLASSROOM_SCHEDULE, CoverageKind.ROOM_BLOCKS)
    rows = list(
        DataCoverage.objects.filter(
            school_id=school_id,
            kind__in=kinds,
            from_date__lte=day,
            until_date__gt=day,
        ).values_list("kind", "scope_key", "from_date", "until_date", "completeness")
    )
    coverage = _CoverageLookup(rows)
    keys = ("school", f"classroom:{classroom_code}")
    return all(_scope_complete_for_any(coverage, kind, keys, day) for kind in kinds)


def _scope_complete_for_all(
    coverage: _CoverageLookup,
    kind: str,
    keys: tuple[str, ...],
    day: date,
) -> bool:
    # An empty key set cannot prove "no extra restriction"; a school-level
    # complete declaration is checked separately by the caller.
    return bool(keys) and all(coverage.scope_complete(kind, key, day) for key in keys)


def _missing_data_common_for(
    coverage: _CoverageLookup,
    *,
    day: date,
    offering_code: str,
    teachers: tuple[TeacherProfile, ...],
    students: tuple[tuple[UUID, str], ...],
) -> tuple[str, ...]:
    """Coverage that does not depend on the candidate classroom.

    This runs once per candidate time slot, not once per classroom.  The two
    classroom-dependent categories are handled by ``_missing_data_room_for``.
    """
    teacher_keys = tuple(f"teacher:{teacher.teacher_no}" for teacher in teachers)
    student_keys = tuple(f"student:{student_no}" for _, student_no in students)
    offering_key = f"offering:{offering_code}"

    missing: set[str] = set()
    if not coverage.scope_complete(CoverageKind.CALENDAR, "school", day):
        missing.add(CoverageKind.CALENDAR)

    if not _scope_complete_for_any(
        coverage,
        CoverageKind.TEACHER_ASSIGNMENTS,
        ("school", offering_key),
        day,
    ):
        missing.add(CoverageKind.TEACHER_ASSIGNMENTS)

    if not (
        coverage.scope_complete(CoverageKind.TEACHER_SCHEDULE, "school", day)
        or _scope_complete_for_all(coverage, CoverageKind.TEACHER_SCHEDULE, teacher_keys, day)
    ):
        missing.add(CoverageKind.TEACHER_SCHEDULE)

    if not _scope_complete_for_any(
        coverage,
        CoverageKind.STUDENT_ENROLLMENTS,
        ("school", offering_key),
        day,
    ):
        missing.add(CoverageKind.STUDENT_ENROLLMENTS)

    if not (
        coverage.scope_complete(CoverageKind.STUDENT_SCHEDULE, "school", day)
        or _scope_complete_for_all(coverage, CoverageKind.STUDENT_SCHEDULE, student_keys, day)
    ):
        missing.add(CoverageKind.STUDENT_SCHEDULE)

    person_keys = teacher_keys + student_keys
    if not (
        coverage.scope_complete(CoverageKind.PERSON_BLOCKS, "school", day)
        or _scope_complete_for_all(coverage, CoverageKind.PERSON_BLOCKS, person_keys, day)
    ):
        missing.add(CoverageKind.PERSON_BLOCKS)

    return tuple(kind for kind in _MISSING_ORDER if kind in missing)


def _missing_data_room_for(
    coverage: _CoverageLookup,
    *,
    day: date,
    classroom_code: str,
) -> tuple[str, ...]:
    """Only the two coverage categories that differ by candidate classroom."""
    classroom_key = f"classroom:{classroom_code}"
    missing: set[str] = set()
    if not _scope_complete_for_any(
        coverage,
        CoverageKind.CLASSROOM_SCHEDULE,
        ("school", classroom_key),
        day,
    ):
        missing.add(CoverageKind.CLASSROOM_SCHEDULE)
    if not _scope_complete_for_any(
        coverage,
        CoverageKind.ROOM_BLOCKS,
        ("school", classroom_key),
        day,
    ):
        missing.add(CoverageKind.ROOM_BLOCKS)
    return tuple(kind for kind in _MISSING_ORDER if kind in missing)


def _merge_missing_data(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged = set().union(*groups) if groups else set()
    return tuple(kind for kind in _MISSING_ORDER if kind in merged)


def _conflict_reasons(conflicts: tuple[ConflictDetail, ...]) -> list[str]:
    reasons: list[str] = []
    for conflict in conflicts:
        if conflict.category == CONFLICT_TEACHER:
            if conflict.session_id is not None:
                reasons.append(f"教师时间冲突：{conflict.course_name or '未知课程'}")
            else:
                reasons.append(f"教师不可用：{conflict.reason or '未说明原因'}")
        elif conflict.category == CONFLICT_STUDENT:
            if conflict.session_id is not None:
                reasons.append(f"学生冲突 {conflict.student_count or 0} 人：{conflict.course_name or '未知课程'}")
            else:
                reasons.append(f"学生不可用 {conflict.student_count or 0} 人：{conflict.reason or '未说明原因'}")
        elif conflict.category == CONFLICT_CLASSROOM:
            if conflict.session_id is not None:
                reasons.append(f"教室已被课程占用：{conflict.course_name or '未知课程'}")
            else:
                reasons.append(f"教室不可用：{conflict.reason or '未说明原因'}")
    return reasons


def _build_option_reasons(
    *,
    conflict_reasons: tuple[str, ...],
    missing_data: tuple[str, ...],
    uses_source_classroom: bool,
) -> tuple[str, ...]:
    reasons = list(conflict_reasons)
    if not reasons:
        if missing_data:
            reasons.append("尚未发现已知冲突，但数据不完整，仅供核验")
        else:
            reasons.append("教师、选课学生及教室均无已知冲突")
    if uses_source_classroom:
        reasons.append("使用原教室")
    reasons.extend(f"缺少数据：{_MISSING_LABELS[kind]}" for kind in missing_data)

    # A course can have two overlapping sessions or several blocked people with
    # the same reason; one readable line per distinct explanation is enough.
    return tuple(dict.fromkeys(reasons))


def _occupancy_course_name(occupied) -> str:
    name = getattr(occupied, "course_name", "")
    if name:
        return name
    offering = getattr(occupied, "offering", None)
    course = getattr(offering, "course", None)
    return getattr(course, "name", "")


def _target_times(
    term,
    periods: dict[int, Period],
    source: ClassSession,
    *,
    day: date,
    start_period: int,
    end_period: int,
    require_same_span: bool = True,
    require_same_block: bool = True,
) -> tuple[datetime | None, datetime | None, str | None]:
    """Validate period geometry and return target start/end times.

    This is only the structural period check; calendar, weekend, past-time,
    room, capacity and equipment checks live in the evaluator so they use the
    same preloaded data as every conflict check.  ``require_same_span`` and
    ``require_same_block`` are reschedule-policy rules (keep a move to the same
    number of periods and within one morning/afternoon block); imports may turn
    them off without touching the B30/B31 defaults.
    """
    if start_period < 1 or end_period < start_period:
        return None, None, "目标节次范围无效。"
    if require_same_span:
        span = source.end_period - source.start_period + 1
        if end_period - start_period + 1 != span:
            return None, None, f"目标节次数必须与源课次一致（{span} 节）。"

    selected = [periods.get(number) for number in range(start_period, end_period + 1)]
    if any(period is None for period in selected):
        missing = [
            number
            for number, period in zip(range(start_period, end_period + 1), selected, strict=True)
            if period is None
        ]
        return None, None, f"目标节次不存在：第 {'、'.join(str(number) for number in missing)} 节。"
    assert all(period is not None for period in selected)
    block_code = selected[0].block_code
    if require_same_block and any(period.block_code != block_code for period in selected):
        return None, None, "目标节次必须位于同一时段组，不能跨午休或上下午。"

    start_time = selected[0].start_time
    end_time = selected[-1].end_time
    if end_time <= start_time:
        return None, None, "目标节次结束时间必须晚于开始时间。"
    tzinfo = ZoneInfo(term.timezone)
    return (
        datetime.combine(day, start_time, tzinfo=tzinfo),
        datetime.combine(day, end_time, tzinfo=tzinfo),
        None,
    )


def _load_evaluation_data(
    session: ClassSession,
    *,
    periods: dict[int, Period],
    exception_dates: set[date] | frozenset[date],
    target_days: tuple[date, ...],
    search_start: datetime,
    search_end: datetime,
    classrooms: tuple[Classroom, ...],
    source_offering_ids: set[UUID] | None = None,
    source_teacher_ids: set[UUID] | None = None,
    extra_session_ids: set[UUID] | None = None,
    keep_source_session: bool = False,
    excluded_session_id: UUID | None = None,
    replaced_session_ids: set[UUID] | None = None,
    overlay_sessions: list[ClassSession] | None = None,
    overlay_teachers: dict[UUID, set[UUID]] | None = None,
    replaced_room_block_ids: set[UUID] | None = None,
    overlay_room_blocks: list[RoomBlock] | None = None,
    replaced_person_block_ids: set[UUID] | None = None,
    overlay_person_blocks: list[PersonBlock] | None = None,
) -> _EvaluationData:
    """Load every row needed for all targets in this window.

    Eight fixed queries (periods/exceptions are loaded by the caller): schedule
    occupancy, session teachers, enrolments, person blocks, room blocks and
    coverage.  Nothing in the candidate loop below issues a query.

    ``keep_source_session=True`` is used by the import batch evaluator: it loads
    all sessions once and each per-target evaluator skips its own row via
    ``excluded_session_id``.  Defaults preserve the B30/B31 single-session
    behaviour exactly.
    """
    offering = session.offering
    term = offering.term
    tzinfo = ZoneInfo(term.timezone)
    target_day_set = set(target_days)
    first_target_day = target_days[0]
    last_target_day = target_days[-1]

    def covered_local_days(start_at: datetime, end_at: datetime) -> tuple[date, ...]:
        """Index an occupying record under every school-local date it covers.

        Sessions do not cross midnight in the first version, but the calendar-
        day index still expands any such record so a candidate on the next day
        cannot silently miss it.  End is exclusive, so an end exactly at
        midnight belongs to the previous day.
        """
        first = start_at.astimezone(tzinfo).date()
        last = (end_at - timedelta(microseconds=1)).astimezone(tzinfo).date()
        if last < first:
            return ()
        day = max(first, first_target_day)
        final = min(last, last_target_day)
        days: list[date] = []
        while day <= final:
            if day in target_day_set:
                days.append(day)
            day += timedelta(days=1)
        return tuple(days)

    # One query for every session in the same school that could overlap any
    # candidate, regardless of which term owns it.  Time across the whole
    # school must be considered, not only the source term's offerings.
    occupancy_sessions_query = ClassSession.objects.filter(
        offering__term__school_id=term.school_id,
        status__in=(SessionStatus.SCHEDULED, SessionStatus.COMPLETED),
        start_at__lt=search_end,
        end_at__gt=search_start,
    ).select_related("offering__course")
    if not keep_source_session:
        occupancy_sessions_query = occupancy_sessions_query.exclude(pk=session.pk)
    if replaced_session_ids:
        occupancy_sessions_query = occupancy_sessions_query.exclude(pk__in=replaced_session_ids)
    occupancy_sessions = list(occupancy_sessions_query.order_by("start_at", "id"))
    if overlay_sessions:
        occupancy_sessions.extend(overlay_sessions)
    session_ids = [session.id, *(extra_session_ids or ()), *(occupied.id for occupied in occupancy_sessions)]
    teacher_links = list(
        SessionTeacher.objects.filter(session_id__in=session_ids)
        .select_related("teacher")
        .order_by("session_id", "teacher__teacher_no")
    )
    teachers_by_session: dict[UUID, set[UUID]] = defaultdict(set)
    teachers_by_id: dict[UUID, TeacherProfile] = {}
    source_teachers: dict[UUID, TeacherProfile] = {}
    for link in teacher_links:
        teachers_by_session[link.session_id].add(link.teacher_id)
        teachers_by_id[link.teacher_id] = link.teacher
        if link.session_id == session.id:
            source_teachers[link.teacher_id] = link.teacher
    for session_id, teacher_ids in (overlay_teachers or {}).items():
        teachers_by_session[session_id] = set(teacher_ids)
    if source_teacher_ids is not None:
        missing_teachers = set(source_teacher_ids) - set(teachers_by_id)
        if missing_teachers:
            teachers_by_id.update(
                {teacher.id: teacher for teacher in TeacherProfile.objects.filter(pk__in=missing_teachers)}
            )
    if source_teacher_ids is None:
        ordered_source_teachers = tuple(sorted(source_teachers.values(), key=lambda teacher: teacher.teacher_no))
        source_teacher_ids = {teacher.id for teacher in ordered_source_teachers}
    else:
        ordered_source_teachers = tuple(
            sorted(
                (teachers_by_id[teacher_id] for teacher_id in source_teacher_ids if teacher_id in teachers_by_id),
                key=lambda teacher: teacher.teacher_no,
            )
        )

    # Load all relevant enrolment intervals in one query.  Student numbers are
    # joined here because coverage keys use them, but they are never returned.
    if source_offering_ids is None:
        source_offering_ids = {offering.id}
    offering_ids = {
        *source_offering_ids,
        *(occupied.offering_id for occupied in occupancy_sessions),
        *(overlay.offering_id for overlay in (overlay_sessions or ())),
    }
    enrollment_rows = list(
        Enrollment.objects.filter(
            offering_id__in=offering_ids,
            effective_from__lte=target_days[-1],
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gt=target_days[0]))
        .values_list(
            "offering_id",
            "student_id",
            "student__student_no",
            "effective_from",
            "effective_until",
        )
        .order_by("offering_id", "student_id", "effective_from")
    )
    enrolments_by_offering: dict[UUID, list[_EnrollmentInterval]] = defaultdict(list)
    student_numbers: dict[UUID, str] = {}
    for offering_id, student_id, student_no, effective_from, effective_until in enrollment_rows:
        enrolments_by_offering[offering_id].append(
            _EnrollmentInterval(
                student_id=student_id,
                student_no=student_no,
                effective_from=effective_from,
                effective_until=effective_until,
            )
        )
        student_numbers[student_id] = student_no

    occupancy_by_day: dict[date, list[ClassSession]] = defaultdict(list)
    occupancy_by_day_room: dict[tuple[date, UUID], list[ClassSession]] = defaultdict(list)
    for occupied in occupancy_sessions:
        for day in covered_local_days(occupied.start_at, occupied.end_at):
            occupancy_by_day[day].append(occupied)
            occupancy_by_day_room[(day, occupied.classroom_id)].append(occupied)

    source_teacher_ids = {teacher.id for teacher in ordered_source_teachers}
    # Person-block filtering is deliberately name-independent.  For the source
    # offering we need its own active students, not students from other
    # offerings that happen to overlap the target window.
    source_student_ids: set[UUID] = set()
    for offering_id in source_offering_ids:
        for interval in enrolments_by_offering.get(offering_id, ()):
            if any(interval.active_on(day) for day in target_days):
                source_student_ids.add(interval.student_id)

    person_blocks_query = PersonBlock.objects.filter(
        Q(teacher_id__in=source_teacher_ids) | Q(student_id__in=source_student_ids),
        start_at__lt=search_end,
        end_at__gt=search_start,
    )
    if replaced_person_block_ids:
        person_blocks_query = person_blocks_query.exclude(pk__in=replaced_person_block_ids)
    person_blocks = list(person_blocks_query.order_by("start_at", "id"))
    person_blocks.extend(overlay_person_blocks or ())
    person_blocks_by_day: dict[date, list[PersonBlock]] = defaultdict(list)
    for block in person_blocks:
        for day in covered_local_days(block.start_at, block.end_at):
            person_blocks_by_day[day].append(block)

    room_blocks_query = RoomBlock.objects.filter(
        classroom_id__in=[classroom.id for classroom in classrooms],
        start_at__lt=search_end,
        end_at__gt=search_start,
    )
    if replaced_room_block_ids:
        room_blocks_query = room_blocks_query.exclude(pk__in=replaced_room_block_ids)
    room_blocks = list(room_blocks_query.order_by("start_at", "id"))
    room_blocks.extend(overlay_room_blocks or ())
    room_blocks_by_day_room: dict[tuple[date, UUID], list[RoomBlock]] = defaultdict(list)
    for block in room_blocks:
        for day in covered_local_days(block.start_at, block.end_at):
            room_blocks_by_day_room[(day, block.classroom_id)].append(block)

    coverage_rows = list(
        DataCoverage.objects.filter(
            term=term,
            from_date__lt=target_days[-1] + timedelta(days=1),
            until_date__gt=target_days[0],
        ).values_list("kind", "scope_key", "from_date", "until_date", "completeness")
    )
    coverage = _CoverageLookup(coverage_rows)

    return _EvaluationData(
        session=session,
        term=term,
        periods=periods,
        exception_dates=frozenset(exception_dates),
        classrooms=classrooms,
        target_days=target_days,
        occupancy_by_day={day: tuple(rows) for day, rows in occupancy_by_day.items()},
        occupancy_by_day_room={key: tuple(rows) for key, rows in occupancy_by_day_room.items()},
        teachers_by_session=dict(teachers_by_session),
        teachers_by_id=teachers_by_id,
        source_teachers=ordered_source_teachers,
        enrolments_by_offering=dict(enrolments_by_offering),
        student_numbers=student_numbers,
        person_blocks_by_day={day: tuple(rows) for day, rows in person_blocks_by_day.items()},
        room_blocks_by_day_room={key: tuple(rows) for key, rows in room_blocks_by_day_room.items()},
        coverage=coverage,
        required_equipment=frozenset(offering.required_equipment or ()),
        excluded_session_id=excluded_session_id,
    )


class _TargetEvaluator:
    """The single target-evaluation algorithm used by B30, B31 and imports."""

    def __init__(
        self,
        data: _EvaluationData,
        *,
        policy: str = POLICY_RESCHEDULE,
        start_at_override: datetime | None = None,
        end_at_override: datetime | None = None,
    ) -> None:
        self.data = data
        self.session = data.session
        self.offering = self.session.offering
        self.term = data.term
        self.tzinfo = ZoneInfo(self.term.timezone)
        self.policy = policy
        self.import_policy = policy == POLICY_IMPORT
        self.start_at_override = start_at_override
        self.end_at_override = end_at_override
        self.excluded_session_id = data.excluded_session_id
        self.source_teacher_ids = {teacher.id for teacher in data.source_teachers}
        self._active_students_cache: dict[tuple[UUID, date], set[UUID]] = {}
        self.source_students_by_day = {day: self._active_students(self.offering.id, day) for day in data.target_days}
        self._common_cache: dict[tuple[date, int, int], tuple] = {}

    def _active_students(self, offering_id: UUID, day: date) -> set[UUID]:
        key = (offering_id, day)
        if key not in self._active_students_cache:
            self._active_students_cache[key] = {
                row.student_id for row in self.data.enrolments_by_offering.get(offering_id, ()) if row.active_on(day)
            }
        return self._active_students_cache[key]

    def _not_allowed(
        self,
        detail: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        field_name: str | None = None,
    ) -> TargetEvaluation:
        field_errors = {field_name: [detail]} if field_name else {}
        return TargetEvaluation(
            eligibility=ELIGIBILITY_NOT_ALLOWED,
            conflicts=(),
            missing_data=(),
            student_conflict_count=0,
            reasons=(detail,),
            start_at=start_at,
            end_at=end_at,
            code=TARGET_NOT_ALLOWED,
            detail=detail,
            field_errors=field_errors,
        )

    def evaluate(
        self,
        *,
        start_period: int,
        end_period: int,
        day: date,
        classroom: Classroom,
        now: datetime,
        allow_weekend: bool = False,
    ) -> TargetEvaluation:
        start_at, end_at, geometry_error = _target_times(
            self.term,
            self.data.periods,
            self.session,
            day=day,
            start_period=start_period,
            end_period=end_period,
            require_same_span=not self.import_policy,
            require_same_block=not self.import_policy,
        )
        if geometry_error is not None:
            return self._not_allowed(geometry_error, start_at=start_at, end_at=end_at)
        if self.import_policy and (self.start_at_override is not None or self.end_at_override is not None):
            start_at = self.start_at_override or start_at
            end_at = self.end_at_override or end_at
            if start_at is None or end_at is None or end_at <= start_at:
                return self._not_allowed("导入目标时间无效。", start_at=start_at, end_at=end_at)

        if teaching_week(self.term, day) is None:
            return self._not_allowed(
                "目标日期不在学期教学周内。", start_at=start_at, end_at=end_at, field_name="target_date"
            )
        if not self.import_policy:
            if not allow_weekend and day.weekday() >= 5:
                return self._not_allowed(
                    "目标日期是周末，当前不允许周末调课。",
                    start_at=start_at,
                    end_at=end_at,
                    field_name="target_date",
                )
            if day in self.data.exception_dates:
                return self._not_allowed(
                    "目标日期存在校历例外，不能作为调课目标。",
                    start_at=start_at,
                    end_at=end_at,
                    field_name="target_date",
                )
            if start_at <= now:
                return self._not_allowed(
                    "目标开始时间必须晚于当前时间。",
                    start_at=start_at,
                    end_at=end_at,
                    field_name="target_date",
                )
        if classroom.school_id != self.term.school_id:
            return self._not_allowed(
                "目标教室必须与学期属于同一学校。",
                start_at=start_at,
                end_at=end_at,
                field_name="target_classroom_id",
            )
        if not self.import_policy and not classroom.is_active:
            return self._not_allowed(
                "目标教室已停用，不能作为调课目标。",
                start_at=start_at,
                end_at=end_at,
                field_name="target_classroom_id",
            )

        source_local_start = self.session.start_at.astimezone(self.tzinfo)
        source_day = source_local_start.date()
        same_target = (
            day == source_day
            and start_period == self.session.start_period
            and end_period == self.session.end_period
            and classroom.id == self.session.classroom_id
        )
        if same_target and not self.import_policy:
            return self._not_allowed(
                "目标与当前课次完全相同，无需调课。",
                start_at=start_at,
                end_at=end_at,
            )

        source_students = self.source_students_by_day.get(day, set())
        if not self.import_policy and classroom.capacity < len(source_students):
            return self._not_allowed(
                f"教室容量不足：目标日有效选课 {len(source_students)} 人，教室容量 {classroom.capacity} 人。",
                start_at=start_at,
                end_at=end_at,
                field_name="target_classroom_id",
            )
        missing_equipment = self.data.required_equipment - set(classroom.equipment or ())
        if not self.import_policy and missing_equipment:
            missing_text = "、".join(sorted(missing_equipment))
            return self._not_allowed(
                f"教室缺少必需设备：{missing_text}。",
                start_at=start_at,
                end_at=end_at,
                field_name="target_classroom_id",
            )

        common = self._common_for(
            day,
            start_period,
            end_period,
            start_at_override=start_at if self.import_policy else None,
            end_at_override=end_at if self.import_policy else None,
        )
        common_conflicts, common_conflict_reasons, common_missing_data, student_conflict_count = common

        classroom_conflicts: list[ConflictDetail] = []
        for occupied in self.data.occupancy_by_day_room.get((day, classroom.id), ()):
            if occupied.id == self.excluded_session_id:
                continue
            if _overlaps(start_at, end_at, occupied.start_at, occupied.end_at):
                classroom_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_CLASSROOM,
                        session_id=occupied.id,
                        course_name=_occupancy_course_name(occupied),
                    )
                )

        for block in self.data.room_blocks_by_day_room.get((day, classroom.id), ()):
            if _overlaps(start_at, end_at, block.start_at, block.end_at):
                classroom_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_CLASSROOM,
                        reason=block.reason,
                    )
                )

        conflicts = common_conflicts + tuple(classroom_conflicts)
        room_missing_data = _missing_data_room_for(
            self.data.coverage,
            day=day,
            classroom_code=classroom.classroom_code,
        )
        missing_data = _merge_missing_data(common_missing_data, room_missing_data)
        if conflicts:
            eligibility = ELIGIBILITY_CONFLICT
        elif missing_data:
            eligibility = ELIGIBILITY_UNVERIFIED
        else:
            eligibility = ELIGIBILITY_AVAILABLE

        conflict_reasons = common_conflict_reasons + tuple(_conflict_reasons(tuple(classroom_conflicts)))
        reasons = _build_option_reasons(
            conflict_reasons=conflict_reasons,
            missing_data=missing_data,
            uses_source_classroom=classroom.id == self.session.classroom_id,
        )
        if eligibility == ELIGIBILITY_CONFLICT:
            code = RESCHEDULE_CONFLICT
            detail = "目标时段存在已知冲突，不能普通提交或批准。"
        elif eligibility == ELIGIBILITY_UNVERIFIED:
            code = DATA_UNVERIFIED
            detail = "目标数据不完整，不能普通批准；请先核验或登记学校强制安排。"
        else:
            code = None
            detail = "教师、选课学生及教室均无已知冲突。"
        return TargetEvaluation(
            eligibility=eligibility,
            conflicts=conflicts,
            missing_data=missing_data,
            student_conflict_count=student_conflict_count,
            reasons=reasons,
            start_at=start_at,
            end_at=end_at,
            code=code,
            detail=detail,
        )

    def _common_for(
        self,
        day: date,
        start_period: int,
        end_period: int,
        *,
        start_at_override: datetime | None = None,
        end_at_override: datetime | None = None,
    ) -> tuple:
        """Teacher/student conflicts and non-room coverage for one time slot.

        Cached because B30 evaluates every classroom of a slot; the cache is
        keyed by the exact slot, not by candidate id, so the result is identical
        to evaluating one target in isolation.
        """
        key = (day, start_period, end_period, start_at_override, end_at_override)
        if key in self._common_cache:
            return self._common_cache[key]

        start_at, end_at, geometry_error = _target_times(
            self.term,
            self.data.periods,
            self.session,
            day=day,
            start_period=start_period,
            end_period=end_period,
            require_same_span=not self.import_policy,
            require_same_block=not self.import_policy,
        )
        assert geometry_error is None and start_at is not None and end_at is not None
        if start_at_override is not None or end_at_override is not None:
            start_at = start_at_override or start_at
            end_at = end_at_override or end_at
        source_students = self.source_students_by_day.get(day, set())
        source_students_for_coverage = tuple(
            sorted(
                ((student_id, self.data.student_numbers[student_id]) for student_id in source_students),
                key=lambda item: item[1],
            )
        )

        teacher_conflicts: list[ConflictDetail] = []
        student_conflicts: list[ConflictDetail] = []
        conflicting_student_ids: set[UUID] = set()

        for occupied in self.data.occupancy_by_day.get(day, ()):
            if occupied.id == self.excluded_session_id:
                continue
            if not _overlaps(start_at, end_at, occupied.start_at, occupied.end_at):
                continue
            if self.data.teachers_by_session.get(occupied.id, set()) & self.source_teacher_ids:
                teacher_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_TEACHER,
                        session_id=occupied.id,
                        course_name=_occupancy_course_name(occupied),
                    )
                )

            overlap_students = self._active_students(occupied.offering_id, day) & source_students
            if overlap_students:
                conflicting_student_ids.update(overlap_students)
                student_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_STUDENT,
                        session_id=occupied.id,
                        course_name=_occupancy_course_name(occupied),
                        student_count=len(overlap_students),
                    )
                )

        for block in self.data.person_blocks_by_day.get(day, ()):
            if not _overlaps(start_at, end_at, block.start_at, block.end_at):
                continue
            if block.teacher_id in self.source_teacher_ids:
                teacher_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_TEACHER,
                        reason=block.reason,
                    )
                )
            if block.student_id in source_students:
                conflicting_student_ids.add(block.student_id)
                student_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_STUDENT,
                        reason=block.reason,
                        student_count=1,
                    )
                )

        common_conflicts = tuple(teacher_conflicts + student_conflicts)
        common_missing_data = _missing_data_common_for(
            self.data.coverage,
            day=day,
            offering_code=self.offering.offering_code,
            teachers=self.data.source_teachers,
            students=source_students_for_coverage,
        )
        result = (
            common_conflicts,
            tuple(_conflict_reasons(common_conflicts)),
            common_missing_data,
            len(conflicting_student_ids),
        )
        self._common_cache[key] = result
        return result


def evaluate_import_targets(
    requests: list[ImportEvaluationRequest],
    *,
    periods: dict[int, Period],
    now: datetime,
    replaced_session_ids: set[UUID] | None = None,
    occupancy_targets: list[ImportOccupancyTarget] | None = None,
    replaced_room_block_ids: set[UUID] | None = None,
    overlay_room_blocks: list[RoomBlock] | None = None,
    replaced_person_block_ids: set[UUID] | None = None,
    overlay_person_blocks: list[PersonBlock] | None = None,
) -> dict[UUID, TargetEvaluation]:
    """Evaluate many import targets against one preloaded snapshot per term.

    The query count depends on the number of affected terms/windows, not on the
    number of moved/restored sessions.  It reuses the exact B30 loader and
    :class:`_TargetEvaluator` algorithm with ``policy=POLICY_IMPORT``.

    All requests are treated as the batch's final target occupancy while their
    previous database rows are removed from the snapshot.  This makes two
    sessions moving into the same slot visible to each other, and stops a slot
    that another session is vacating from looking occupied.  Commit calls it
    again with only the writes that will actually be applied, so kept sessions
    remain at their old slots.
    """
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")
    if not requests:
        return {}

    if replaced_session_ids is None:
        replaced_session_ids = {request.session.id for request in requests}
    if occupancy_targets is None:
        default_session_ids = {request.session.id for request in requests}
        default_teachers: dict[UUID, set[UUID]] = defaultdict(set)
        for session_id, teacher_id in SessionTeacher.objects.filter(session_id__in=default_session_ids).values_list(
            "session_id", "teacher_id"
        ):
            default_teachers[session_id].add(teacher_id)
        occupancy_targets = [
            ImportOccupancyTarget(
                id=request.session.id,
                offering_id=request.session.offering_id,
                start_at=request.start_at,
                end_at=request.end_at,
                classroom_id=request.classroom.id,
                teacher_ids=tuple(
                    request.teacher_ids
                    if request.teacher_ids is not None
                    else default_teachers.get(request.session.id, set())
                ),
                status=request.status,
                course_name=request.session.offering.offering_code,
            )
            for request in requests
        ]

    by_term: dict[UUID, list[ImportEvaluationRequest]] = defaultdict(list)
    for request in requests:
        by_term[request.session.offering.term_id].append(request)

    overlay_sessions: list[ClassSession] = []
    overlay_teachers: dict[UUID, set[UUID]] = {}
    source_teacher_ids: set[UUID] = set()
    for target in occupancy_targets:
        overlay_teachers[target.id] = set(target.teacher_ids)
        source_teacher_ids.update(target.teacher_ids)
        overlay = ClassSession(
            id=target.id,
            offering_id=target.offering_id,
            start_at=target.start_at,
            end_at=target.end_at,
            classroom_id=target.classroom_id,
            status=target.status,
        )
        overlay.course_name = target.course_name
        overlay_sessions.append(overlay)
    all_offering_ids = {request.session.offering_id for request in requests} | {
        target.offering_id for target in occupancy_targets
    }

    results: dict[UUID, TargetEvaluation] = {}
    for term_requests in by_term.values():
        first = term_requests[0]
        term = first.session.offering.term
        tzinfo = ZoneInfo(term.timezone)
        target_days = tuple(sorted({request.day for request in term_requests}))
        search_start = datetime.combine(target_days[0], time.min, tzinfo=tzinfo)
        search_end = datetime.combine(target_days[-1] + timedelta(days=1), time.min, tzinfo=tzinfo)
        classrooms = {request.classroom.id: request.classroom for request in term_requests}
        source_session_ids = {request.session.id for request in term_requests}
        exception_dates = set(CalendarException.objects.filter(term=term).values_list("date", flat=True))
        data = _load_evaluation_data(
            first.session,
            periods=periods,
            exception_dates=exception_dates,
            target_days=target_days,
            search_start=search_start,
            search_end=search_end,
            classrooms=tuple(classrooms.values()),
            source_offering_ids=all_offering_ids,
            source_teacher_ids=source_teacher_ids,
            extra_session_ids=source_session_ids,
            keep_source_session=True,
            replaced_session_ids=replaced_session_ids,
            overlay_sessions=overlay_sessions,
            overlay_teachers=overlay_teachers,
            replaced_room_block_ids=replaced_room_block_ids,
            overlay_room_blocks=overlay_room_blocks,
            replaced_person_block_ids=replaced_person_block_ids,
            overlay_person_blocks=overlay_person_blocks,
        )
        for request in term_requests:
            teacher_ids = overlay_teachers.get(request.session.id, set())
            source_teachers = tuple(
                sorted(
                    (
                        data.teachers_by_id[teacher_id]
                        for teacher_id in teacher_ids
                        if teacher_id in data.teachers_by_id
                    ),
                    key=lambda teacher: teacher.teacher_no,
                )
            )
            target_data = replace(
                data,
                session=request.session,
                source_teachers=source_teachers,
                required_equipment=frozenset(request.session.offering.required_equipment or ()),
                excluded_session_id=request.session.id,
            )
            evaluator = _TargetEvaluator(
                target_data,
                policy=POLICY_IMPORT,
                start_at_override=request.start_at,
                end_at_override=request.end_at,
            )
            results[request.session.id] = evaluator.evaluate(
                start_period=request.start_period,
                end_period=request.end_period,
                day=request.day,
                classroom=request.classroom,
                now=now,
            )
    return results


def evaluate_target(
    session: ClassSession,
    *,
    start_period: int,
    end_period: int,
    day: date,
    classroom: Classroom,
    now: datetime,
    allow_weekend: bool = False,
    policy: str = POLICY_RESCHEDULE,
    periods: dict[int, Period] | None = None,
    start_at_override: datetime | None = None,
    end_at_override: datetime | None = None,
) -> TargetEvaluation:
    """Evaluate one exact target with a constant number of queries.

    B31 uses this for submission and both approval paths; B30 invokes the same
    :class:`_TargetEvaluator` over one preloaded snapshot so candidate loops do
    not issue per-candidate queries.  The source session is excluded from all
    occupancy checks, exactly as in B30.

    ``policy=POLICY_IMPORT`` is the B12 entry point: it skips only the
    reschedule policy conditions (future time, weekend, calendar-exception day,
    period block, unchanged target, capacity/equipment, source status) and keeps
    the teacher/student/classroom conflict calculation.  ``periods`` and the
    start/end overrides let the preview evaluate the *effective* period times
    from the batch before they are written.  The defaults are the unchanged
    B30/B31 behaviour.
    """
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")

    if policy != POLICY_IMPORT:
        _check_source_status(session)
    offering = session.offering
    term = offering.term
    tzinfo = ZoneInfo(term.timezone)
    if periods is None:
        periods = {period.period_number: period for period in Period.objects.filter(term=term)}
    exception_dates = set(CalendarException.objects.filter(term=term).values_list("date", flat=True))

    # A whole local day is loaded even for an invalid target; this keeps the
    # public call's query count constant instead of branching into different
    # query sets based on validation errors.
    search_start = datetime.combine(day, time.min, tzinfo=tzinfo)
    search_end = search_start + timedelta(days=1)
    data = _load_evaluation_data(
        session,
        periods=periods,
        exception_dates=exception_dates,
        target_days=(day,),
        search_start=search_start,
        search_end=search_end,
        classrooms=(classroom,),
    )
    evaluator = _TargetEvaluator(
        data,
        policy=policy,
        start_at_override=start_at_override,
        end_at_override=end_at_override,
    )
    return evaluator.evaluate(
        start_period=start_period,
        end_period=end_period,
        day=day,
        classroom=classroom,
        now=now,
        allow_weekend=allow_weekend,
    )


def _empty_result(session: ClassSession, term, now: datetime) -> OptionResult:
    return OptionResult(
        session_id=session.id,
        schedule_version=term.schedule_version,
        checked_at=now,
        data_completeness="complete",
        options=(),
    )


def find_reschedule_options(session: ClassSession, query: OptionQuery, *, now: datetime) -> OptionResult:
    """Return grouped, explained reschedule candidates for ``session``.

    ``now`` is supplied by the caller so tests are deterministic and the
    service never reads the wall clock itself.  The source session is never
    modified.
    """
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")

    _check_source_status(session)
    offering = session.offering
    term = offering.term
    start_date, end_date = _clip_date_range(term, query)
    if end_date <= start_date:
        return _empty_result(session, term, now)

    tzinfo = ZoneInfo(term.timezone)
    source_local_start = session.start_at.astimezone(tzinfo)
    source_date = source_local_start.date()
    source_start_minutes = source_local_start.hour * 60 + source_local_start.minute

    periods = {period.period_number: period for period in Period.objects.filter(term=term)}
    exception_dates = set(CalendarException.objects.filter(term=term).values_list("date", flat=True))
    slots = _candidate_slots(
        term=term,
        start_date=start_date,
        end_date=end_date,
        source=session,
        periods=periods,
        exception_dates=exception_dates,
        allowed_block_codes=query.allowed_block_codes,
        allow_weekend=query.allow_weekend,
        now=now,
    )
    if not slots:
        return _empty_result(session, term, now)

    classroom_qs = Classroom.objects.filter(school_id=term.school_id, is_active=True)
    if query.classroom_ids is not None:
        classroom_qs = classroom_qs.filter(pk__in=query.classroom_ids)
    classrooms = tuple(classroom_qs.order_by("classroom_code", "id"))
    if not classrooms:
        return _empty_result(session, term, now)

    target_days = tuple(sorted({slot.day for slot in slots}))
    search_start = min(slot.start_at for slot in slots)
    search_end = max(slot.end_at for slot in slots)
    data = _load_evaluation_data(
        session,
        periods=periods,
        exception_dates=exception_dates,
        target_days=target_days,
        search_start=search_start,
        search_end=search_end,
        classrooms=classrooms,
    )
    evaluator = _TargetEvaluator(data)

    options: list[RescheduleOption] = []
    for slot in slots:
        for classroom in classrooms:
            evaluation = evaluator.evaluate(
                start_period=slot.start_period,
                end_period=slot.end_period,
                day=slot.day,
                classroom=classroom,
                now=now,
                allow_weekend=query.allow_weekend,
            )
            if evaluation.eligibility == ELIGIBILITY_NOT_ALLOWED:
                # Hard conditions (capacity, equipment, inactive room, source
                # slot itself) are not candidates.  B30 used to filter these in
                # the loop; the shared evaluator now owns that decision.
                continue
            options.append(
                RescheduleOption.from_evaluation(
                    evaluation,
                    start_period=slot.start_period,
                    end_period=slot.end_period,
                    classroom=classroom,
                )
            )

    def sort_key(option: RescheduleOption):
        local_start = option.start_at.astimezone(tzinfo)
        return (
            _ELIGIBILITY_ORDER[option.eligibility],
            0 if option.classroom_id == session.classroom_id else 1,
            abs((local_start.date() - source_date).days),
            abs((local_start.hour * 60 + local_start.minute) - source_start_minutes),
            option.start_at,
            option.end_at,
            option.classroom_code,
        )

    options.sort(key=sort_key)
    data_completeness = "complete" if all(not option.missing_data for option in options) else "partial"
    return OptionResult(
        session_id=session.id,
        schedule_version=term.schedule_version,
        checked_at=now,
        data_completeness=data_completeness,
        options=tuple(options),
    )
