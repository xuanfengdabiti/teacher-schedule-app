"""Read-only reschedule candidate generation and conflict checking (B30).

The service deliberately never writes a session.  It expands target dates and
contiguous periods, then answers three separate questions for every candidate:

* is there a known teacher/student/classroom conflict?
* is the data needed to trust a "no conflict" answer complete for that date?
* where should the option appear in the stable recommendation order?

All schedule, enrolment, block and coverage rows are fetched in a fixed number
of queries before the in-memory candidate loop.  Candidate count (14 vs 60
days) must not change the query count; see A04-A08, A21, A22 and A26 in
``docs/domain-design.md`` section 10.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
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
    """Small business error owned by this endpoint.

    The project does not yet have a global error framework.  The view catches
    this exception and renders the unified ``code/detail/field_errors`` body.
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


def _check_source_status(session: ClassSession) -> None:
    if session.status != SessionStatus.SCHEDULED:
        raise RescheduleOptionsError(
            "SESSION_NOT_RESCHEDULABLE",
            "只有待上课的课次可以查询调课候选，已完成或已取消的课次不能调课。",
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
    classrooms = list(classroom_qs.order_by("classroom_code", "id"))
    if not classrooms:
        return _empty_result(session, term, now)

    target_days = tuple(sorted({slot.day for slot in slots}))
    target_day_set = set(target_days)
    first_target_day = target_days[0]
    last_target_day = target_days[-1]
    search_start = min(slot.start_at for slot in slots)
    search_end = max(slot.end_at for slot in slots)

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
    occupancy_sessions = list(
        ClassSession.objects.filter(
            offering__term__school_id=term.school_id,
            status__in=(SessionStatus.SCHEDULED, SessionStatus.COMPLETED),
            start_at__lt=search_end,
            end_at__gt=search_start,
        )
        .exclude(pk=session.pk)
        .select_related("offering__course")
        .order_by("start_at", "id")
    )
    session_ids = [session.id, *(occupancy.id for occupancy in occupancy_sessions)]
    teacher_links = list(
        SessionTeacher.objects.filter(session_id__in=session_ids)
        .select_related("teacher")
        .order_by("session_id", "teacher__teacher_no")
    )
    teachers_by_session: dict[UUID, set[UUID]] = defaultdict(set)
    source_teachers: dict[UUID, TeacherProfile] = {}
    for link in teacher_links:
        teachers_by_session[link.session_id].add(link.teacher_id)
        if link.session_id == session.id:
            source_teachers[link.teacher_id] = link.teacher
    source_teacher_ids = set(source_teachers)
    ordered_source_teachers = tuple(sorted(source_teachers.values(), key=lambda teacher: teacher.teacher_no))

    # Load all relevant enrolment intervals in one query.  Student numbers are
    # joined here because coverage keys use them, but they are never returned.
    offering_ids = {offering.id, *(over.offering_id for over in occupancy_sessions)}
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

    active_students_cache: dict[tuple[UUID, date], set[UUID]] = {}

    def active_students(offering_id: UUID, day: date) -> set[UUID]:
        key = (offering_id, day)
        if key not in active_students_cache:
            active_students_cache[key] = {
                row.student_id for row in enrolments_by_offering.get(offering_id, ()) if row.active_on(day)
            }
        return active_students_cache[key]

    source_students_by_day = {day: active_students(offering.id, day) for day in target_days}
    source_student_union = set().union(*source_students_by_day.values())

    occupancy_by_day: dict[date, list[ClassSession]] = defaultdict(list)
    occupancy_by_day_room: dict[tuple[date, UUID], list[ClassSession]] = defaultdict(list)
    for occupied in occupancy_sessions:
        for day in covered_local_days(occupied.start_at, occupied.end_at):
            occupancy_by_day[day].append(occupied)
            occupancy_by_day_room[(day, occupied.classroom_id)].append(occupied)

    person_blocks = list(
        PersonBlock.objects.filter(
            Q(teacher_id__in=source_teacher_ids) | Q(student_id__in=source_student_union),
            start_at__lt=search_end,
            end_at__gt=search_start,
        ).order_by("start_at", "id")
    )
    person_blocks_by_day: dict[date, list[PersonBlock]] = defaultdict(list)
    for block in person_blocks:
        for day in covered_local_days(block.start_at, block.end_at):
            person_blocks_by_day[day].append(block)

    room_blocks = list(
        RoomBlock.objects.filter(
            classroom_id__in=[classroom.id for classroom in classrooms],
            start_at__lt=search_end,
            end_at__gt=search_start,
        ).order_by("start_at", "id")
    )
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

    required_equipment = set(offering.required_equipment or ())
    options: list[RescheduleOption] = []

    for slot in slots:
        source_students = source_students_by_day[slot.day]
        source_students_for_coverage = tuple(
            sorted(
                ((student_id, student_numbers[student_id]) for student_id in source_students),
                key=lambda item: item[1],
            )
        )
        source_student_count = len(source_students)

        # Teacher and student conflicts, plus six of the eight coverage
        # categories, do not depend on the candidate classroom.  Compute them
        # once for this time slot; only classroom occupancy and the two
        # classroom-specific coverage kinds vary per room.
        teacher_conflicts: list[ConflictDetail] = []
        student_conflicts: list[ConflictDetail] = []
        conflicting_student_ids: set[UUID] = set()

        for occupied in occupancy_by_day.get(slot.day, ()):
            if not _overlaps(slot.start_at, slot.end_at, occupied.start_at, occupied.end_at):
                continue
            if teachers_by_session[occupied.id] & source_teacher_ids:
                teacher_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_TEACHER,
                        session_id=occupied.id,
                        course_name=occupied.offering.course.name,
                    )
                )

            overlap_students = active_students(occupied.offering_id, slot.day) & source_students
            if overlap_students:
                conflicting_student_ids.update(overlap_students)
                student_conflicts.append(
                    ConflictDetail(
                        category=CONFLICT_STUDENT,
                        session_id=occupied.id,
                        course_name=occupied.offering.course.name,
                        student_count=len(overlap_students),
                    )
                )

        for block in person_blocks_by_day.get(slot.day, ()):
            if not _overlaps(slot.start_at, slot.end_at, block.start_at, block.end_at):
                continue
            if block.teacher_id in source_teacher_ids:
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
        common_conflict_reasons = tuple(_conflict_reasons(common_conflicts))
        common_missing_data = _missing_data_common_for(
            coverage,
            day=slot.day,
            offering_code=offering.offering_code,
            teachers=ordered_source_teachers,
            students=source_students_for_coverage,
        )

        for classroom in classrooms:
            if classroom.capacity < source_student_count:
                continue
            if not required_equipment.issubset(set(classroom.equipment or ())):
                continue
            if (
                slot.day == source_date
                and slot.start_period == session.start_period
                and slot.end_period == session.end_period
                and classroom.id == session.classroom_id
            ):
                continue

            classroom_conflicts: list[ConflictDetail] = []
            for occupied in occupancy_by_day_room.get((slot.day, classroom.id), ()):
                if _overlaps(slot.start_at, slot.end_at, occupied.start_at, occupied.end_at):
                    classroom_conflicts.append(
                        ConflictDetail(
                            category=CONFLICT_CLASSROOM,
                            session_id=occupied.id,
                            course_name=occupied.offering.course.name,
                        )
                    )

            for block in room_blocks_by_day_room.get((slot.day, classroom.id), ()):
                if _overlaps(slot.start_at, slot.end_at, block.start_at, block.end_at):
                    classroom_conflicts.append(
                        ConflictDetail(
                            category=CONFLICT_CLASSROOM,
                            reason=block.reason,
                        )
                    )

            conflicts = common_conflicts + tuple(classroom_conflicts)
            room_missing_data = _missing_data_room_for(
                coverage,
                day=slot.day,
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
            options.append(
                RescheduleOption(
                    start_at=slot.start_at,
                    end_at=slot.end_at,
                    start_period=slot.start_period,
                    end_period=slot.end_period,
                    classroom_id=classroom.id,
                    classroom_code=classroom.classroom_code,
                    eligibility=eligibility,
                    student_conflict_count=len(conflicting_student_ids),
                    conflicts=conflicts,
                    missing_data=missing_data,
                    reasons=_build_option_reasons(
                        conflict_reasons=conflict_reasons,
                        missing_data=missing_data,
                        uses_source_classroom=classroom.id == session.classroom_id,
                    ),
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
