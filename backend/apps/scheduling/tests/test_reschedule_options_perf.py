"""Scale performance guard for B30 reschedule options.

Marked ``perf`` and excluded from the default test run because building the
20,000-session/20,000-enrolment fixture takes noticeably longer than a normal
unit test.  Run explicitly with ``uv run --env-file .env pytest -m perf``.
"""

from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.academics.models import Enrollment, Student
from apps.scheduling.models import ClassSession
from apps.scheduling.reschedule_options import OptionQuery, find_reschedule_options
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories as f

pytestmark = [pytest.mark.django_db, pytest.mark.perf]


def test_14_day_candidate_query_stays_under_two_seconds() -> None:
    rnd = random.Random(7)
    school = f.make_school()
    term = f.make_term(school)
    f.make_periods(term)
    rooms = [f.make_classroom(school, f"R{i:03d}", capacity=60) for i in range(100)]
    teachers = [f.make_teacher(school, f"{i:04d}") for i in range(200)]
    students = Student.objects.bulk_create([Student(school=school, student_no=f"{i:06d}") for i in range(5000)])

    offerings = []
    slots = [(day, period) for day in range(1, 6) for period in (1, 3, 5, 7)]
    index = 0
    for room in rooms:
        for weekday, start_period in rnd.sample(slots, 10):
            teacher = teachers[index % 200]
            offering = f.make_offering(
                term,
                teacher,
                offering_code=f"O{index:04d}",
                course_code=f"C{index:04d}",
                course_name=f"课程{index}",
                class_code=f"TC{index % 50:02d}",
                required_equipment=(),
            )
            f.make_rule(
                offering,
                room,
                (teacher,),
                rule_code=f"R{index:04d}",
                weekday=weekday,
                start_period=start_period,
                end_period=start_period + 1,
                weeks=tuple(range(1, 21)),
            )
            offerings.append(offering)
            index += 1

    enrollments = []
    for student in students:
        for offering in rnd.sample(offerings, 4):
            enrollments.append(
                Enrollment(
                    offering=offering,
                    student=student,
                    effective_from=term.start_date,
                )
            )
    Enrollment.objects.bulk_create(enrollments)
    generate_sessions_for_term(term)

    assert ClassSession.objects.count() == 20_000
    assert len(enrollments) == 20_000
    source = (
        ClassSession.objects.filter(
            offering=offerings[0],
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
