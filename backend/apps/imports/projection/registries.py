"""Term, period and calendar-exception projections."""

from __future__ import annotations

import uuid

from apps.academics.models import CalendarException, Period, Term
from apps.imports.projection.base import (
    ENTITY_CALENDAR_EXCEPTION,
    ENTITY_PERIOD,
    ENTITY_TERM,
    ERROR,
    BuildContext,
    diff_fields,
    external_id_for_period,
    issue_for,
    resolve_object,
    term_ref,
)
from apps.scheduling.models import ClassSession


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
        differences = []
        if isinstance(target, Period):
            differences = diff_fields(
                target,
                {
                    "period_number": data.period_number,
                    "start_time": data.start_time,
                    "end_time": data.end_time,
                    "block_code": data.block_code,
                },
                ["period_number", "start_time", "end_time", "block_code"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_PERIOD,
            external_id=external_id_for_period(term_code, number),
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.periods[key] = obj


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
        differences = []
        if isinstance(target, CalendarException):
            differences = diff_fields(
                target,
                {
                    "term_id": data.term_id,
                    "date": data.date,
                    "kind": data.kind,
                    "source_teaching_date": data.source_teaching_date,
                    "reason": data.reason,
                },
                ["term_id", "date", "kind", "source_teaching_date", "reason"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_CALENDAR_EXCEPTION,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        if target is None and term.pk and ClassSession.objects.filter(offering__term_id=term.pk).exists():
            obj.state = ERROR
            ctx.add_issue(
                issue_for(
                    row,
                    "date",
                    "CHANGE_NOT_SUPPORTED_YET",
                    "该学期已有正式课次，新增校历例外会改变既有课表；B12b 不自动重排，留待 B12c 处理。",
                )
            )
        obj.metadata["term_code"] = term_code
        ctx.graph.calendar_exceptions[code] = obj
