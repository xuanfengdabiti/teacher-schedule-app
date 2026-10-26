"""Session generation service.

Formal schedule writes must go through a single transactional entry point
(``docs/domain-design.md`` 3.1 and 4.4).  B11 implements rule expansion only:
existing sessions are never rewritten here, mismatches are reported for the
B12 import preview, and rescheduling lives in B31.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.academics.models import CalendarException, Period, TeacherProfile, TeachingAssignment, Term
from apps.scheduling import calendar
from apps.scheduling.models import ClassSession, ScheduleRule, SchoolSyncState, SessionStatus, SessionTeacher


@dataclass(frozen=True)
class SessionMismatch:
    """An existing generated session that no longer matches the rule expansion."""

    session_id: UUID
    rule_id: UUID | None
    origin_teaching_date: date | None
    origin_period: int | None
    differences: tuple[str, ...]


@dataclass(frozen=True)
class GenerationResult:
    """Outcome counts/details of one term generation run.

    ``created``/``unchanged`` are counts; ``mismatched`` is the list of existing
    sessions whose persisted values no longer match the rule expansion.  The
    caller (Admin action or B12 preview) decides what to do with them.
    """

    created: int
    unchanged: int
    mismatched: tuple[SessionMismatch, ...]
    created_session_ids: tuple[UUID, ...]
    unchanged_session_ids: tuple[UUID, ...]
    suspected_duplicates: tuple[calendar.SuspectedDuplicate, ...]
    schedule_version: int

    @property
    def mismatched_count(self) -> int:
        return len(self.mismatched)


def _build_rule_plans(rules: list[ScheduleRule]) -> list[calendar.RulePlan]:
    return [
        calendar.RulePlan(
            rule_id=rule.id,
            rule_code=rule.rule_code,
            offering_id=rule.offering_id,
            weekday=rule.weekday,
            start_period=rule.start_period,
            end_period=rule.end_period,
            weeks=tuple(rule.weeks or ()),
            classroom_id=rule.classroom_id,
            planned_hours=rule.planned_hours,
            teacher_ids=tuple(sorted((teacher.id for teacher in rule.teachers.all()), key=str)),
        )
        for rule in rules
    ]


def _validate_teacher_scope(term: Term, rule_plans: list[calendar.RulePlan]) -> None:
    """Every rule teacher must belong to the offering's assignment scope.

    ``ScheduleRule.clean()`` cannot see the M2M rows on an unsaved instance, so
    the generation service re-checks the scope before writing anything.
    """
    allowed: dict[UUID, set[UUID]] = defaultdict(set)
    rows = TeachingAssignment.objects.filter(offering__term=term).values_list("offering_id", "teacher_id")
    for offering_id, teacher_id in rows:
        allowed[offering_id].add(teacher_id)

    for rule in rule_plans:
        extra = set(rule.teacher_ids) - allowed[rule.offering_id]
        if not extra:
            continue
        names = ", ".join(TeacherProfile.objects.filter(pk__in=extra).values_list("teacher_no", flat=True))
        raise ValidationError(f"规则 {rule.rule_code} 的授课教师不在课程开设的任课范围内：{names}。")


def _status_matches_plan(existing_status: str, planned_status: str) -> bool:
    """Return whether an existing session status is compatible with the plan.

    A planned ``scheduled`` session may already be ``completed``: finishing a
    class is normal business progress, not a deviation from the rule expansion.
    A planned ``cancelled`` session only matches an existing ``cancelled`` row;
    an already completed class must still be surfaced for a human when the
    calendar is later changed to stop that day.
    """
    if planned_status == SessionStatus.SCHEDULED:
        return existing_status in {SessionStatus.SCHEDULED, SessionStatus.COMPLETED}
    return existing_status == planned_status


def _differences(existing: ClassSession, planned: calendar.PlannedSession) -> tuple[str, ...]:
    """Compare the fields B11 reports on; updates are deliberately out of scope."""
    differences: list[str] = []
    if existing.start_at != planned.start_at:
        differences.append("开始时间")
    if existing.end_at != planned.end_at:
        differences.append("结束时间")
    if existing.start_period != planned.start_period:
        differences.append("开始节")
    if existing.end_period != planned.end_period:
        differences.append("结束节")
    if existing.classroom_id != planned.classroom_id:
        differences.append("教室")
    if not _status_matches_plan(existing.status, planned.status):
        differences.append("状态")
    existing_teachers = {teacher.pk for teacher in existing.teachers.all()}
    if existing_teachers != set(planned.teacher_ids):
        differences.append("授课教师")
    return tuple(differences)


def generate_sessions_for_term(term: Term) -> GenerationResult:
    """Expand every schedule rule of ``term`` into ``ClassSession`` rows.

    The whole run is one transaction.  Only missing generation keys are created;
    existing sessions are left untouched (their differences land in
    ``GenerationResult.mismatched``) because B12 owns the update preview.  When
    at least one session is created, ``term.schedule_version`` is incremented.
    """
    with transaction.atomic():
        # Project-wide lock order: SchoolSyncState first, then the Term row
        # (domain-design 2.1/4.4).  Keep this order in every future writer to
        # avoid deadlocks between imports, rescheduling and admin actions.
        SchoolSyncState.objects.select_for_update().get_or_create(school_id=term.school_id)
        locked_term = Term.objects.select_for_update().get(pk=term.pk)

        periods = {period.period_number: period for period in Period.objects.filter(term=locked_term)}
        exceptions = list(CalendarException.objects.filter(term=locked_term))
        rules = list(
            ScheduleRule.objects.filter(offering__term=locked_term)
            .select_related("offering")
            .prefetch_related("teachers")
        )
        rule_plans = _build_rule_plans(rules)
        _validate_teacher_scope(locked_term, rule_plans)

        planned = calendar.plan_term_sessions(
            term=locked_term,
            rules=rule_plans,
            periods=periods,
            exceptions=exceptions,
        )
        suspected_duplicates = calendar.find_suspected_duplicates(planned)

        existing_by_key = {
            (session.rule_id, session.origin_teaching_date, session.origin_period): session
            for session in ClassSession.objects.filter(
                offering__term=locked_term,
                rule__isnull=False,
            ).prefetch_related("teachers")
        }

        created_ids: list[UUID] = []
        unchanged_ids: list[UUID] = []
        mismatches: list[SessionMismatch] = []
        new_sessions: list[ClassSession] = []
        new_session_teachers: list[SessionTeacher] = []

        for plan in planned:
            key = (plan.rule_id, plan.origin_teaching_date, plan.origin_period)
            existing = existing_by_key.get(key)
            if existing is not None:
                differences = _differences(existing, plan)
                if differences:
                    mismatches.append(
                        SessionMismatch(
                            session_id=existing.id,
                            rule_id=existing.rule_id,
                            origin_teaching_date=existing.origin_teaching_date,
                            origin_period=existing.origin_period,
                            differences=differences,
                        )
                    )
                else:
                    unchanged_ids.append(existing.id)
                continue

            # Build in memory and insert in two bulk statements below.  A term
            # can reach ~20k sessions (implementation-plan 11.3), so per-row
            # INSERTs would make the Admin action time out.  ``recorded_by_user``
            # is intentionally left null: it belongs to completion recording
            # (B22), not to rule generation.
            session = ClassSession(
                offering_id=plan.offering_id,
                rule_id=plan.rule_id,
                origin_teaching_date=plan.origin_teaching_date,
                origin_period=plan.origin_period,
                start_period=plan.start_period,
                end_period=plan.end_period,
                start_at=plan.start_at,
                end_at=plan.end_at,
                classroom_id=plan.classroom_id,
                status=plan.status,
                status_reason=plan.status_reason,
                planned_hours=plan.planned_hours,
                # Cancelled sessions must carry exactly 0 actual hours; the
                # condition is enforced by a database check constraint.
                actual_hours=Decimal("0") if plan.status == SessionStatus.CANCELLED else None,
            )
            new_sessions.append(session)
            created_ids.append(session.id)
            new_session_teachers.extend(
                SessionTeacher(session_id=session.id, teacher_id=teacher_id) for teacher_id in plan.teacher_ids
            )

        if new_sessions:
            # UUID primary keys are generated in Python; PostgreSQL returns the
            # rows in one statement, and the explicit through rows can follow
            # in a single second statement.
            ClassSession.objects.bulk_create(new_sessions)
            if new_session_teachers:
                SessionTeacher.objects.bulk_create(new_session_teachers)

        if created_ids:
            locked_term.schedule_version += 1
            locked_term.save(update_fields=["schedule_version", "updated_at"])
            # Keep the caller's instance in sync with the persisted version.
            term.schedule_version = locked_term.schedule_version

        return GenerationResult(
            created=len(created_ids),
            unchanged=len(unchanged_ids),
            mismatched=tuple(mismatches),
            created_session_ids=tuple(created_ids),
            unchanged_session_ids=tuple(unchanged_ids),
            suspected_duplicates=tuple(suspected_duplicates),
            schedule_version=locked_term.schedule_version,
        )
