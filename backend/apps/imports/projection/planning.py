"""Rule expansion planning used by session projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError

from apps.academics.models import CalendarException, Period, Term
from apps.imports.projection.base import (
    ENTITY_CALENDAR_EXCEPTION,
    ENTITY_RULE,
    BuildContext,
    ImportGraph,
    ImportObject,
    issue_for,
    term_ref,
)
from apps.scheduling import calendar
from apps.scheduling.models import ClassSession, ScheduleRule


@dataclass
class PlanData:
    planned: list[calendar.PlannedSession]
    planned_by_key: dict[tuple[str, str, date, int], calendar.PlannedSession]
    existing_generation_keys: set[tuple[UUID, date, int]]
    expected_new: int


def objects_for_term(ctx: BuildContext, entity_type: str, term_code: str) -> list[ImportObject]:
    mapping = ctx.graph.by_entity.get(entity_type, {})
    result: list[ImportObject] = []
    for obj in mapping.values():
        obj_term = obj.metadata.get("term_code")
        if obj_term is None and isinstance(obj.data, (Period, CalendarException, Term)):
            obj_term = getattr(getattr(obj.data, "term", None), "code", None)
        if obj_term == term_code:
            result.append(obj)
    return result


def rule_plan_from_object(ctx: BuildContext, obj: ImportObject) -> calendar.RulePlan | None:
    rule = obj.data
    if not isinstance(rule, ScheduleRule):
        return None
    if not (rule.start_period and rule.end_period and rule.classroom_id and rule.planned_hours is not None):
        return None
    teacher_ids = tuple(obj.metadata.get("teacher_ids", ()))
    if not teacher_ids:
        return None
    return calendar.RulePlan(
        rule_id=rule.pk,
        rule_code=rule.rule_code,
        offering_id=rule.offering_id,
        weekday=rule.weekday,
        start_period=rule.start_period,
        end_period=rule.end_period,
        weeks=tuple(rule.weeks or ()),
        classroom_id=rule.classroom_id,
        planned_hours=rule.planned_hours,
        teacher_ids=teacher_ids,
    )


def referenced_term_codes(graph: ImportGraph, batch, parsed=None) -> set[str]:
    codes = set(graph.terms)
    for obj in graph.objects():
        term_code = obj.metadata.get("term_code")
        if term_code:
            codes.add(term_code)
    if parsed is not None:
        # Sessions/rule planning runs before session objects are added to the
        # graph.  Their term references must still select the existing rules
        # and periods for a partial file batch.
        for row in parsed.rows:
            term_code = row.values.get("term_code")
            if term_code:
                codes.add(term_code)
    if batch.term_id:
        term = batch.term
        if term is not None:
            codes.add(term.code)
    return codes


def plan_all_sessions(ctx: BuildContext) -> PlanData:
    planned: list[calendar.PlannedSession] = []
    planned_by_key: dict[tuple[str, str, date, int], calendar.PlannedSession] = {}
    existing_generation_keys: set[tuple[UUID, date, int]] = set()
    expected_new = 0

    for term_code in sorted(referenced_term_codes(ctx.graph, ctx.batch, ctx.parsed)):
        term_obj = ctx.graph.terms.get(term_code)
        term = term_obj.data if term_obj is not None else term_ref(ctx, term_code)
        if not isinstance(term, Term):
            continue
        if not all(
            [
                term.start_date,
                term.end_date,
                term.first_week_monday,
                term.timezone,
                term.teaching_week_count,
            ]
        ):
            continue
        try:
            ZoneInfo(term.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            continue

        periods: dict[int, Period] = {}
        if term.pk:
            periods.update({period.period_number: period for period in Period.objects.filter(term_id=term.pk)})
        for (period_term, number), period_obj in ctx.graph.periods.items():
            if period_term == term_code and isinstance(period_obj.data, Period):
                period = period_obj.data
                if period.start_time and period.end_time:
                    periods[number] = period

        exception_objects = objects_for_term(ctx, ENTITY_CALENDAR_EXCEPTION, term_code)
        exceptions_by_date: dict[date, CalendarException] = {}
        if term.pk:
            for exception in CalendarException.objects.filter(term_id=term.pk):
                exceptions_by_date[exception.date] = exception
        for obj in exception_objects:
            exception = obj.data
            if isinstance(exception, CalendarException) and exception.date and exception.kind:
                exceptions_by_date[exception.date] = exception
        exceptions = list(exceptions_by_date.values())

        rules = objects_for_term(ctx, ENTITY_RULE, term_code)
        rule_plans_by_id: dict[UUID, calendar.RulePlan] = {}
        for obj in rules:
            plan = rule_plan_from_object(ctx, obj)
            if plan is not None:
                rule_plans_by_id.setdefault(plan.rule_id, plan)
        if term.pk:
            db_rules = (
                ScheduleRule.objects.filter(offering__term_id=term.pk)
                .select_related("offering")
                .prefetch_related("teachers")
            )
            for rule in db_rules:
                if rule.pk in rule_plans_by_id:
                    continue
                if not (rule.start_period and rule.end_period and rule.classroom_id):
                    continue
                rule_plans_by_id[rule.pk] = calendar.RulePlan(
                    rule_id=rule.pk,
                    rule_code=rule.rule_code,
                    offering_id=rule.offering_id,
                    weekday=rule.weekday,
                    start_period=rule.start_period,
                    end_period=rule.end_period,
                    weeks=tuple(rule.weeks or ()),
                    classroom_id=rule.classroom_id,
                    planned_hours=rule.planned_hours,
                    teacher_ids=tuple(sorted((teacher.pk for teacher in rule.teachers.all()), key=str)),
                )
        rule_plans = list(rule_plans_by_id.values())

        if not rule_plans:
            continue
        try:
            term_planned = calendar.plan_term_sessions(
                term=term,
                rules=rule_plans,
                periods=periods,
                exceptions=exceptions,
            )
        except ValidationError as exc:
            rows = rules[0].rows if rules else []
            if rows:
                ctx.add_issue(
                    issue_for(
                        rows[0],
                        "rule_code",
                        "RULE_EXPANSION_FAILED",
                        f"规则展开失败：{' '.join(exc.messages)}",
                    )
                )
            continue

        existing_sessions: dict[tuple[UUID, date, int], ClassSession] = {}
        if term.pk:
            for session in ClassSession.objects.filter(
                offering__term_id=term.pk,
                rule__isnull=False,
            ):
                existing_sessions[(session.rule_id, session.origin_teaching_date, session.origin_period)] = session
        for planned_session in term_planned:
            key = (
                term_code,
                planned_session.rule_code,
                planned_session.origin_teaching_date,
                planned_session.origin_period,
            )
            planned_by_key[key] = planned_session
            generation_key = (
                planned_session.rule_id,
                planned_session.origin_teaching_date,
                planned_session.origin_period,
            )
            if generation_key in existing_sessions:
                existing_generation_keys.add(generation_key)
            else:
                expected_new += 1
        planned.extend(term_planned)

    return PlanData(
        planned=planned,
        planned_by_key=planned_by_key,
        existing_generation_keys=existing_generation_keys,
        expected_new=expected_new,
    )
