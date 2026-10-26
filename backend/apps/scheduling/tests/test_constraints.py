"""Model constraint tests (B11 acceptance item 6)."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.academics.models import DataCoverage, Enrollment, Period, Term
from apps.scheduling.models import ClassSession, PersonBlock, SessionStatus
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db

TZ = ZoneInfo("Asia/Shanghai")


def aware(year: int, month: int, day: int, hour: int = 8, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def make_session(demo_term, **overrides) -> ClassSession:
    values = {
        "offering": demo_term.offerings["OFF-CRIME-2026A"],
        "start_period": 1,
        "end_period": 2,
        "start_at": aware(2026, 11, 2, 8),
        "end_at": aware(2026, 11, 2, 9, 40),
        "classroom": demo_term.classrooms["A301"],
        "status": SessionStatus.SCHEDULED,
        "planned_hours": Decimal("2.0"),
    }
    values.update(overrides)
    return ClassSession.objects.create(**values)


class TestPersonBlock:
    def test_requires_exactly_one_person(self, demo_term) -> None:
        base = {
            "start_at": aware(2026, 10, 28, 10),
            "end_at": aware(2026, 10, 28, 11, 40),
            "reason": "虚构示例",
            "block_code": "PB-TEST",
        }
        with pytest.raises(IntegrityError), transaction.atomic():
            PersonBlock.objects.create(**base)
        with pytest.raises(IntegrityError), transaction.atomic():
            PersonBlock.objects.create(
                teacher=demo_term.teachers["0002"],
                student=demo_term.students["000002"],
                **base,
            )

    def test_single_person_is_accepted(self, demo_term) -> None:
        block = PersonBlock.objects.create(
            teacher=demo_term.teachers["0002"],
            start_at=aware(2026, 10, 28, 10),
            end_at=aware(2026, 10, 28, 11, 40),
            reason="虚构示例教师会议",
            block_code="PB-T0002-20261028",
        )
        assert block.teacher_id == demo_term.teachers["0002"].id


class TestClassSessionConstraints:
    @pytest.mark.parametrize(
        "overrides",
        [
            # scheduled sessions must not carry actual hours yet.
            {"status": SessionStatus.SCHEDULED, "actual_hours": Decimal("2.0")},
            # cancelled sessions must be exactly 0, not null.
            {"status": SessionStatus.CANCELLED},
            {"status": SessionStatus.CANCELLED, "actual_hours": Decimal("1.0")},
            # completed requires amount, confirmation time and teacher.
            {"status": SessionStatus.COMPLETED, "actual_hours": Decimal("2.0")},
            {
                "status": SessionStatus.COMPLETED,
                "actual_hours": Decimal("2.0"),
                "completed_at": aware(2026, 11, 2, 10),
            },
            {
                "status": SessionStatus.COMPLETED,
                "actual_hours": Decimal("2.0"),
                "with_teacher": True,
            },
            {
                "status": SessionStatus.COMPLETED,
                "completed_at": aware(2026, 11, 2, 10),
                "with_teacher": True,
            },
        ],
    )
    def test_status_and_actual_hours_combinations(self, demo_term, overrides) -> None:
        values = dict(overrides)
        if values.pop("with_teacher", False):
            values["completed_by_teacher"] = demo_term.teachers["0001"]
        with pytest.raises(IntegrityError), transaction.atomic():
            make_session(demo_term, **values)

    def test_completed_session_with_full_evidence_is_accepted(self, demo_term) -> None:
        session = make_session(
            demo_term,
            status=SessionStatus.COMPLETED,
            actual_hours=Decimal("2.0"),
            completed_at=aware(2026, 11, 2, 10),
            completed_by_teacher=demo_term.teachers["0001"],
            completion_ref="DEMO-RECORD",
        )
        assert session.actual_hours == Decimal("2.0")

    def test_cancelled_session_with_zero_hours_is_accepted(self, demo_term) -> None:
        session = make_session(
            demo_term,
            status=SessionStatus.CANCELLED,
            actual_hours=Decimal("0"),
            status_reason="虚构示例停课",
        )
        assert session.actual_hours == Decimal("0")

    def test_end_at_must_be_after_start_at(self, demo_term) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            make_session(demo_term, end_at=aware(2026, 11, 2, 8))

    def test_rule_and_origin_are_all_or_nothing(self, demo_term) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            make_session(demo_term, rule=demo_term.rules["R-CRIME-ODD"])
        with pytest.raises(IntegrityError), transaction.atomic():
            make_session(demo_term, origin_teaching_date=date(2026, 11, 2), rule=None)


class TestCalendarExceptionConstraints:
    def test_same_source_cannot_be_replaced_twice(self, demo_term) -> None:
        # demo_term already replaces 2026-10-05 with 2026-10-10.
        with pytest.raises(IntegrityError), transaction.atomic():
            factories.make_exception(
                demo_term.term,
                date=date(2026, 10, 17),
                kind="replace",
                source_teaching_date=date(2026, 10, 5),
            )

    def test_non_replace_with_source_is_rejected(self, demo_term) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            factories.make_exception(
                demo_term.term,
                date=date(2026, 10, 17),
                kind="cancel",
                source_teaching_date=date(2026, 10, 5),
            )


class TestEnrollmentConstraints:
    def test_overlapping_ranges_are_rejected_by_clean(self, demo_term) -> None:
        enrollment = Enrollment(
            offering=demo_term.offerings["OFF-CRIME-2026A"],
            student=demo_term.students["000001"],
            effective_from=date(2026, 10, 1),
            effective_until=date(2026, 11, 1),
        )
        with pytest.raises(ValidationError) as exc_info:
            enrollment.full_clean()
        assert "effective_from" in exc_info.value.message_dict

    def test_adjacent_ranges_are_accepted(self, demo_term) -> None:
        enrollment = Enrollment(
            offering=demo_term.offerings["OFF-CRIME-2026A"],
            student=demo_term.students["000003"],
            effective_from=date(2026, 10, 1),
            effective_until=date(2026, 11, 1),
        )
        enrollment.full_clean()
        enrollment.save()
        assert enrollment.pk is not None


class TestTermConstraints:
    def test_first_week_monday_must_be_a_monday(self, demo_term) -> None:
        term = Term(
            school=demo_term.school,
            code="2026-bad-monday",
            name="虚构错误学期",
            start_date=date(2026, 9, 7),
            end_date=date(2027, 1, 24),
            first_week_monday=date(2026, 9, 8),
            teaching_week_count=20,
        )
        with pytest.raises(ValidationError) as exc_info:
            term.full_clean()
        assert "first_week_monday" in exc_info.value.message_dict

    def test_first_week_monday_cannot_be_after_start(self, demo_term) -> None:
        term = Term(
            school=demo_term.school,
            code="2026-late-week-one",
            name="虚构错误学期",
            start_date=date(2026, 9, 7),
            end_date=date(2027, 1, 24),
            first_week_monday=date(2026, 9, 14),
            teaching_week_count=20,
        )
        with pytest.raises(ValidationError) as exc_info:
            term.full_clean()
        assert "first_week_monday" in exc_info.value.message_dict


class TestPeriodConstraints:
    def test_overlapping_period_times_are_rejected_by_clean(self, demo_term) -> None:
        period = Period(
            term=demo_term.term,
            period_number=9,
            start_time=time(8, 10),
            end_time=time(8, 50),
            block_code="morning",
        )
        with pytest.raises(ValidationError) as exc_info:
            period.full_clean()
        assert "start_time" in exc_info.value.message_dict


class TestDataCoverageConstraints:
    def test_complete_requires_confirmation_ref(self, demo_term) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            DataCoverage.objects.create(
                school=demo_term.school,
                term=demo_term.term,
                kind="calendar",
                scope_key="school",
                from_date=date(2026, 9, 7),
                until_date=date(2027, 1, 25),
                completeness="complete",
                source_updated_at=aware(2026, 10, 26),
                confirmation_ref="",
            )

    def test_partial_without_ref_is_accepted(self, demo_term) -> None:
        coverage = DataCoverage.objects.create(
            school=demo_term.school,
            term=demo_term.term,
            kind="person_blocks",
            scope_key="school",
            from_date=date(2026, 9, 7),
            until_date=date(2027, 1, 25),
            completeness="partial",
            source_updated_at=aware(2026, 10, 26),
        )
        assert coverage.pk is not None
