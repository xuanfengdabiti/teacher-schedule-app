"""本地演示：李/张/王三位老师的共享任课与轮讲课程（在 seed_demo_extra.py 之后执行）。

只造演示数据，不新增功能：
- 新建工号 0011/0012/0013 的李、张、王三位老师，首次创建才设密码；
- 把三人加入旧甲 6 门 EXTRA-OFF-1001…1006 的任课范围（不改旧课次/旧实际授课教师/旧负责人）；
- 新建 3 门共享示例课程，李为负责人、三人同在教学范围；周二 1–2/3–4/5–6 由李上，
  周四同节次张上单周、王上双周，20 周校历；每门课独立教学班与 25 名虚构学生；
- 全部使用 TEAM- 稳定编码，get_or_create，重复执行只补缺失，不覆盖用户编辑。

课次只通过现有 ``generate_sessions_for_term`` 生成。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.academics.models import (
    AssignmentRole,
    Course,
    CourseOffering,
    Enrollment,
    School,
    Student,
    TeacherProfile,
    TeacherStatus,
    TeachingAssignment,
    TeachingClass,
    Term,
)
from apps.classrooms.models import Classroom
from apps.scheduling.models import ClassSession, ScheduleRule
from apps.scheduling.services import generate_sessions_for_term

SCHOOL_CODE = "DEMO-SCHOOL"
TERM_CODE = "2026-autumn"
TEACHER_PASSWORD = "TeacherDemo2026!"
User = get_user_model()

TEAM_TEACHERS = (
    ("0011", "李老师", "li@example.invalid"),
    ("0012", "张老师", "zhang@example.invalid"),
    ("0013", "王老师", "wang@example.invalid"),
)

# 旧甲的 6 门课：只扩充任课范围，方便发送交接时选择接收人。
LEGACY_OFFERING_CODES = tuple(f"EXTRA-OFF-{number}" for number in range(1001, 1007))

# 新课程：编号、名称、周二节次（周四同节次由张/王按单双周轮讲）。
TEAM_COURSES = (
    ("001", "证据法专题研讨（示例）", 1, 2),
    ("002", "刑事诉讼案例研讨（示例）", 3, 4),
    ("003", "犯罪心理实务（示例）", 5, 6),
)

# 优先使用木犀校区高警察楼的空教室；被占用时退回到其他空教室。
PREFERRED_ROOMS = ("木犀校区-高警察楼101", "木犀校区-高警察楼201", "木犀校区-高警察楼301")
PREFERRED_CAMPUS = "木犀校区"
PREFERRED_BUILDING = "高警察楼"

ALL_WEEKS = tuple(range(1, 21))
ODD_WEEKS = tuple(range(1, 21, 2))
EVEN_WEEKS = tuple(range(2, 21, 2))
PER_SESSION_HOURS = Decimal("2.0")
TARGET_HOURS = Decimal("80.0")
STUDENTS_PER_COURSE = 25


def fail(message: str) -> None:
    raise RuntimeError(message)


def ensure_teachers(school: School) -> dict[str, TeacherProfile]:
    """工号 0011–0013；已有账号/档案保留，首次创建才设密码。"""
    created = 0
    result: dict[str, TeacherProfile] = {}
    for teacher_no, formal_name, email in TEAM_TEACHERS:
        teacher = TeacherProfile.objects.filter(school=school, teacher_no=teacher_no).first()
        if teacher is None:
            teacher = TeacherProfile.objects.create(
                school=school,
                teacher_no=teacher_no,
                formal_name=formal_name,
                department="虚构侦查教研室",
                email=email,
            )
            created += 1
        user, user_created = User.objects.get_or_create(email=email)
        if user_created:
            user.set_password(TEACHER_PASSWORD)
            user.save()
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )
        changed_fields = []
        if teacher.user_id != user.id:
            teacher.user = user
            changed_fields.append("user")
        if teacher.teacher_status != TeacherStatus.ACTIVE:
            teacher.teacher_status = TeacherStatus.ACTIVE
            changed_fields.append("teacher_status")
        if changed_fields:
            teacher.save(update_fields=[*changed_fields, "updated_at"])
        result[teacher_no] = teacher
    print(f"演示团队：新建 {created} 位老师（0011–0013）。")
    return result


def ensure_legacy_scope(school: School, term: Term, teachers: dict[str, TeacherProfile]) -> int:
    """把三人加入旧甲 6 门课的任课范围；返回新增任课条数。"""
    created_total = 0
    bumped: set[int] = set()
    for offering_code in LEGACY_OFFERING_CODES:
        offering = CourseOffering.objects.filter(term=term, offering_code=offering_code).first()
        if offering is None:
            continue
        for teacher in teachers.values():
            _, created = TeachingAssignment.objects.get_or_create(
                offering=offering,
                teacher=teacher,
                effective_from=term.start_date,
                defaults={
                    "role": AssignmentRole.TEACHER,
                    "effective_until": term.end_date + timedelta(days=1),
                },
            )
            if not created:
                continue
            created_total += 1
            teacher_id = teacher.pk
            if teacher_id in bumped:
                continue
            teacher.assignment_scope_version += 1
            teacher.save(update_fields=["assignment_scope_version", "updated_at"])
            bumped.add(teacher_id)
    print(f"旧课程任课范围：新增 {created_total} 条，涉及 {len(bumped)} 位老师。")
    return created_total


def choose_rooms() -> list[Classroom]:
    """三间用于轮讲课程的教室。

    重复执行时优先沿用 TEAM 规则已经使用的教室，保证课程/教室映射稳定；
    首次执行时从未被任何规则占用的教室中选，优先木犀校区高警察楼。
    """
    existing_ids = list(
        ScheduleRule.objects.filter(rule_code__startswith="TEAM-R-")
        .order_by("rule_code")
        .values_list("classroom_id", flat=True)
    )
    existing_rooms: list[Classroom] = []
    for room_id in dict.fromkeys(existing_ids):
        room = Classroom.objects.filter(pk=room_id).first()
        if room is not None and room not in existing_rooms:
            existing_rooms.append(room)
    if len(existing_rooms) >= 3:
        chosen = existing_rooms[:3]
        print(f"轮讲课程教室（沿用已建立规则）：{'、'.join(room.classroom_code for room in chosen)}")
        return chosen

    used_ids = set(ScheduleRule.objects.values_list("classroom_id", flat=True))
    unused = list(
        Classroom.objects.filter(is_active=True)
        .exclude(pk__in=used_ids)
        .exclude(pk__in=[room.pk for room in existing_rooms])
        .order_by("campus", "building", "room_no")
    )
    preferred = [room for room in unused if room.classroom_code in PREFERRED_ROOMS]
    same_building = [
        room
        for room in unused
        if room.campus == PREFERRED_CAMPUS and room.building == PREFERRED_BUILDING and room not in preferred
    ]
    ordered = (
        existing_rooms + preferred + same_building + [room for room in unused if room not in preferred + same_building]
    )
    if len(ordered) < 3:
        fail(f"空闲教室不足 3 间，实际 {len(ordered)} 间。")
    chosen = ordered[:3]
    print(f"轮讲课程教室：{'、'.join(room.classroom_code for room in chosen)}")
    return chosen


def ensure_students(school: School) -> dict[str, list[Student]]:
    result: dict[str, list[Student]] = {}
    created = 0
    for course_number, _, _, _ in TEAM_COURSES:
        numbers = [f"TEAM-{course_number}-{index:03d}" for index in range(1, STUDENTS_PER_COURSE + 1)]
        existing = {
            student.student_no: student for student in Student.objects.filter(school=school, student_no__in=numbers)
        }
        for number in numbers:
            if number in existing:
                continue
            student = Student.objects.create(school=school, student_no=number)
            existing[number] = student
            created += 1
        result[course_number] = [existing[number] for number in numbers]
    print(f"团队课程学生：新建 {created} 名（3 门 × 25 名，独立虚构）。")
    return result


def ensure_course_data(
    school: School,
    term: Term,
    teachers: dict[str, TeacherProfile],
    students: dict[str, list[Student]],
    rooms: list[Classroom],
) -> None:
    li = teachers["0011"]
    zhang = teachers["0012"]
    wang = teachers["0013"]
    for index, (number, name, start_period, end_period) in enumerate(TEAM_COURSES):
        course_code = f"TEAM-C-{number}"
        course = Course.objects.filter(school=school, course_code=course_code).first()
        if course is None:
            course = Course.objects.create(school=school, course_code=course_code, name=name)

        class_code = f"TEAM-TC-{number}"
        teaching_class = TeachingClass.objects.filter(term=term, class_code=class_code).first()
        if teaching_class is None:
            teaching_class = TeachingClass.objects.create(
                term=term,
                class_code=class_code,
                name=f"2026级示例教学班TEAM-{number}",
            )

        offering_code = f"TEAM-OFF-{number}"
        offering = CourseOffering.objects.filter(term=term, offering_code=offering_code).first()
        if offering is None:
            offering = CourseOffering.objects.create(
                term=term,
                offering_code=offering_code,
                course=course,
                teaching_class=teaching_class,
                target_hours=TARGET_HOURS,
                lead_teacher=li,
                required_equipment=["projector"],
            )

        bumped: set[int] = set()
        for teacher, role in (
            (li, AssignmentRole.LEAD),
            (zhang, AssignmentRole.TEACHER),
            (wang, AssignmentRole.TEACHER),
        ):
            _, created = TeachingAssignment.objects.get_or_create(
                offering=offering,
                teacher=teacher,
                effective_from=term.start_date,
                defaults={
                    "role": role,
                    "effective_until": term.end_date + timedelta(days=1),
                },
            )
            if created and teacher.pk not in bumped:
                teacher.assignment_scope_version += 1
                teacher.save(update_fields=["assignment_scope_version", "updated_at"])
                bumped.add(teacher.pk)

        room = rooms[index]
        slots = (
            (2, start_period, end_period, li, ALL_WEEKS),
            (4, start_period, end_period, zhang, ODD_WEEKS),
            (4, start_period, end_period, wang, EVEN_WEEKS),
        )
        for slot_index, (weekday, first_period, last_period, teacher, weeks) in enumerate(slots, start=1):
            rule_code = f"TEAM-R-{number}-{slot_index}"
            if ScheduleRule.objects.filter(offering=offering, rule_code=rule_code).exists():
                continue
            rule = ScheduleRule.objects.create(
                offering=offering,
                rule_code=rule_code,
                weekday=weekday,
                start_period=first_period,
                end_period=last_period,
                weeks=list(weeks),
                classroom=room,
                planned_hours=PER_SESSION_HOURS,
            )
            rule.teachers.set([teacher])

        for student in students[number]:
            Enrollment.objects.get_or_create(
                offering=offering,
                student=student,
                effective_from=term.start_date,
                defaults={"effective_until": term.end_date + timedelta(days=1)},
            )
    print("轮讲课程：3 门课程/教学班/开课已就绪，每门周二李老师、周四张/王按单双周。")


def generate_missing_sessions(term: Term) -> None:
    with transaction.atomic():
        result = generate_sessions_for_term(term)
    print(
        f"课次生成：新增 {result.created} 个、原有未变 {result.unchanged} 个、"
        f"保留现状的差异 {result.mismatched_count} 个。"
    )
    if result.mismatched_count:
        print("提示：已有课次与规则展开不同（可能由演示者调课造成），已保留现状，未覆盖。")


def main() -> None:
    school = School.objects.filter(code=SCHOOL_CODE).first()
    if school is None:
        fail("未找到演示学校，请先运行 seed_demo.py。")
    term = Term.objects.filter(school=school, code=TERM_CODE).first()
    if term is None:
        fail(f"未找到学期 {TERM_CODE}，请先运行 seed_demo.py。")

    teachers = ensure_teachers(school)
    ensure_legacy_scope(school, term, teachers)
    students = ensure_students(school)
    rooms = choose_rooms()
    ensure_course_data(school, term, teachers, students, rooms)
    generate_missing_sessions(term)

    print(
        {
            "school": school.code,
            "teachers": TeacherProfile.objects.filter(school=school).count(),
            "classrooms": Classroom.objects.filter(school=school).count(),
            "courses": Course.objects.filter(school=school).count(),
            "offerings": CourseOffering.objects.filter(term=term).count(),
            "sessions": ClassSession.objects.filter(offering__term=term).count(),
            "checked_at": timezone.now().isoformat(),
        }
    )


try:
    main()
except Exception as exc:  # noqa: BLE001 - seed 失败必须让调用方看到
    print(f"SEED_TEAM_FAILED: {exc}", file=sys.stderr)
    raise
