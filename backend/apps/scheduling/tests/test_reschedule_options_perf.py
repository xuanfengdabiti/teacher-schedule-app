"""Scale performance guard for B30 reschedule options.

Marked ``perf`` and excluded from the default test run because building the
20,000-session/20,000-enrolment fixture takes noticeably longer than a normal
unit test.  Run explicitly with ``pytest -m perf``.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.scheduling.models import ClassSession
from apps.scheduling.reschedule_options import OptionQuery, find_reschedule_options
from apps.scheduling.tests.scale_data import build_scale_data

pytestmark = [pytest.mark.django_db, pytest.mark.perf]


def test_14_day_candidate_query_stays_under_two_seconds() -> None:
    data = build_scale_data()

    assert ClassSession.objects.count() == 20_000
    assert data.enrollment_count == 20_000
    source = (
        ClassSession.objects.filter(
            offering=data.offerings[0],
            start_at__date__gte=date(2026, 10, 27),
        )
        .order_by("start_at")
        .first()
    )
    assert source is not None

    now = datetime(2026, 10, 26, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    query = OptionQuery(
        date_from=date(2026, 10, 26),
        date_until=date(2026, 10, 26) + timedelta(days=14),
    )
    started = time.perf_counter()
    result = find_reschedule_options(source, query, now=now)
    elapsed = time.perf_counter() - started

    print(f"\nB30 14-day scale query: {len(result.options)} options in {elapsed:.2f}s")
    assert result.options
    assert elapsed < 2.0, f"14-day candidate query took {elapsed:.2f}s (target < 2.00s)"

    # Printed for the review report; the hard acceptance target is 14 days.
    sixty_day_query = OptionQuery(
        date_from=date(2026, 10, 26),
        date_until=date(2026, 10, 26) + timedelta(days=60),
    )
    started = time.perf_counter()
    sixty_day_result = find_reschedule_options(source, sixty_day_query, now=now)
    sixty_day_elapsed = time.perf_counter() - started
    print(f"B30 60-day scale query: {len(sixty_day_result.options)} options in {sixty_day_elapsed:.2f}s")
    assert sixty_day_result.options
