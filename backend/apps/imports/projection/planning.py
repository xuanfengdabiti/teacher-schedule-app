"""Rule expansion planning used by session projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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
class TermPlanData:
    """Effective term inputs plus the expansion they produced.

    ``periods``/``exceptions``/``rule_plans`` already include this batch's
    changes on top of the committed rows.  They are reused by the session
    reconciliation so conflict checks see the same times the plan used.
    """

    term: Term
    periods: dict[int, Period]
    exceptions: list[CalendarException]
    rule_plans: dict[UUID, calendar.RulePlan]
    planned: list[calendar.PlannedSession]
    existing_sessions: list[ClassSession]


@dataclass
class PlanData:
    planned: list[calendar.PlannedSession]
    planned_by_key: dict[tuple[str, str, date, int], calendar.PlannedSession]
    planned_by_rule_week: dict[tuple[str, str, int], calendar.PlannedSession]
    existing_generation_keys: set[tuple[UUID, date, int]]
    expected_new: int
    term_plans: dict[str, TermPlanData]


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


def configured_week(term: Term, origin_date: date, weekday: int) -> int:
    """Return the teaching week that produced ``origin_date`` for ``weekday``.

    ``origin_date`` is the date generated from the rule's *original* weekday.
    Its own ISO weekday is therefore the stable old weekday even after the
    committed rule has been changed and re-imported several times; using the
    current rule weekday would subtract the new offset and drift one week.
    """
    return (origin_date - term.first_week_monday - timedelta(days=weekday - 1)).days // 7 + 1


def planned_for_origin(
    plan: PlanData,
    term: Term,
    *,
    term_code: str,
    rule: ScheduleRule,
    origin_date: date,
    origin_period: int,
    allow_week_fallback: bool,
) -> calendar.PlannedSession | None:
    """Find the plan row for one saved generation key.

    Exact keys win.  When the rule's weekday or start period changed, the
    expansion produces a different origin key; the saved session is still the
    same configured teaching week, so fall back to the week mapping.  The
    fallback is only allowed when an existing session/identity proves this is
    not a fresh, approximate match.
    """
    exact = plan.planned_by_key.get((term_code, rule.rule_code, origin_date, origin_period))
    if exact is not None:
        return exact
    if not allow_week_fallback:
        return None
    week = configured_week(term, origin_date, origin_date.isoweekday())
    return plan.planned_by_rule_week.get((term_code, rule.rule_code, week))


def plan_all_sessions(ctx: BuildContext) -> PlanData:
    planned: list[calendar.PlannedSession] = []
    planned_by_key: dict[tuple[str, str, date, int], calendar.PlannedSession] = {}
    planned_by_rule_week: dict[tuple[str, str, int], calendar.PlannedSession] = {}
    existing_generation_keys: set[tuple[UUID, date, int]] = set()
    expected_new = 0
    term_plans: dict[str, TermPlanData] = {}

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
        replaced_exception_pks = {
            obj.existing.pk
            for obj in exception_objects
            if isinstance(obj.existing, CalendarException) and isinstance(obj.data, CalendarException)
        }
        exceptions_by_date: dict[date, CalendarException] = {}
        if term.pk:
            for exception in CalendarException.objects.filter(term_id=term.pk):
                # A changed exception may have moved to another date; remove
                # the committed row by identity, not leave its old date behind.
                if exception.pk in replaced_exception_pks:
                    continue
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
                ScheduleRule.objects.filter(offering__term_id=term.pk, is_active=True)
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

        existing_rows: list[ClassSession] = []
        if term.pk:
            existing_rows = list(
                ClassSession.objects.filter(offering__term_id=term.pk)
                .select_related("classroom", "offering__term", "offering__course")
                .prefetch_related("teachers")
            )
        existing_sessions: dict[tuple[UUID, date, int], ClassSession] = {
            (session.rule_id, session.origin_teaching_date, session.origin_period): session
            for session in existing_rows
            if session.rule_id is not None
        }

        term_planned: list[calendar.PlannedSession] = []
        if rule_plans:
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

        for planned_session in term_planned:
            key = (
                term_code,
                planned_session.rule_code,
                planned_session.origin_teaching_date,
                planned_session.origin_period,
            )
            planned_by_key[key] = planned_session
            planned_by_rule_week[(term_code, planned_session.rule_code, planned_session.week)] = planned_session
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
        term_plans[term_code] = TermPlanData(
            term=term,
            periods=periods,
            exceptions=exceptions,
            rule_plans=rule_plans_by_id,
            planned=term_planned,
            existing_sessions=existing_rows,
        )

    return PlanData(
        planned=planned,
        planned_by_key=planned_by_key,
        planned_by_rule_week=planned_by_rule_week,
        existing_generation_keys=existing_generation_keys,
        expected_new=expected_new,
        term_plans=term_plans,
    )
