"""Pure functions for teaching weeks, calendar exceptions and rule expansion.

This module never touches the database: callers (``services.generate_sessions_for_term``)
load terms, periods, exceptions, rules and teacher ids first and pass them in.
Keeping the calculation side-effect free makes the calendar rules testable and
ensures a failed validation cannot leave half-written sessions behind.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError

from apps.academics.models import CalendarExceptionKind
from apps.scheduling.models import SessionStatus


class TermLike(Protocol):
    """Anything exposing a term's calendar fields (``Term`` or a test double)."""

    start_date: date
    end_date: date
    first_week_monday: date
    teaching_week_count: int
    timezone: str


class PeriodLike(Protocol):
    period_number: int
    start_time: time
    end_time: time


class ExceptionLike(Protocol):
    date: date
    kind: str
    source_teaching_date: date | None
    reason: str


@dataclass(frozen=True)
class RulePlan:
    """Lightweight projection of a ``ScheduleRule`` plus its teacher ids."""

    rule_id: UUID
    rule_code: str
    offering_id: UUID
    weekday: int
    start_period: int
    end_period: int
    weeks: tuple[int, ...]
    classroom_id: UUID
    planned_hours: Decimal
    teacher_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class PlannedSession:
    """One session the rule expansion wants to exist, not yet a model instance."""

    rule_id: UUID
    rule_code: str
    offering_id: UUID
    origin_teaching_date: date
    origin_period: int
    week: int
    session_date: date
    start_period: int
    end_period: int
    start_at: datetime
    end_at: datetime
    classroom_id: UUID
    status: str
    status_reason: str
    planned_hours: Decimal
    teacher_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class SuspectedDuplicate:
    offering_id: UUID
    session_date: date
    start_period: int
    end_period: int
    rule_codes: tuple[str, ...]


def teaching_week(term: TermLike, day: date) -> int | None:
    """Return the teaching week of ``day`` or ``None`` when it is not taught.

    Weeks are counted from ``first_week_monday`` (not ISO week numbers).  Days
    outside the term's inclusive start/end dates, or beyond its configured
    teaching week count, do not belong to any teaching week.
    """
    if day < term.start_date or day > term.end_date:
        return None
    week = (day - term.first_week_monday).days // 7 + 1
    if week < 1 or week > term.teaching_week_count:
        return None
    return week


def validate_exceptions(term: TermLike, exceptions: Sequence[ExceptionLike]) -> None:
    """Validate calendar exceptions before any session is written.

    Raises ``ValidationError`` (Chinese messages) so a bad calendar aborts the
    whole generation instead of producing a partially moved schedule.
    """
    errors: list[str] = []
    replaces = [exception for exception in exceptions if exception.kind == CalendarExceptionKind.REPLACE]
    replace_targets = {exception.date for exception in replaces}
    used_sources: set[date] = set()
    used_dates: set[date] = set()

    for exception in exceptions:
        if exception.date in used_dates:
            errors.append(f"同一日期 {exception.date} 只能有一条校历例外。")
        used_dates.add(exception.date)
        if not term.start_date <= exception.date <= term.end_date:
            errors.append(f"校历例外 {exception.date} 不在学期起止日期内。")
        if exception.kind != CalendarExceptionKind.REPLACE:
            continue
        source = exception.source_teaching_date
        if source is None:
            errors.append(f"替换例外（目标日 {exception.date}）缺少原教学日期。")
            continue
        if not term.start_date <= source <= term.end_date:
            errors.append(f"替换例外的原教学日期 {source} 不在学期起止日期内。")
        if source == exception.date:
            errors.append(f"替换例外 {exception.date} 的原教学日期不能与目标日相同。")
            continue
        if source in replace_targets:
            errors.append(f"禁止替换链：原教学日期 {source} 已是另一个 replace 的目标日，不能再被替换。")
        if source in used_sources:
            errors.append(f"同一原教学日期 {source} 只能迁往一个目标日。")
        used_sources.add(source)

    if errors:
        raise ValidationError(errors)


def plan_term_sessions(
    *,
    term: TermLike,
    rules: Iterable[RulePlan],
    periods: Mapping[int, PeriodLike],
    exceptions: Sequence[ExceptionLike],
) -> list[PlannedSession]:
    """Expand every rule into its planned sessions for the term.

    Applies ``docs/import-spec.md`` 3.7/3.8 in this order:

    1. compute the base teaching date for every configured week and drop dates
       outside the term;
    2. if a ``replace`` exception uses that date as source, teach it on the
       replacement date (scheduled);
    3. otherwise, a cancel/custom exception *or another replace's target date*
       keeps the session but marks it cancelled with the calendar reason;
    4. otherwise the session stays scheduled on its base date.

    ``origin_teaching_date``/``origin_period`` always keep the first generated
    values, even when the session is moved.
    """
    validate_exceptions(term, exceptions)

    by_date = {exception.date: exception for exception in exceptions}
    replaces_by_source = {
        exception.source_teaching_date: exception
        for exception in exceptions
        if exception.kind == CalendarExceptionKind.REPLACE
    }
    try:
        tzinfo = ZoneInfo(term.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"学期时区无效：{term.timezone}") from exc
    planned: list[PlannedSession] = []

    for rule in rules:
        for week in sorted(set(rule.weeks)):
            # Defensive: rules created outside full_clean() may carry weeks the
            # term does not teach; those dates simply do not generate sessions.
            if week < 1 or week > term.teaching_week_count:
                continue
            teaching_date = term.first_week_monday + timedelta(days=7 * (week - 1) + rule.weekday - 1)
            if teaching_date < term.start_date or teaching_date > term.end_date:
                continue
            if teaching_week(term, teaching_date) is None:
                continue

            replacement = replaces_by_source.get(teaching_date)
            if replacement is not None:
                session_date = replacement.date
                status = SessionStatus.SCHEDULED
                status_reason = ""
            elif teaching_date in by_date:
                # Keep a stable, visible record of the stopped day instead of
                # dropping it: its UUID can be restored when the school
                # withdraws the calendar exception (A02).
                session_date = teaching_date
                status = SessionStatus.CANCELLED
                status_reason = by_date[teaching_date].reason
            else:
                session_date = teaching_date
                status = SessionStatus.SCHEDULED
                status_reason = ""

            start_slot = periods.get(rule.start_period)
            end_slot = periods.get(rule.end_period)
            if start_slot is None or end_slot is None:
                raise ValidationError(
                    f"规则 {rule.rule_code} 的节次 {rule.start_period}-{rule.end_period} 在学期中不存在。"
                )
            planned.append(
                PlannedSession(
                    rule_id=rule.rule_id,
                    rule_code=rule.rule_code,
                    offering_id=rule.offering_id,
                    origin_teaching_date=teaching_date,
                    origin_period=rule.start_period,
                    week=week,
                    session_date=session_date,
                    start_period=rule.start_period,
                    end_period=rule.end_period,
                    start_at=datetime.combine(session_date, start_slot.start_time, tzinfo=tzinfo),
                    end_at=datetime.combine(session_date, end_slot.end_time, tzinfo=tzinfo),
                    classroom_id=rule.classroom_id,
                    status=status,
                    status_reason=status_reason,
                    planned_hours=rule.planned_hours,
                    teacher_ids=tuple(sorted(set(rule.teacher_ids), key=str)),
                )
            )

    planned.sort(key=lambda item: (item.start_at, item.offering_id.hex, item.rule_code))
    return planned


def find_suspected_duplicates(planned: Iterable[PlannedSession]) -> list[SuspectedDuplicate]:
    """Flag identical offering/date/period sessions produced by different rules.

    Generation still proceeds (the rule identities are legitimate), but the
    caller must surface the hint: two rule codes covering the same slot would
    silently double-count otherwise (import-spec 3.8).
    """
    grouped: dict[tuple[UUID, date, int, int], set[str]] = defaultdict(set)
    for session in planned:
        if session.status != SessionStatus.SCHEDULED:
            continue
        key = (session.offering_id, session.session_date, session.start_period, session.end_period)
        grouped[key].add(session.rule_code)

    duplicates = [
        SuspectedDuplicate(
            offering_id=offering_id,
            session_date=session_date,
            start_period=start_period,
            end_period=end_period,
            rule_codes=tuple(sorted(rule_codes)),
        )
        for (offering_id, session_date, start_period, end_period), rule_codes in grouped.items()
        if len(rule_codes) > 1
    ]
    duplicates.sort(key=lambda item: (item.session_date, item.start_period, str(item.offering_id)))
    return duplicates
