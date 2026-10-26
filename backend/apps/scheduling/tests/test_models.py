"""Model-level business rules that are easy to regress (B11 section 2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.academics.models import TeacherProfile, Term
from apps.scheduling.models import ScheduleRule
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db


class TestReferenceDefaults:
    def test_course_offering_inherits_term_minutes_per_hour(self, demo_term) -> None:
        offering = factories.make_offering(
            demo_term.term,
            demo_term.teachers["0001"],
            offering_code="OFF-DEFAULT-MINUTES",
            course_code="C-DEFAULT",
            course_name="默认学时示例",
        )

        assert offering.minutes_per_hour == demo_term.term.hour_minutes

    def test_teacher_email_is_normalized_on_save(self, demo_term) -> None:
        teacher = TeacherProfile.objects.create(
            school=demo_term.school,
            teacher_no="0100",
            formal_name="示例教师丙",
            email="  Mixed@Example.INVALID  ",
        )

        assert teacher.email == "mixed@example.invalid"

    def test_rule_weeks_are_deduplicated_and_sorted(self, demo_term) -> None:
        rule = factories.make_rule(
            demo_term.offerings["OFF-CRIME-2026A"],
            demo_term.classrooms["A301"],
            (demo_term.teachers["0001"],),
            rule_code="R-WEEKS",
            weeks=(5, 3, 3, 1),
        )
        rule.refresh_from_db()

        assert rule.weeks == [1, 3, 5]
        assert rule.planned_hours == Decimal("2.0")


class TestTermValidation:
    def test_invalid_timezone_is_rejected_by_clean(self, demo_term) -> None:
        term = Term(
            school=demo_term.school,
            code="2026-invalid-tz",
            name="虚构错误学期",
            start_date=date(2026, 9, 7),
            end_date=date(2027, 1, 24),
            first_week_monday=date(2026, 9, 7),
            teaching_week_count=20,
            timezone="Mars/Olympus",
        )
        with pytest.raises(ValidationError) as exc_info:
            term.full_clean()
        assert "timezone" in exc_info.value.message_dict


class TestScheduleRuleValidation:
    def test_missing_period_in_term_is_rejected(self, demo_term) -> None:
        rule = ScheduleRule(
            offering=demo_term.offerings["OFF-CRIME-2026A"],
            rule_code="R-MISSING-PERIOD",
            weekday=1,
            start_period=1,
            end_period=9,
            weeks=[1],
            classroom=demo_term.classrooms["A301"],
            planned_hours=Decimal("2.0"),
        )
        with pytest.raises(ValidationError) as exc_info:
            rule.full_clean()
        assert "start_period" in exc_info.value.message_dict

    def test_week_outside_teaching_week_count_is_rejected(self, demo_term) -> None:
        rule = ScheduleRule(
            offering=demo_term.offerings["OFF-CRIME-2026A"],
            rule_code="R-WEEK-TOO-LATE",
            weekday=1,
            start_period=1,
            end_period=2,
            weeks=[1, 21],
            classroom=demo_term.classrooms["A301"],
            planned_hours=Decimal("2.0"),
        )
        with pytest.raises(ValidationError) as exc_info:
            rule.full_clean()
        assert "weeks" in exc_info.value.message_dict

    def test_teacher_outside_offering_scope_is_rejected_by_clean(self, demo_term) -> None:
        outsider = factories.make_teacher(demo_term.school, "0003")
        rule = factories.make_rule(
            demo_term.offerings["OFF-DATA-2026A"],
            demo_term.classrooms["A302"],
            (outsider,),
            rule_code="R-OUT-OF-SCOPE",
            weeks=(1,),
        )
        with pytest.raises(ValidationError) as exc_info:
            rule.full_clean()
        assert "teachers" in exc_info.value.message_dict

    def test_rule_code_must_be_unique_within_term(self, demo_term) -> None:
        rule = ScheduleRule(
            offering=demo_term.offerings["OFF-DATA-2026A"],
            rule_code="R-CRIME-ODD",
            weekday=1,
            start_period=3,
            end_period=4,
            weeks=[1],
            classroom=demo_term.classrooms["A302"],
            planned_hours=Decimal("2.0"),
        )
        with pytest.raises(ValidationError) as exc_info:
            rule.full_clean()
        assert "rule_code" in exc_info.value.message_dict
