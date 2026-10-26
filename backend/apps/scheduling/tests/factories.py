"""ORM builders for fictional data mirroring ``templates/import/*.csv``.

These helpers call ``objects.create`` (never ``full_clean``) so tests can also
construct deliberately invalid rows to exercise database constraints.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

from apps.academics.models import (
    CalendarException,
    Course,
    CourseOffering,
    Enrollment,
    Period,
    School,
    Student,
    TeacherProfile,
    TeachingAssignment,
    TeachingClass,
    Term,
)
from apps.classrooms.models import Classroom
from apps.scheduling.models import ScheduleRule

DEFAULT_PERIODS: tuple[tuple[int, time, time, str], ...] = (
    (1, time(8, 0), time(8, 45), "morning"),
    (2, time(8, 55), time(9, 40), "morning"),
    (3, time(10, 0), time(10, 45), "morning"),
    (4, time(10, 55), time(11, 40), "morning"),
    (5, time(14, 0), time(14, 45), "afternoon"),
    (6, time(14, 55), time(15, 40), "afternoon"),
    (7, time(16, 0), time(16, 45), "afternoon"),
    (8, time(16, 55), time(17, 40), "afternoon"),
)


def make_school(code: str = "DEMO-SCHOOL", name: str = "虚构演示学校") -> School:
    return School.objects.create(code=code, name=name)


def make_term(
    school: School,
    *,
    code: str = "2026-autumn",
    name: str = "虚构演示学校2026秋季学期",
    start_date: date = date(2026, 9, 7),
    end_date: date = date(2027, 1, 24),
    first_week_monday: date = date(2026, 9, 7),
    teaching_week_count: int = 20,
    timezone: str = "Asia/Shanghai",
    hour_minutes: int = 45,
) -> Term:
    return Term.objects.create(
        school=school,
        code=code,
        name=name,
        start_date=start_date,
        end_date=end_date,
        first_week_monday=first_week_monday,
        teaching_week_count=teaching_week_count,
        timezone=timezone,
        hour_minutes=hour_minutes,
    )


def make_periods(
    term: Term,
    rows: tuple[tuple[int, time, time, str], ...] = DEFAULT_PERIODS,
) -> dict[int, Period]:
    periods: dict[int, Period] = {}
    for number, start_time, end_time, block_code in rows:
        periods[number] = Period.objects.create(
            term=term,
            period_number=number,
            start_time=start_time,
            end_time=end_time,
            block_code=block_code,
        )
    return periods


def make_teacher(school: School, teacher_no: str, formal_name: str | None = None) -> TeacherProfile:
    return TeacherProfile.objects.create(
        school=school,
        teacher_no=teacher_no,
        formal_name=formal_name or f"示例教师{teacher_no}",
        department="虚构侦查教研室",
        email=f"t{teacher_no}@example.invalid",
    )


def make_student(school: School, student_no: str) -> Student:
    return Student.objects.create(school=school, student_no=student_no)


def make_classroom(
    school: School,
    classroom_code: str,
    *,
    campus: str = "虚构校区",
    building: str = "示例教学楼A",
    room_no: str | None = None,
    capacity: int = 30,
    equipment: tuple[str, ...] = ("projector", "computer"),
) -> Classroom:
    return Classroom.objects.create(
        school=school,
        classroom_code=classroom_code,
        campus=campus,
        building=building,
        room_no=room_no or classroom_code[-3:],
        capacity=capacity,
        equipment=list(equipment),
    )


def make_offering(
    term: Term,
    lead_teacher: TeacherProfile,
    *,
    offering_code: str,
    course_code: str,
    course_name: str,
    class_code: str = "TC-2026-A",
    class_name: str = "2026级示例教学班A",
    target_hours: Decimal = Decimal("40.0"),
    required_equipment: tuple[str, ...] = ("projector",),
    co_teachers: tuple[TeacherProfile, ...] = (),
) -> CourseOffering:
    course, _ = Course.objects.get_or_create(
        school=term.school,
        course_code=course_code,
        defaults={"name": course_name},
    )
    teaching_class, _ = TeachingClass.objects.get_or_create(
        term=term,
        class_code=class_code,
        defaults={"name": class_name},
    )
    offering = CourseOffering.objects.create(
        term=term,
        offering_code=offering_code,
        course=course,
        teaching_class=teaching_class,
        target_hours=target_hours,
        lead_teacher=lead_teacher,
        required_equipment=list(required_equipment),
    )
    until = term.end_date + timedelta(days=1)
    TeachingAssignment.objects.create(
        offering=offering,
        teacher=lead_teacher,
        role="lead",
        effective_from=term.start_date,
        effective_until=until,
    )
    for teacher in co_teachers:
        TeachingAssignment.objects.create(
            offering=offering,
            teacher=teacher,
            role="teacher",
            effective_from=term.start_date,
            effective_until=until,
        )
    return offering


def make_enrollment(
    offering: CourseOffering,
    student: Student,
    *,
    effective_from: date | None = None,
    effective_until: date | None = None,
) -> Enrollment:
    return Enrollment.objects.create(
        offering=offering,
        student=student,
        effective_from=effective_from or offering.term.start_date,
        effective_until=effective_until,
    )


def make_exception(
    term: Term,
    *,
    date: date,
    kind: str,
    source_teaching_date: date | None = None,
    reason: str = "虚构示例校历说明",
) -> CalendarException:
    return CalendarException.objects.create(
        term=term,
        date=date,
        kind=kind,
        source_teaching_date=source_teaching_date,
        reason=reason,
    )


def make_rule(
    offering: CourseOffering,
    classroom: Classroom,
    teachers: tuple[TeacherProfile, ...],
    *,
    rule_code: str,
    weekday: int = 1,
    start_period: int = 1,
    end_period: int = 2,
    weeks: tuple[int, ...] = (1,),
    planned_hours: Decimal = Decimal("2.0"),
) -> ScheduleRule:
    rule = ScheduleRule.objects.create(
        offering=offering,
        rule_code=rule_code,
        weekday=weekday,
        start_period=start_period,
        end_period=end_period,
        weeks=list(weeks),
        classroom=classroom,
        planned_hours=planned_hours,
    )
    rule.teachers.set(teachers)
    return rule
