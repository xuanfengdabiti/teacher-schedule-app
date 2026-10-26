"""B30 reschedule candidate tests (A04-A08, A21, A22, A26).

All tests use the fictional demo term and the fixed morning clock from the task:
2026-10-26 07:00 +08:00.  They never call the wall clock inside the service.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.academics.models import CoverageCompleteness, CoverageKind, DataCoverage
from apps.classrooms.models import Classroom, RoomBlock
from apps.scheduling.models import (
    ClassSession,
    PersonBlock,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.reschedule_options import (
    ELIGIBILITY_AVAILABLE,
    ELIGIBILITY_CONFLICT,
    ELIGIBILITY_UNVERIFIED,
    OptionQuery,
    find_reschedule_options,
)
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 26, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
SOURCE_DATE = date(2026, 10, 26)


def _add_school_coverage(
    term,
    *,
    kinds: tuple[str, ...] | None = None,
    start: date | None = None,
    end: date | None = None,
    completeness: str = CoverageCompleteness.COMPLETE,
) -> list[DataCoverage]:
    """Create school-level coverage rows for all eight kinds by default."""
    selected_kinds = kinds or tuple(CoverageKind.values)
    from_date = start or term.start_date
    until_date = end or term.end_date + timedelta(days=1)
    return [
        DataCoverage.objects.create(
            school=term.school,
            term=term,
            kind=kind,
            scope_key="school",
            from_date=from_date,
            until_date=until_date,
            completeness=completeness,
            source_updated_at=NOW,
            confirmation_ref="TEST-CONFIRMATION" if completeness == CoverageCompleteness.COMPLETE else "",
        )
        for kind in selected_kinds
    ]


def _crime_source(demo_term) -> ClassSession:
    return ClassSession.objects.select_related(
        "offering__term",
        "offering__course",
        "classroom",
    ).get(
        rule=demo_term.rules["R-CRIME-EVEN"],
        origin_teaching_date=SOURCE_DATE,
    )


def _result(
    source: ClassSession,
    *,
    date_from: date,
    date_until: date,
    allowed_block_codes: set[str] | None = None,
    classroom_ids=None,
    allow_weekend: bool = False,
    now: datetime = NOW,
):
    return find_reschedule_options(
        source,
        OptionQuery(
            date_from=date_from,
            date_until=date_until,
            allowed_block_codes=allowed_block_codes,
            classroom_ids=classroom_ids,
            allow_weekend=allow_weekend,
        ),
        now=now,
    )


def _local_date(option, term) -> date:
    return option.start_at.astimezone(ZoneInfo(term.timezone)).date()


def _find_option(result, term, *, day: date, start_period: int, classroom_code: str | None = None):
    for option in result.options:
        if _local_date(option, term) != day or option.start_period != start_period:
            continue
        if classroom_code is not None and option.classroom_code != classroom_code:
            continue
        return option
    return None


def _create_session(
    offering,
    *,
    classroom,
    teacher,
    start_at: datetime,
    end_at: datetime,
    start_period: int,
    end_period: int,
    status: str = SessionStatus.SCHEDULED,
) -> ClassSession:
    session = ClassSession.objects.create(
        offering=offering,
        start_period=start_period,
        end_period=end_period,
        start_at=start_at,
        end_at=end_at,
        classroom=classroom,
        status=status,
        planned_hours=Decimal("2.0"),
        actual_hours=Decimal("0") if status == SessionStatus.CANCELLED else None,
    )
    SessionTeacher.objects.create(session=session, teacher=teacher)
    return session


class TestConflictChecks:
    """A04, A07, A21 and the "same class but not enrolled" boundary."""

    def test_a04_cross_offering_student_conflict_is_reported_on_the_crime_session(self, generated_demo) -> None:
        source = _crime_source(generated_demo)

        result = _result(source, date_from=SOURCE_DATE, date_until=SOURCE_DATE + timedelta(days=1))

        options = [option for option in result.options if option.start_period == 3 and option.end_period == 4]
        assert {option.classroom_code for option in options} == {"A301", "A302", "B201"}
        for option in options:
            assert option.eligibility == ELIGIBILITY_CONFLICT
            assert option.student_conflict_count == 1
            assert option.eligibility != ELIGIBILITY_AVAILABLE
            assert any(
                conflict.category == "student" and conflict.course_name == "公安大数据分析（示例）"
                for conflict in option.conflicts
            )
            assert any("学生冲突 1 人" in reason for reason in option.reasons)

    def test_a04_student_who_is_only_in_the_teaching_class_is_not_counted(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        factories.make_student(generated_demo.school, "000004")

        result = _result(source, date_from=SOURCE_DATE, date_until=SOURCE_DATE + timedelta(days=1))

        option = _find_option(result, generated_demo.term, day=SOURCE_DATE, start_period=3, classroom_code="A301")
        assert option is not None
        assert option.student_conflict_count == 1

    def test_a07_adjacent_end_and_start_times_do_not_overlap(self, demo_term) -> None:
        term = factories.make_term(
            demo_term.school,
            code="boundary-2026",
            name="虚构调课边界学期",
            start_date=date(2026, 9, 7),
            end_date=date(2027, 1, 24),
            first_week_monday=date(2026, 9, 7),
            teaching_week_count=20,
        )
        factories.make_periods(
            term,
            rows=(
                (1, time(8, 55), time(9, 40), "morning"),
                (2, time(9, 40), time(10, 25), "morning"),
                (3, time(10, 35), time(11, 20), "morning"),
            ),
        )
        teacher = factories.make_teacher(demo_term.school, "B0001", "边界示例教师")
        student = factories.make_student(demo_term.school, "B000001")
        classroom = factories.make_classroom(demo_term.school, "BND101", room_no="101")
        offering = factories.make_offering(
            term,
            teacher,
            offering_code="OFF-BOUNDARY-2026",
            course_code="C-BOUNDARY",
            course_name="边界示例课程",
        )
        factories.make_enrollment(offering, student)
        factories.make_rule(
            offering,
            classroom,
            (teacher,),
            rule_code="R-BOUNDARY",
            weekday=1,
            start_period=1,
            end_period=1,
            weeks=(8,),
        )
        generate_sessions_for_term(term)
        source = ClassSession.objects.select_related(
            "offering__term",
            "offering__course",
            "classroom",
        ).get(offering=offering)

        other_offering = factories.make_offering(
            term,
            teacher,
            offering_code="OFF-BOUNDARY-OTHER",
            course_code="C-BOUNDARY-OTHER",
            course_name="边界前一节示例课程",
        )
        tzinfo = ZoneInfo(term.timezone)
        _create_session(
            other_offering,
            classroom=classroom,
            teacher=teacher,
            start_at=datetime(2026, 10, 26, 8, 55, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 26, 9, 40, tzinfo=tzinfo),
            start_period=1,
            end_period=1,
        )
        _add_school_coverage(term)

        result = _result(
            source,
            date_from=date(2026, 10, 26),
            date_until=date(2026, 10, 27),
            allowed_block_codes={"morning"},
        )

        option = _find_option(result, term, day=date(2026, 10, 26), start_period=2, classroom_code="BND101")
        assert option is not None
        assert option.start_at == datetime(2026, 10, 26, 9, 40, tzinfo=tzinfo)
        assert option.eligibility == ELIGIBILITY_AVAILABLE
        assert option.conflicts == ()

    def test_occupancy_crossing_midnight_is_indexed_on_the_next_day(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        tzinfo = ZoneInfo(term.timezone)
        other_offering = factories.make_offering(
            term,
            generated_demo.teachers["0002"],
            offering_code="OFF-OVERNIGHT-2026",
            course_code="C-OVERNIGHT",
            course_name="跨日占用示例课程",
        )
        _create_session(
            other_offering,
            classroom=generated_demo.classrooms["B201"],
            teacher=generated_demo.teachers["0002"],
            start_at=datetime(2026, 10, 26, 23, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 27, 8, 30, tzinfo=tzinfo),
            start_period=1,
            end_period=1,
        )

        result = _result(
            source,
            date_from=date(2026, 10, 27),
            date_until=date(2026, 10, 28),
            allowed_block_codes={"morning"},
        )

        option = _find_option(result, term, day=date(2026, 10, 27), start_period=1, classroom_code="A301")
        assert option is not None
        assert option.eligibility == ELIGIBILITY_CONFLICT
        assert any(conflict.category == "teacher" for conflict in option.conflicts)

    def test_other_term_in_same_school_occupies_teacher_and_classroom(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        tzinfo = ZoneInfo(term.timezone)
        other_term = factories.make_term(
            generated_demo.school,
            code="2026-short",
            name="虚构短学期",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 12, 31),
            first_week_monday=date(2026, 9, 7),
            teaching_week_count=16,
        )
        other_offering = factories.make_offering(
            other_term,
            generated_demo.teachers["0002"],
            offering_code="OFF-SHORT-2026",
            course_code="C-SHORT",
            course_name="短学期示例课程",
        )
        _create_session(
            other_offering,
            classroom=generated_demo.classrooms["A301"],
            teacher=generated_demo.teachers["0002"],
            start_at=datetime(2026, 10, 27, 14, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 27, 15, 40, tzinfo=tzinfo),
            start_period=5,
            end_period=6,
        )

        result = _result(
            source,
            date_from=date(2026, 10, 27),
            date_until=date(2026, 10, 28),
            allowed_block_codes={"afternoon"},
        )

        same_room = _find_option(result, term, day=date(2026, 10, 27), start_period=5, classroom_code="A301")
        other_room = _find_option(result, term, day=date(2026, 10, 27), start_period=5, classroom_code="B201")
        assert same_room is not None
        assert same_room.eligibility == ELIGIBILITY_CONFLICT
        assert {conflict.category for conflict in same_room.conflicts} == {"teacher", "classroom"}
        assert other_room is not None
        assert other_room.eligibility == ELIGIBILITY_CONFLICT
        assert {conflict.category for conflict in other_room.conflicts} == {"teacher"}

    def test_a21_person_blocks_and_student_deduplication(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        tzinfo = ZoneInfo(term.timezone)
        student = generated_demo.students["000001"]
        teacher = generated_demo.teachers["0002"]
        data_offering = generated_demo.offerings["OFF-DATA-2026A"]

        # A course conflict plus a PersonBlock for the same student in the same
        # candidate window must still count that student once.
        _create_session(
            data_offering,
            classroom=generated_demo.classrooms["A302"],
            teacher=generated_demo.teachers["0001"],
            start_at=datetime(2026, 10, 26, 14, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 26, 15, 40, tzinfo=tzinfo),
            start_period=5,
            end_period=6,
        )
        PersonBlock.objects.create(
            student=student,
            start_at=datetime(2026, 10, 26, 14, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 26, 15, 0, tzinfo=tzinfo),
            reason="虚构学生考试安排",
            block_code="PB-STUDENT",
        )
        PersonBlock.objects.create(
            teacher=teacher,
            start_at=datetime(2026, 10, 26, 16, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 26, 17, 0, tzinfo=tzinfo),
            reason="虚构教师会议",
            block_code="PB-TEACHER",
        )

        result = _result(source, date_from=SOURCE_DATE, date_until=SOURCE_DATE + timedelta(days=1))

        student_option = _find_option(result, term, day=SOURCE_DATE, start_period=5, classroom_code="B201")
        assert student_option is not None
        assert student_option.eligibility == ELIGIBILITY_CONFLICT
        assert student_option.student_conflict_count == 1
        assert sum(conflict.category == "student" for conflict in student_option.conflicts) >= 2

        teacher_option = _find_option(result, term, day=SOURCE_DATE, start_period=7, classroom_code="B201")
        assert teacher_option is not None
        assert teacher_option.eligibility == ELIGIBILITY_CONFLICT
        assert any(
            conflict.category == "teacher" and conflict.reason == "虚构教师会议"
            for conflict in teacher_option.conflicts
        )


class TestCapacityAndCoverage:
    """A05, A06/A26 and the hard capacity/equipment conditions."""

    def test_a05_capacity_uses_active_enrolment_on_target_date(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        target = date(2026, 10, 27)
        crime = generated_demo.offerings["OFF-CRIME-2026A"]

        for index in range(16):
            student = factories.make_student(generated_demo.school, f"1000{index:02d}")
            factories.make_enrollment(crime, student)
        withdrawn = factories.make_student(generated_demo.school, "199999")
        factories.make_enrollment(crime, withdrawn, effective_until=target)

        original_room = Classroom.objects.get(pk=generated_demo.classrooms["A301"].pk)
        original_room.capacity = 20
        original_room.save(update_fields=["capacity", "updated_at"])
        exactly_eighteen = Classroom.objects.get(pk=generated_demo.classrooms["A302"].pk)
        exactly_eighteen.capacity = 18
        exactly_eighteen.save(update_fields=["capacity", "updated_at"])
        too_small = Classroom.objects.get(pk=generated_demo.classrooms["B201"].pk)
        too_small.capacity = 17
        too_small.save(update_fields=["capacity", "updated_at"])

        result = _result(
            source,
            date_from=target,
            date_until=target + timedelta(days=1),
            allowed_block_codes={"afternoon"},
        )

        # The withdrawn student is not active on 10-27; counting them would
        # make 19 and incorrectly exclude the capacity-18 room.
        twenty_capacity = _find_option(result, term, day=target, start_period=5, classroom_code="A301")
        eighteen_capacity = _find_option(result, term, day=target, start_period=5, classroom_code="A302")
        too_small_option = _find_option(result, term, day=target, start_period=5, classroom_code="B201")
        assert twenty_capacity is not None
        assert eighteen_capacity is not None
        assert too_small_option is None

    def test_a26_coverage_turns_unverified_into_available_and_back(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        target = date(2026, 10, 27)

        no_coverage = _result(source, date_from=target, date_until=target + timedelta(days=1))
        assert no_coverage.data_completeness == "partial"
        assert no_coverage.options
        assert all(option.eligibility == ELIGIBILITY_UNVERIFIED for option in no_coverage.options)
        assert set(no_coverage.options[0].missing_data) == set(CoverageKind.values)

        _add_school_coverage(term)
        complete = _result(source, date_from=target, date_until=target + timedelta(days=1))
        assert complete.data_completeness == "complete"
        assert all(option.eligibility == ELIGIBILITY_AVAILABLE for option in complete.options)
        assert all(option.missing_data == () for option in complete.options)

        DataCoverage.objects.filter(term=term, kind=CoverageKind.STUDENT_SCHEDULE).delete()
        missing_student_schedule = _result(source, date_from=target, date_until=target + timedelta(days=1))
        assert missing_student_schedule.data_completeness == "partial"
        assert all(CoverageKind.STUDENT_SCHEDULE in option.missing_data for option in missing_student_schedule.options)
        assert all(option.eligibility == ELIGIBILITY_UNVERIFIED for option in missing_student_schedule.options)
        assert any("缺少数据：学生全校排课" in reason for reason in missing_student_schedule.options[0].reasons)

        # An until-exclusive boundary equal to the target date does not cover it.
        DataCoverage.objects.create(
            school=term.school,
            term=term,
            kind=CoverageKind.STUDENT_SCHEDULE,
            scope_key="school",
            from_date=term.start_date,
            until_date=target,
            completeness=CoverageCompleteness.COMPLETE,
            source_updated_at=NOW,
            confirmation_ref="TEST-UNTIL-BOUNDARY",
        )
        boundary_missing = _result(source, date_from=target, date_until=target + timedelta(days=1))
        assert all(CoverageKind.STUDENT_SCHEDULE in option.missing_data for option in boundary_missing.options)

        # A complete row must not hide an explicitly declared partial row for
        # the same kind/scope on the target date.
        DataCoverage.objects.filter(term=term, kind=CoverageKind.STUDENT_SCHEDULE).delete()
        DataCoverage.objects.create(
            school=term.school,
            term=term,
            kind=CoverageKind.STUDENT_SCHEDULE,
            scope_key="school",
            from_date=term.start_date,
            until_date=term.end_date + timedelta(days=1),
            completeness=CoverageCompleteness.COMPLETE,
            source_updated_at=NOW,
            confirmation_ref="TEST-COMPLETE",
        )
        DataCoverage.objects.create(
            school=term.school,
            term=term,
            kind=CoverageKind.STUDENT_SCHEDULE,
            scope_key="school",
            from_date=term.start_date,
            until_date=term.end_date + timedelta(days=1),
            completeness=CoverageCompleteness.PARTIAL,
            source_updated_at=NOW,
        )
        conservative = _result(source, date_from=target, date_until=target + timedelta(days=1))
        assert all(CoverageKind.STUDENT_SCHEDULE in option.missing_data for option in conservative.options)
        assert all(option.eligibility == ELIGIBILITY_UNVERIFIED for option in conservative.options)


class TestClassificationAndOrdering:
    """A22: three classes and a stable recommendation order."""

    def test_a22_only_three_classes_and_group_order(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        tzinfo = ZoneInfo(term.timezone)

        # Cover only dates from 10-29 onwards: 10-26..10-28 is deliberately
        # unverified, while a meeting adds a known conflict inside that gap.
        _add_school_coverage(term, start=date(2026, 10, 29))
        PersonBlock.objects.create(
            teacher=generated_demo.teachers["0002"],
            start_at=datetime(2026, 10, 28, 14, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 28, 15, 0, tzinfo=tzinfo),
            reason="虚构候选日会议",
            block_code="PB-A22",
        )

        result = _result(source, date_from=SOURCE_DATE, date_until=date(2026, 10, 31))

        assert {option.eligibility for option in result.options} == {
            ELIGIBILITY_AVAILABLE,
            ELIGIBILITY_CONFLICT,
            ELIGIBILITY_UNVERIFIED,
        }
        ranks = {"available": 0, "conflict": 1, "unverified": 2}
        assert [ranks[option.eligibility] for option in result.options] == sorted(
            ranks[option.eligibility] for option in result.options
        )

        source_date = source.start_at.astimezone(tzinfo).date()
        source_minutes = source.start_at.astimezone(tzinfo).hour * 60 + source.start_at.astimezone(tzinfo).minute
        available = [option for option in result.options if option.eligibility == ELIGIBILITY_AVAILABLE]
        expected = sorted(
            available,
            key=lambda option: (
                0 if option.classroom_id == source.classroom_id else 1,
                abs((option.start_at.astimezone(tzinfo).date() - source_date).days),
                abs(
                    (option.start_at.astimezone(tzinfo).hour * 60 + option.start_at.astimezone(tzinfo).minute)
                    - source_minutes
                ),
                option.start_at,
                option.end_at,
                option.classroom_code,
            ),
        )
        assert available == expected

        known_conflict_with_gap = [
            option for option in result.options if option.eligibility == ELIGIBILITY_CONFLICT and option.missing_data
        ]
        assert known_conflict_with_gap
        assert all(option.missing_data for option in known_conflict_with_gap)


class TestHardConditions:
    """B30 hard exclusions: blocks, time, weekends, exceptions, equipment, status."""

    def test_does_not_cross_blocks_or_include_the_source_slot_room(self, generated_demo) -> None:
        source = _crime_source(generated_demo)

        result = _result(source, date_from=SOURCE_DATE, date_until=SOURCE_DATE + timedelta(days=5))

        assert not any(option.start_period == 4 and option.end_period == 5 for option in result.options)
        source_option = _find_option(
            result,
            generated_demo.term,
            day=SOURCE_DATE,
            start_period=1,
            classroom_code="A301",
        )
        assert source_option is None
        assert (
            _find_option(
                result,
                generated_demo.term,
                day=SOURCE_DATE,
                start_period=1,
                classroom_code="B201",
            )
            is not None
        )

    def test_past_start_times_are_excluded_using_the_injected_now(self, generated_demo) -> None:
        source = _crime_source(generated_demo)

        result = _result(source, date_from=date(2026, 10, 23), date_until=date(2026, 10, 30))

        assert result.options
        assert all(option.start_at > NOW for option in result.options)

    def test_weekends_are_excluded_unless_requested(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        weekend = date(2026, 10, 31)

        default_result = _result(
            source,
            date_from=date(2026, 10, 30),
            date_until=date(2026, 11, 2),
        )
        assert not any(_local_date(option, generated_demo.term).weekday() >= 5 for option in default_result.options)

        weekend_result = _result(
            source,
            date_from=date(2026, 10, 30),
            date_until=date(2026, 11, 2),
            allow_weekend=True,
        )
        assert any(_local_date(option, generated_demo.term) == weekend for option in weekend_result.options)

    def test_any_calendar_exception_date_is_not_a_target(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        excluded = date(2026, 10, 28)
        factories.make_exception(
            generated_demo.term,
            date=excluded,
            kind="cancel",
            reason="虚构候选日停课",
        )

        result = _result(source, date_from=SOURCE_DATE, date_until=date(2026, 10, 30))

        assert not any(_local_date(option, generated_demo.term) == excluded for option in result.options)

    def test_room_without_required_equipment_is_not_a_candidate(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        target = date(2026, 10, 27)
        room = Classroom.objects.get(pk=generated_demo.classrooms["B201"].pk)
        room.equipment = ["computer"]
        room.save(update_fields=["equipment", "updated_at"])

        result = _result(
            source,
            date_from=target,
            date_until=target + timedelta(days=1),
            allowed_block_codes={"afternoon"},
        )

        assert _find_option(result, generated_demo.term, day=target, start_period=5, classroom_code="A301") is not None
        assert _find_option(result, generated_demo.term, day=target, start_period=5, classroom_code="B201") is None

    def test_room_block_marks_only_the_blocked_classroom(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        tzinfo = ZoneInfo(term.timezone)
        target = date(2026, 10, 27)
        RoomBlock.objects.create(
            classroom=generated_demo.classrooms["B201"],
            start_at=datetime(2026, 10, 27, 14, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 27, 15, 0, tzinfo=tzinfo),
            reason="虚构教室检修",
            block_code="RB-A22",
        )

        result = _result(
            source,
            date_from=target,
            date_until=target + timedelta(days=1),
            allowed_block_codes={"afternoon"},
        )

        blocked = _find_option(result, term, day=target, start_period=5, classroom_code="B201")
        free = _find_option(result, term, day=target, start_period=5, classroom_code="A301")
        assert blocked is not None
        assert blocked.eligibility == ELIGIBILITY_CONFLICT
        assert any(
            conflict.category == "classroom" and conflict.reason == "虚构教室检修" for conflict in blocked.conflicts
        )
        assert free is not None
        assert free.eligibility != ELIGIBILITY_CONFLICT

    def test_cancelled_session_does_not_occupy_teacher_or_room(self, generated_demo) -> None:
        source = _crime_source(generated_demo)
        term = generated_demo.term
        tzinfo = ZoneInfo(term.timezone)
        target = date(2026, 10, 27)
        _create_session(
            generated_demo.offerings["OFF-CRIME-2026A"],
            classroom=generated_demo.classrooms["A301"],
            teacher=generated_demo.teachers["0002"],
            start_at=datetime(2026, 10, 27, 14, 0, tzinfo=tzinfo),
            end_at=datetime(2026, 10, 27, 15, 40, tzinfo=tzinfo),
            start_period=5,
            end_period=6,
            status=SessionStatus.CANCELLED,
        )

        result = _result(
            source,
            date_from=target,
            date_until=target + timedelta(days=1),
            allowed_block_codes={"afternoon"},
        )

        option = _find_option(result, term, day=target, start_period=5, classroom_code="A301")
        assert option is not None
        assert option.eligibility != ELIGIBILITY_CONFLICT
        assert option.conflicts == ()


class TestPerformance:
    """The 60-day query must use exactly the same fixed number of statements."""

    def test_14_and_60_day_ranges_have_equal_query_counts(self, generated_demo, django_assert_max_num_queries) -> None:
        source = _crime_source(generated_demo)

        with django_assert_max_num_queries(20):
            with CaptureQueriesContext(connection) as small_queries:
                small = _result(
                    source,
                    date_from=SOURCE_DATE,
                    date_until=SOURCE_DATE + timedelta(days=14),
                )
        with CaptureQueriesContext(connection) as large_queries:
            large = _result(
                source,
                date_from=SOURCE_DATE,
                date_until=SOURCE_DATE + timedelta(days=60),
            )

        assert small.options
        assert large.options
        assert len(small.options) < len(large.options)
        assert len(small_queries) == len(large_queries)


@pytest.fixture
def generated_demo(demo_term):
    generate_sessions_for_term(demo_term.term)
    return demo_term
