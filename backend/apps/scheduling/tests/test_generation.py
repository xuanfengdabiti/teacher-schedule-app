"""Rule expansion acceptance tests (A02, import-spec section 6 items 2 and 4)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone as django_timezone

from apps.academics.models import CalendarException, Term
from apps.scheduling.models import ClassSession, ScheduleRule, SessionStatus
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db


def _session(term: Term, rule_code: str, origin_teaching_date: date) -> ClassSession:
    return ClassSession.objects.get(
        offering__term=term,
        rule__rule_code=rule_code,
        origin_teaching_date=origin_teaching_date,
    )


def _local(value: datetime, term: Term) -> datetime:
    return django_timezone.localtime(value, term.tzinfo)


def _complete(session: ClassSession, teacher) -> None:
    """Apply a legal completion to an existing session (B22 will own this)."""
    session.status = SessionStatus.COMPLETED
    session.actual_hours = Decimal("2.0")
    session.completed_at = django_timezone.now()
    session.completed_by_teacher = teacher
    session.completion_ref = "TEST-COMPLETION"
    session.save(
        update_fields=[
            "status",
            "actual_hours",
            "completed_at",
            "completed_by_teacher",
            "completion_ref",
            "updated_at",
        ]
    )


def _single_rule_term(demo_term, code: str) -> tuple[Term, ScheduleRule]:
    """Build an isolated one-rule term so status transitions are unambiguous."""
    term = factories.make_term(
        demo_term.school,
        code=code,
        name=f"虚构状态测试学期 {code}",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 1, 24),
        first_week_monday=date(2026, 9, 7),
        teaching_week_count=20,
    )
    factories.make_periods(term)
    offering = factories.make_offering(
        term,
        demo_term.teachers["0001"],
        offering_code=f"OFF-{code}",
        course_code=f"C-{code}",
        course_name=f"状态测试课程 {code}",
    )
    rule = factories.make_rule(
        offering,
        demo_term.classrooms["B201"],
        (demo_term.teachers["0001"],),
        rule_code=f"R-{code}",
        weekday=1,
        weeks=(1,),
    )
    return term, rule


class TestSampleAcceptance:
    """A02 / import-spec section 6: 40 sessions, 10-05 moved to 10-10, week 8."""

    def test_generates_exactly_40_sessions(self, demo_term) -> None:
        result = generate_sessions_for_term(demo_term.term)

        assert result.created == 40
        assert result.mismatched_count == 0
        assert ClassSession.objects.filter(offering__term=demo_term.term).count() == 40
        assert ClassSession.objects.filter(rule=demo_term.rules["R-CRIME-ODD"]).count() == 10
        assert ClassSession.objects.filter(rule=demo_term.rules["R-CRIME-EVEN"]).count() == 10
        assert ClassSession.objects.filter(rule=demo_term.rules["R-DATA-MON"]).count() == 20

    def test_cancelled_monday_moves_to_replacement_saturday(self, demo_term) -> None:
        generate_sessions_for_term(demo_term.term)
        tz = demo_term.term.tzinfo

        crime = _session(demo_term.term, "R-CRIME-ODD", date(2026, 10, 5))
        data = _session(demo_term.term, "R-DATA-MON", date(2026, 10, 5))

        for session in (crime, data):
            assert _local(session.start_at, demo_term.term).date() == date(2026, 10, 10)
            assert session.origin_teaching_date == date(2026, 10, 5)
            assert session.status == SessionStatus.SCHEDULED
        assert _local(crime.start_at, demo_term.term) == datetime(2026, 10, 10, 8, 0, tzinfo=tz)
        assert _local(data.start_at, demo_term.term) == datetime(2026, 10, 10, 10, 0, tzinfo=tz)

    def test_week_8_teacher_rotation_and_wall_clock(self, demo_term) -> None:
        generate_sessions_for_term(demo_term.term)

        crime = _session(demo_term.term, "R-CRIME-EVEN", date(2026, 10, 26))
        data = _session(demo_term.term, "R-DATA-MON", date(2026, 10, 26))

        assert {teacher.teacher_no for teacher in crime.teachers.all()} == {"0002"}
        assert {teacher.teacher_no for teacher in data.teachers.all()} == {"0001"}
        # 2026-10-26 is teaching week 8 (first week Monday is 2026-09-07).
        assert _local(crime.start_at, demo_term.term).isoformat() == "2026-10-26T08:00:00+08:00"
        assert _local(data.start_at, demo_term.term).isoformat() == "2026-10-26T10:00:00+08:00"

    def test_teaching_week_8(self, demo_term) -> None:
        from apps.scheduling.calendar import teaching_week

        assert teaching_week(demo_term.term, date(2026, 10, 26)) == 8

    def test_generation_does_not_record_completion_operator(self, demo_term) -> None:
        generate_sessions_for_term(demo_term.term)

        assert not ClassSession.objects.filter(recorded_by_user__isnull=False).exists()


class TestIdempotency:
    """B11 acceptance item 2: re-running never duplicates or bumps the version."""

    def test_second_run_creates_nothing_and_keeps_ids_and_version(self, demo_term) -> None:
        first = generate_sessions_for_term(demo_term.term)
        session_ids = set(ClassSession.objects.filter(offering__term=demo_term.term).values_list("id", flat=True))
        assert first.created == 40
        assert first.schedule_version == 2
        assert Term.objects.get(pk=demo_term.term.pk).schedule_version == 2

        second = generate_sessions_for_term(demo_term.term)

        assert second.created == 0
        assert second.unchanged == 40
        assert second.mismatched_count == 0
        assert second.schedule_version == 2
        assert (
            set(ClassSession.objects.filter(offering__term=demo_term.term).values_list("id", flat=True)) == session_ids
        )
        assert Term.objects.get(pk=demo_term.term.pk).schedule_version == 2

    def test_existing_differences_are_reported_not_overwritten(self, demo_term) -> None:
        generate_sessions_for_term(demo_term.term)
        session = _session(demo_term.term, "R-CRIME-EVEN", date(2026, 10, 26))
        session.classroom = demo_term.classrooms["B201"]
        session.save(update_fields=["classroom", "updated_at"])

        result = generate_sessions_for_term(demo_term.term)

        assert result.created == 0
        assert result.unchanged == 39
        assert result.mismatched_count == 1
        assert result.mismatched[0].session_id == session.id
        assert "教室" in result.mismatched[0].differences
        session.refresh_from_db()
        assert session.classroom_id == demo_term.classrooms["B201"].id


class TestStatusCompatibility:
    """Completed classes are progress, not rule mismatches.

    A cancelled plan still conflicts with an already completed session (the
    calendar changed after the class), and a cancelled session conflicts when
    the calendar exception is later withdrawn.
    """

    def test_completed_session_matches_scheduled_plan(self, demo_term) -> None:
        term, rule = _single_rule_term(demo_term, "status-scheduled")
        generate_sessions_for_term(term)
        session = ClassSession.objects.get(rule=rule)
        _complete(session, demo_term.teachers["0001"])

        result = generate_sessions_for_term(term)

        assert result.created == 0
        assert result.unchanged == 1
        assert result.mismatched_count == 0

    def test_completed_session_conflicts_with_new_cancelled_plan(self, demo_term) -> None:
        term, rule = _single_rule_term(demo_term, "status-cancelled")
        generate_sessions_for_term(term)
        session = ClassSession.objects.get(rule=rule)
        _complete(session, demo_term.teachers["0001"])
        factories.make_exception(
            term,
            date=date(2026, 9, 7),
            kind="cancel",
            reason="虚构临时停课",
        )

        result = generate_sessions_for_term(term)

        assert result.created == 0
        assert result.mismatched_count == 1
        assert result.mismatched[0].session_id == session.id
        assert "状态" in result.mismatched[0].differences

    def test_cancelled_session_matches_cancelled_plan(self, demo_term) -> None:
        term, rule = _single_rule_term(demo_term, "status-ongoing-cancel")
        factories.make_exception(
            term,
            date=date(2026, 9, 7),
            kind="cancel",
            reason="虚构停课",
        )
        generate_sessions_for_term(term)
        session = ClassSession.objects.get(rule=rule)
        assert session.status == SessionStatus.CANCELLED

        result = generate_sessions_for_term(term)

        assert result.created == 0
        assert result.unchanged == 1
        assert result.mismatched_count == 0

    def test_cancelled_session_conflicts_with_restored_scheduled_plan(self, demo_term) -> None:
        term, rule = _single_rule_term(demo_term, "status-withdrawn-cancel")
        exception = factories.make_exception(
            term,
            date=date(2026, 9, 7),
            kind="cancel",
            reason="虚构停课",
        )
        generate_sessions_for_term(term)
        exception.delete()

        result = generate_sessions_for_term(term)

        assert result.created == 0
        assert result.mismatched_count == 1
        assert "状态" in result.mismatched[0].differences


class TestCalendarExceptions:
    """B11 acceptance item 3: cancelled sessions stay visible; replacement targets suppress base sessions."""

    def _clear_exceptions(self, term: Term) -> None:
        CalendarException.objects.filter(term=term).delete()

    def test_cancel_without_replace_keeps_cancelled_sessions(self, demo_term) -> None:
        self._clear_exceptions(demo_term.term)
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 5),
            kind="cancel",
            reason="虚构示例停课",
        )

        result = generate_sessions_for_term(demo_term.term)

        assert result.created == 40
        for rule_code in ("R-CRIME-ODD", "R-DATA-MON"):
            session = _session(demo_term.term, rule_code, date(2026, 10, 5))
            assert session.status == SessionStatus.CANCELLED
            assert session.actual_hours == Decimal("0")
            assert session.status_reason == "虚构示例停课"

    def test_custom_day_keeps_cancelled_sessions(self, demo_term) -> None:
        self._clear_exceptions(demo_term.term)
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 5),
            kind="custom",
            reason="虚构示例特殊安排",
        )

        generate_sessions_for_term(demo_term.term)

        session = _session(demo_term.term, "R-CRIME-ODD", date(2026, 10, 5))
        assert session.status == SessionStatus.CANCELLED
        assert session.actual_hours == Decimal("0")
        assert session.status_reason == "虚构示例特殊安排"

    def test_base_sessions_on_replace_target_are_cancelled(self, demo_term) -> None:
        self._clear_exceptions(demo_term.term)
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 12),
            kind="replace",
            source_teaching_date=date(2026, 10, 5),
            reason="虚构示例调至 10-12",
        )

        result = generate_sessions_for_term(demo_term.term)

        assert result.created == 40
        # Sessions from the source day moved to the target day.
        for rule_code in ("R-CRIME-ODD", "R-DATA-MON"):
            moved = _session(demo_term.term, rule_code, date(2026, 10, 5))
            assert _local(moved.start_at, demo_term.term).date() == date(2026, 10, 12)
            assert moved.status == SessionStatus.SCHEDULED
        # The target day's own base sessions are kept but cancelled.
        for rule_code in ("R-CRIME-EVEN", "R-DATA-MON"):
            suppressed = _session(demo_term.term, rule_code, date(2026, 10, 12))
            assert suppressed.status == SessionStatus.CANCELLED
            assert suppressed.actual_hours == Decimal("0")
            assert suppressed.status_reason == "虚构示例调至 10-12"


class TestExceptionValidation:
    """B11 acceptance item 4: replace chains abort the whole generation."""

    def test_replace_chain_is_rejected_without_writes(self, demo_term) -> None:
        CalendarException.objects.filter(term=demo_term.term).delete()
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 6),
            kind="replace",
            source_teaching_date=date(2026, 10, 5),
        )
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 7),
            kind="replace",
            source_teaching_date=date(2026, 10, 6),
        )

        with pytest.raises(ValidationError) as exc_info:
            generate_sessions_for_term(demo_term.term)

        assert any("禁止替换链" in message for message in exc_info.value.messages)
        assert ClassSession.objects.filter(offering__term=demo_term.term).count() == 0

    def test_replace_source_outside_term_is_rejected(self, demo_term) -> None:
        CalendarException.objects.filter(term=demo_term.term).delete()
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 6),
            kind="replace",
            source_teaching_date=date(2027, 2, 1),
        )

        with pytest.raises(ValidationError) as exc_info:
            generate_sessions_for_term(demo_term.term)

        assert any("不在学期起止日期内" in message for message in exc_info.value.messages)
        assert ClassSession.objects.filter(offering__term=demo_term.term).count() == 0


class TestBoundaries:
    """B11 acceptance item 5: term start, teaching-week count and `teaching_week` edges."""

    def test_term_starting_on_wednesday_skips_first_week_monday_and_tuesday(self, demo_term) -> None:
        term = factories.make_term(
            demo_term.school,
            code="2026-late-start",
            name="虚构晚开学学期",
            start_date=date(2026, 9, 9),
            end_date=date(2027, 6, 30),
            first_week_monday=date(2026, 9, 7),
            teaching_week_count=4,
        )
        factories.make_periods(term)
        teacher = factories.make_teacher(demo_term.school, "0099")
        classroom = factories.make_classroom(demo_term.school, "C101", room_no="101")
        offering = factories.make_offering(
            term,
            teacher,
            offering_code="OFF-LATE-2026",
            course_code="C-LATE",
            course_name="边界示例课程",
        )
        monday = factories.make_rule(
            offering,
            classroom,
            (teacher,),
            rule_code="R-LATE-MON",
            weekday=1,
            weeks=(1, 2),
        )
        tuesday = factories.make_rule(
            offering,
            classroom,
            (teacher,),
            rule_code="R-LATE-TUE",
            weekday=2,
            weeks=(1, 2),
        )
        wednesday = factories.make_rule(
            offering,
            classroom,
            (teacher,),
            rule_code="R-LATE-WED",
            weekday=3,
            weeks=(1, 2),
        )
        beyond_count = factories.make_rule(
            offering,
            classroom,
            (teacher,),
            rule_code="R-LATE-FUTURE",
            weekday=1,
            weeks=(21,),
        )

        result = generate_sessions_for_term(term)

        assert result.created == 4
        assert not ClassSession.objects.filter(rule=monday, origin_teaching_date=date(2026, 9, 7)).exists()
        assert not ClassSession.objects.filter(rule=tuesday, origin_teaching_date=date(2026, 9, 8)).exists()
        assert ClassSession.objects.filter(rule=monday, origin_teaching_date=date(2026, 9, 14)).exists()
        assert ClassSession.objects.filter(rule=tuesday, origin_teaching_date=date(2026, 9, 15)).exists()
        assert ClassSession.objects.filter(rule=wednesday, origin_teaching_date=date(2026, 9, 9)).exists()
        # Week 21 is inside end_date but beyond teaching_week_count and must not
        # generate anything (month-long winter break example).
        assert not ClassSession.objects.filter(rule=beyond_count).exists()

    def test_teaching_week_returns_none_outside_term_and_beyond_count(self, demo_term) -> None:
        from apps.scheduling.calendar import teaching_week

        term = demo_term.term
        assert teaching_week(term, date(2026, 9, 6)) is None
        assert teaching_week(term, date(2026, 9, 7)) == 1
        assert teaching_week(term, date(2027, 1, 24)) == 20
        assert teaching_week(term, date(2027, 1, 25)) is None

        long_term = factories.make_term(
            demo_term.school,
            code="2026-long",
            start_date=date(2026, 9, 7),
            end_date=date(2027, 6, 30),
            first_week_monday=date(2026, 9, 7),
            teaching_week_count=20,
        )
        # Week 22 lies inside the calendar dates but outside the configured weeks.
        assert teaching_week(long_term, date(2027, 2, 1)) is None


class TestValidationAndDuplicates:
    def test_rule_teacher_outside_offering_scope_is_rejected(self, demo_term) -> None:
        outsider = factories.make_teacher(demo_term.school, "0003")
        factories.make_rule(
            demo_term.offerings["OFF-DATA-2026A"],
            demo_term.classrooms["A302"],
            (outsider,),
            rule_code="R-DATA-OUTSIDE",
            start_period=5,
            end_period=6,
            weeks=(1,),
        )

        with pytest.raises(ValidationError) as exc_info:
            generate_sessions_for_term(demo_term.term)

        assert any("任课范围" in message for message in exc_info.value.messages)
        assert ClassSession.objects.filter(offering__term=demo_term.term).count() == 0

    def test_identical_slots_from_different_rules_are_flagged(self, demo_term) -> None:
        factories.make_rule(
            demo_term.offerings["OFF-DATA-2026A"],
            demo_term.classrooms["A302"],
            (demo_term.teachers["0001"],),
            rule_code="R-DATA-DUP",
            start_period=3,
            end_period=4,
            weeks=tuple(range(1, 21)),
        )

        result = generate_sessions_for_term(demo_term.term)

        assert result.created == 60
        assert len(result.suspected_duplicates) == 20
        assert result.suspected_duplicates[0].rule_codes == ("R-DATA-DUP", "R-DATA-MON")
