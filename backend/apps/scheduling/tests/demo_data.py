"""Shared fictional demo term used by scheduling and teaching tests.

The data mirrors ``templates/import/*.csv`` (fictional school, term
2026-autumn, 8 periods, 2 teachers, 3 students, 3 classrooms, 2 offerings,
4 enrolments, 2 calendar exceptions, 3 rules).  It intentionally contains no
CSV parser: the importer is B12 and out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.academics.models import CourseOffering, Period, School, Student, TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.scheduling.models import ScheduleRule
from apps.scheduling.tests import factories


@dataclass(frozen=True)
class DemoTermData:
    school: School
    term: Term
    periods: dict[int, Period]
    teachers: dict[str, TeacherProfile]
    students: dict[str, Student]
    classrooms: dict[str, Classroom]
    offerings: dict[str, CourseOffering]
    rules: dict[str, ScheduleRule]


def build_demo_term() -> DemoTermData:
    school = factories.make_school()
    term = factories.make_term(school)
    periods = factories.make_periods(term)

    teachers = {
        "0001": factories.make_teacher(school, "0001", "示例教师甲"),
        "0002": factories.make_teacher(school, "0002", "示例教师乙"),
    }
    students = {
        "000001": factories.make_student(school, "000001"),
        "000002": factories.make_student(school, "000002"),
        "000003": factories.make_student(school, "000003"),
    }
    classrooms = {
        "A301": factories.make_classroom(school, "A301", room_no="301", capacity=30),
        "A302": factories.make_classroom(
            school,
            "A302",
            room_no="302",
            capacity=20,
            equipment=("projector",),
        ),
        "B201": factories.make_classroom(
            school,
            "B201",
            building="示例教学楼B",
            room_no="201",
            capacity=50,
        ),
    }

    crime = factories.make_offering(
        term,
        teachers["0001"],
        offering_code="OFF-CRIME-2026A",
        course_code="C-CRIME",
        course_name="刑事侦查专题研究（示例）",
        co_teachers=(teachers["0002"],),
    )
    data = factories.make_offering(
        term,
        teachers["0001"],
        offering_code="OFF-DATA-2026A",
        course_code="C-DATA",
        course_name="公安大数据分析（示例）",
    )

    enrollment_until = date(2027, 1, 25)
    for student_no in ("000001", "000002"):
        factories.make_enrollment(
            crime,
            students[student_no],
            effective_until=enrollment_until,
        )
    for student_no in ("000001", "000003"):
        factories.make_enrollment(
            data,
            students[student_no],
            effective_until=enrollment_until,
        )

    factories.make_exception(
        term,
        date=date(2026, 10, 5),
        kind="cancel",
        reason="虚构示例停课",
    )
    factories.make_exception(
        term,
        date=date(2026, 10, 10),
        kind="replace",
        source_teaching_date=date(2026, 10, 5),
        reason="虚构示例周六执行10月5日教学安排",
    )

    odd_weeks = tuple(range(1, 21, 2))
    even_weeks = tuple(range(2, 21, 2))
    rules = {
        "R-CRIME-ODD": factories.make_rule(
            crime,
            classrooms["A301"],
            (teachers["0001"],),
            rule_code="R-CRIME-ODD",
            weekday=1,
            start_period=1,
            end_period=2,
            weeks=odd_weeks,
            planned_hours=Decimal("2.0"),
        ),
        "R-CRIME-EVEN": factories.make_rule(
            crime,
            classrooms["A301"],
            (teachers["0002"],),
            rule_code="R-CRIME-EVEN",
            weekday=1,
            start_period=1,
            end_period=2,
            weeks=even_weeks,
            planned_hours=Decimal("2.0"),
        ),
        "R-DATA-MON": factories.make_rule(
            data,
            classrooms["A302"],
            (teachers["0001"],),
            rule_code="R-DATA-MON",
            weekday=1,
            start_period=3,
            end_period=4,
            weeks=tuple(range(1, 21)),
            planned_hours=Decimal("2.0"),
        ),
    }

    return DemoTermData(
        school=school,
        term=term,
        periods=periods,
        teachers=teachers,
        students=students,
        classrooms=classrooms,
        offerings={"OFF-CRIME-2026A": crime, "OFF-DATA-2026A": data},
        rules=rules,
    )
