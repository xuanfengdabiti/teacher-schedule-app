"""Shared 20,000-session scale fixture for opt-in performance tests.

The data volume follows implementation-plan 11.3: 200 teachers, 5,000
students, 100 classrooms and one 20-week term with 20,000 generated sessions.
Both the B30 candidate query and the B13 weekly schedule API use this builder so
their measurements describe the same school size.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

from apps.academics.models import CourseOffering, Enrollment, Student, TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories as f


@dataclass(frozen=True)
class ScaleData:
    term: Term
    teachers: list[TeacherProfile]
    classrooms: list[Classroom]
    offerings: list[CourseOffering]
    enrollment_count: int


def build_scale_data() -> ScaleData:
    """Create the deterministic 100-room / 1,000-offering / 20,000-session term."""
    rnd = random.Random(7)
    school = f.make_school(code="PERF-SCHOOL", name="大规模性能测试学校")
    term = f.make_term(school, code="perf-term", name="大规模性能测试学期")
    f.make_periods(term)
    classrooms = [f.make_classroom(school, f"R{i:03d}", capacity=60) for i in range(100)]
    teachers = [f.make_teacher(school, f"{i:04d}") for i in range(200)]
    students = Student.objects.bulk_create([Student(school=school, student_no=f"{i:06d}") for i in range(5000)])

    offerings: list[CourseOffering] = []
    slots = [(day, period) for day in range(1, 6) for period in (1, 3, 5, 7)]
    index = 0
    for classroom in classrooms:
        for weekday, start_period in rnd.sample(slots, 10):
            teacher = teachers[index % 200]
            offering = f.make_offering(
                term,
                teacher,
                offering_code=f"O{index:04d}",
                course_code=f"C{index:04d}",
                course_name=f"课程{index}",
                class_code=f"TC{index % 50:02d}",
                target_hours=Decimal("40.0"),
                required_equipment=(),
            )
            f.make_rule(
                offering,
                classroom,
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

    return ScaleData(
        term=term,
        teachers=teachers,
        classrooms=classrooms,
        offerings=offerings,
        enrollment_count=len(enrollments),
    )
