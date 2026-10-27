"""Term, period and calendar-exception projections."""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError

from apps.academics.models import CalendarException, Period, Term
from apps.imports.projection.base import (
    ENTITY_CALENDAR_EXCEPTION,
    ENTITY_PERIOD,
    ENTITY_TERM,
    BuildContext,
    diff_fields,
    external_id_for_period,
    issue_for,
    resolve_object,
    term_ref,
)
from apps.scheduling import calendar


def build_terms(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("terms"):
        code = row.values.get("term_code")
        if not code or code in ctx.graph.terms:
            continue
        data = Term(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            code=code,
            name=row.values.get("term_name") or "",
            start_date=row.values.get("start_date"),
            end_date=row.values.get("end_date"),
            first_week_monday=row.values.get("first_week_monday"),
            teaching_week_count=row.values.get("teaching_week_count") or 0,
            timezone=row.values.get("timezone") or "",
            hour_minutes=row.values.get("hour_minutes") or 0,
        )
        natural = Term.objects.filter(school_id=ctx.batch.school_id, code=code).first()
        identity = ctx.existing.identity(ENTITY_TERM, code)
        target = ctx.existing.target(ENTITY_TERM, code) if identity else None
        unsupported = []
        updates = []
        if isinstance(target, Term):
            unsupported = diff_fields(
                target,
                {
                    "start_date": data.start_date,
                    "end_date": data.end_date,
                    "first_week_monday": data.first_week_monday,
                    "teaching_week_count": data.teaching_week_count,
                    "timezone": data.timezone,
                    "hour_minutes": data.hour_minutes,
                },
                [
                    "start_date",
                    "end_date",
                    "first_week_monday",
                    "teaching_week_count",
                    "timezone",
                    "hour_minutes",
                ],
            )
            updates = diff_fields(target, {"name": data.name}, ["name"])
        resolve_object(
            ctx,
            entity_type=ENTITY_TERM,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        ctx.graph.terms[code] = ctx.graph.by_entity[ENTITY_TERM][code]


def build_periods(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("periods"):
        term_code = row.values.get("term_code")
        number = row.values.get("period_number")
        if not term_code or not isinstance(number, int):
            continue
        key = (term_code, number)
        if key in ctx.graph.periods:
            continue
        term = term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(
                issue_for(
                    row,
                    "term_code",
                    "FK_NOT_FOUND",
                    f"学期 {term_code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
            continue
        data = Period(
            id=uuid.uuid4(),
            term_id=term.pk,
            period_number=number,
            start_time=row.values.get("start_time"),
            end_time=row.values.get("end_time"),
            block_code=row.values.get("block_code") or "",
        )
        natural = Period.objects.filter(term_id=term.pk, period_number=number).first() if term.pk else None
        identity = ctx.existing.identity(ENTITY_PERIOD, external_id_for_period(term_code, number))
        target = ctx.existing.target(ENTITY_PERIOD, external_id_for_period(term_code, number)) if identity else None
        unsupported: list = []
        updates: list = []
        if isinstance(target, Period):
            unsupported = diff_fields(
                target,
                {"period_number": data.period_number},
                ["period_number"],
            )
            updates = diff_fields(
                target,
                {
                    "start_time": data.start_time,
                    "end_time": data.end_time,
                    "block_code": data.block_code,
                },
                ["start_time", "end_time", "block_code"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_PERIOD,
            external_id=external_id_for_period(term_code, number),
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.periods[key] = obj

    _validate_effective_periods(ctx)


def _validate_effective_periods(ctx: BuildContext) -> None:
    """Reject overlaps in the effective period set, not only within one file.

    Changing one period's time can overlap another period that was not part of
    the uploaded file.  The check is one query per affected term, not one per
    row.
    """
    term_numbers: dict[str, set[int]] = {}
    for term_code, number in ctx.graph.periods:
        term_numbers.setdefault(term_code, set()).add(number)
    for term_code, changed_numbers in term_numbers.items():
        term = term_ref(ctx, term_code)
        if term is None or term.pk is None:
            continue
        effective: dict[int, Period] = {
            period.period_number: period for period in Period.objects.filter(term_id=term.pk)
        }
        for (period_term, number), obj in ctx.graph.periods.items():
            if period_term == term_code and isinstance(obj.data, Period):
                effective[number] = obj.data
        rows = [
            (number, period.start_time, period.end_time)
            for number, period in effective.items()
            if period.start_time and period.end_time
        ]
        reported: set[int] = set()
        for index, (left_number, left_start, left_end) in enumerate(rows):
            for right_number, right_start, right_end in rows[index + 1 :]:
                if not (left_start < right_end and right_start < left_end):
                    continue
                for changed_number, other_number in (
                    (left_number, right_number),
                    (right_number, left_number),
                ):
                    if changed_number not in changed_numbers or changed_number in reported:
                        continue
                    obj = ctx.graph.periods.get((term_code, changed_number))
                    if obj is None or not obj.rows:
                        continue
                    reported.add(changed_number)
                    ctx.add_issue(
                        issue_for(
                            obj.rows[0],
                            "start_time",
                            "OVERLAPPING_PERIOD",
                            f"更新后第 {changed_number} 节与第 {other_number} 节时间重叠；同学期节次不能重叠。",
                        )
                    )


def build_calendar_exceptions(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("calendar_exceptions"):
        code = row.values.get("exception_code")
        term_code = row.values.get("term_code")
        if not code or not term_code or code in ctx.graph.calendar_exceptions:
            continue
        term = term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在或编号无效。"))
            continue
        data = CalendarException(
            id=uuid.uuid4(),
            term_id=term.pk,
            date=row.values.get("date"),
            kind=row.values.get("kind") or "",
            source_teaching_date=row.values.get("source_teaching_date"),
            reason=row.values.get("reason") or "",
        )
        natural = (
            CalendarException.objects.filter(term_id=term.pk, date=data.date).first() if term.pk and data.date else None
        )
        identity = ctx.existing.identity(ENTITY_CALENDAR_EXCEPTION, code)
        target = ctx.existing.target(ENTITY_CALENDAR_EXCEPTION, code) if identity else None
        unsupported: list = []
        updates: list = []
        if isinstance(target, CalendarException):
            unsupported = diff_fields(target, {"term_id": data.term_id}, ["term_id"])
            updates = diff_fields(
                target,
                {
                    "date": data.date,
                    "kind": data.kind,
                    "source_teaching_date": data.source_teaching_date,
                    "reason": data.reason,
                },
                ["date", "kind", "source_teaching_date", "reason"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_CALENDAR_EXCEPTION,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.calendar_exceptions[code] = obj

    _validate_effective_exceptions(ctx)


def _validate_effective_exceptions(ctx: BuildContext) -> None:
    """Validate the effective exception set after this batch's changes.

    ``plan_term_sessions`` also validates, but its error is attached to a rule
    row.  Checking here gives the academic staff the exception row that made the
    calendar inconsistent (for example moving a date onto an existing holiday).
    """
    changed_terms = {
        obj.metadata.get("term_code")
        for obj in ctx.graph.calendar_exceptions.values()
        if obj.metadata.get("term_code") and obj.state in {"new", "changed"}
    }
    for term_code in sorted(changed_terms):
        term = term_ref(ctx, term_code)
        if term is None or term.pk is None:
            continue
        if not (term.start_date and term.end_date and term.first_week_monday):
            continue
        effective: list[CalendarException] = list(CalendarException.objects.filter(term_id=term.pk))
        by_code = {
            obj.external_id: obj
            for obj in ctx.graph.calendar_exceptions.values()
            if obj.metadata.get("term_code") == term_code
        }
        replaced_pks = {
            obj.existing.pk
            for obj in by_code.values()
            if obj.state == "changed" and isinstance(obj.existing, CalendarException)
        }
        effective = [exception for exception in effective if exception.pk not in replaced_pks]
        for obj in by_code.values():
            if obj.state in {"new", "changed"} and isinstance(obj.data, CalendarException):
                if obj.data.date and obj.data.kind:
                    effective.append(obj.data)
        try:
            calendar.validate_exceptions(term, effective)
        except ValidationError as exc:
            for obj in by_code.values():
                if obj.state not in {"new", "changed"} or not obj.rows:
                    continue
                ctx.add_issue(
                    issue_for(
                        obj.rows[0],
                        "kind",
                        "CALENDAR_EXCEPTION_CONFLICT",
                        f"更新后的校历例外集合无效：{' '.join(exc.messages)}",
                    )
                )
