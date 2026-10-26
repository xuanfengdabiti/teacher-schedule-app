"""Entity-specific projection builders for import preview and commit."""

from __future__ import annotations

from datetime import date, datetime

from apps.imports.projection.base import BuildContext
from apps.imports.projection.blocks import build_person_blocks, build_room_blocks
from apps.imports.projection.catalog import build_classrooms, build_courses, build_offerings, build_teaching_classes
from apps.imports.projection.coverages import build_coverages
from apps.imports.projection.enrollments import build_enrollments
from apps.imports.projection.planning import PlanData, plan_all_sessions
from apps.imports.projection.registries import build_calendar_exceptions, build_periods, build_terms
from apps.imports.projection.rules import build_rules
from apps.imports.projection.sessions import build_sessions
from apps.imports.projection.teachers import build_students, build_teachers


def build_graph(ctx: BuildContext, *, now: datetime, school_date: date) -> PlanData:
    """Run all entity projections in dependency order and return session plan."""
    build_terms(ctx)
    build_periods(ctx)
    build_teachers(ctx)
    build_classrooms(ctx)
    build_courses(ctx)
    build_teaching_classes(ctx)
    build_offerings(ctx, school_date=school_date)
    build_students(ctx)
    build_enrollments(ctx)
    build_calendar_exceptions(ctx)
    build_rules(ctx)
    plan = plan_all_sessions(ctx)
    build_sessions(ctx, plan, now)
    build_room_blocks(ctx)
    build_person_blocks(ctx)
    build_coverages(ctx)
    return plan
