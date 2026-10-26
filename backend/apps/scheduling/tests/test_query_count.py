"""Query-count guards for session generation (bulk inserts)."""

from __future__ import annotations

from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db


def _build_term(school, code: str, rule_count: int, weeks: tuple[int, ...]):
    term = factories.make_term(
        school,
        code=code,
        name=f"查询数测试学期 {code}",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 1, 24),
        first_week_monday=date(2026, 9, 7),
        teaching_week_count=20,
    )
    factories.make_periods(term)
    teacher = factories.make_teacher(school, "0001")
    classroom = factories.make_classroom(school, "A301", room_no="301")
    offering = factories.make_offering(
        term,
        teacher,
        offering_code=f"OFF-{code}",
        course_code=f"C-{code}",
        course_name=f"查询数测试课程 {code}",
    )
    for index in range(rule_count):
        factories.make_rule(
            offering,
            classroom,
            (teacher,),
            rule_code=f"R-{code}-{index}",
            weekday=1,
            weeks=weeks,
        )
    return term


def test_demo_generation_stays_within_fixed_query_budget(demo_term, django_assert_max_num_queries) -> None:
    with django_assert_max_num_queries(20):
        result = generate_sessions_for_term(demo_term.term)

    assert result.created == 40


def test_query_count_does_not_grow_with_session_count() -> None:
    # 10 rules x 4 weeks = 40 sessions vs 10 rules x 20 weeks = 200 sessions:
    # five times the sessions must use the same number of statements.
    small_term = _build_term(
        factories.make_school(code="PERF-SMALL"),
        "perf-small",
        rule_count=10,
        weeks=(1, 2, 3, 4),
    )
    large_term = _build_term(
        factories.make_school(code="PERF-LARGE"),
        "perf-large",
        rule_count=10,
        weeks=tuple(range(1, 21)),
    )

    with CaptureQueriesContext(connection) as small_queries:
        small_result = generate_sessions_for_term(small_term)
    with CaptureQueriesContext(connection) as large_queries:
        large_result = generate_sessions_for_term(large_term)

    assert small_result.created == 40
    assert large_result.created == 200
    assert len(large_queries) == len(small_queries)
