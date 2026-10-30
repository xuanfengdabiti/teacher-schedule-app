"""本地演示：李/张/王三位老师的整周补充课程（在 seed_demo_team.py 之后执行）。

只造演示数据，不新增功能：
- 每位老师新增 4 门独立虚构课程，每门 25 名独立学生、80 学时（每节 40 分钟）、20 周；
- 每门每周 2 次、每次 2 节，覆盖周一到周五；原有周二/周四轮讲 1–6 节完全保留；
- 固定使用原轮讲课程的三间教室（Mon/Wed/Fri 与 Tue/Thu 7–8 节原本空闲）；
- 全部 ``TEAMFILL-`` 稳定编码 + get_or_create，重复执行只补缺失；
- 课次仍只通过 ``generate_sessions_for_term`` 生成，不直接写 ClassSession。

执行前会核对教师/教室在目标时段是否已有正式课次；若用户已改动造成冲突，
改选同一时段空闲的现有教室，不修改任何已有课次。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal

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
    TeachingAssignment,
    TeachingClass,
    Term,
)
from apps.classrooms.models import Classroom
from apps.scheduling.models import ClassSession, ScheduleRule
from apps.scheduling.services import generate_sessions_for_term

SCHOOL_CODE = "DEMO-SCHOOL"
TERM_CODE = "2026-autumn"

# 每位老师 4 门课：字母、课程名、两段 (weekday, start_period, end_period)。
# weekday 1=周一 … 7=周日（与 ScheduleRule 定义一致）。
TEACHER_COURSES = {
    "0011": (
        ("A", "刑事证据审查实务（示例）", ((1, 1, 2), (3, 3, 4))),
        ("B", "侦查讯问策略（示例）", ((1, 5, 6), (5, 1, 2))),
        ("C", "刑事案件事实认定（示例）", ((3, 5, 6), (5, 3, 4))),
        ("D", "证据规则比较研究（示例）", ((2, 7, 8), (4, 7, 8))),
    ),
    "0012": (
        ("A", "刑事诉讼程序专题（示例）", ((1, 1, 2), (3, 3, 4))),
        ("B", "庭审举证质证实务（示例）", ((1, 5, 6), (5, 1, 2))),
        ("C", "刑事辩护技能（示例）", ((3, 5, 6), (5, 3, 4))),
        ("D", "司法文书写作（示例）", ((2, 7, 8), (4, 7, 8))),
    ),
    "0013": (
        ("A", "犯罪心理画像（示例）", ((1, 1, 2), (3, 3, 4))),
        ("B", "讯问心理与供述分析（示例）", ((1, 5, 6), (5, 1, 2))),
        ("C", "未成年人犯罪研究（示例）", ((3, 5, 6), (5, 3, 4))),
        ("D", "矫正心理学（示例）", ((2, 7, 8), (4, 7, 8))),
    ),
}

# 原轮讲课程使用的三间教室：李/张/王按 TEAM-C-001/002/003 的规则取。
TEAM_RULE_PREFIX = "TEAM-R-"
FILL_RULE_PREFIX = "TEAMFILL-R-"
ALL_WEEKS = tuple(range(1, 21))
PER_SESSION_HOURS = Decimal("2.0")
TARGET_HOURS = Decimal("80.0")
STUDENTS_PER_COURSE = 25


def fail(message: str) -> None:
    raise RuntimeError(message)


def team_rooms() -> dict[str, Classroom]:
    """按原 TEAM-C-001/002/003 规则读取李/张/王各自的轮讲教室。"""
    result: dict[str, Classroom] = {}
    for teacher_no, course_number in (("0011", "001"), ("0012", "002"), ("0013", "003")):
        rule = (
            ScheduleRule.objects.filter(rule_code=f"{TEAM_RULE_PREFIX}{course_number}-1")
            .select_related("classroom")
            .first()
        )
        if rule is None:
            fail(f"未找到轮讲规则 {TEAM_RULE_PREFIX}{course_number}-1，请先运行 seed_demo_team.py。")
        result[teacher_no] = rule.classroom
    return result


def _weekday_of(session: ClassSession, term: Term) -> int:
    return session.start_at.astimezone(term.tzinfo).isoweekday()


def slot_conflict(term: Term, *, teacher: TeacherProfile, room: Classroom, weekday: int, start: int, end: int):
    """返回目标时段内已有正式课次或本批已建规则（教师或教室任一冲突）。"""
    sessions = ClassSession.objects.filter(
        offering__term=term,
        start_period__lte=end,
        end_period__gte=start,
    ).prefetch_related("teachers")
    for session in sessions:
        if _weekday_of(session, term) != weekday:
            continue
        if session.classroom_id == room.id or teacher.id in {link.id for link in session.teachers.all()}:
            return session
    rules = ScheduleRule.objects.filter(
        offering__term=term,
        weekday=weekday,
        start_period__lte=end,
        end_period__gte=start,
    ).prefetch_related("teachers")
    for rule in rules:
        if rule.classroom_id == room.id or teacher.id in {item.id for item in rule.teachers.all()}:
            return rule
    return None


def pick_room(term: Term, *, teacher: TeacherProfile, preferred: Classroom, weekday: int, start: int, end: int):
    """优先用轮讲教室；若该时段已占用，改选同校同校区空闲教室，不动已有课次。"""
    if slot_conflict(term, teacher=teacher, room=preferred, weekday=weekday, start=start, end=end) is None:
        return preferred
    candidates = Classroom.objects.filter(school=teacher.school, is_active=True).exclude(pk=preferred.pk)
    for room in candidates.order_by("campus", "building", "room_no"):
        if slot_conflict(term, teacher=teacher, room=room, weekday=weekday, start=start, end=end) is None:
            print(
                f"提示：{teacher.teacher_no} 在周{weekday} {start}-{end} 节原教室 {preferred.classroom_code} 已占用，"
                f"改用 {room.classroom_code}。"
            )
            return room
    fail(f"{teacher.teacher_no} 在周{weekday} {start}-{end} 节找不到可用教室。")


def ensure_students(school: School, teacher_no: str, letter: str) -> list[Student]:
    numbers = [f"TEAMFILL-{teacher_no}-{letter}-{index:03d}" for index in range(1, STUDENTS_PER_COURSE + 1)]
    existing = {
        student.student_no: student for student in Student.objects.filter(school=school, student_no__in=numbers)
    }
    created = 0
    for number in numbers:
        if number in existing:
            continue
        student = Student.objects.create(school=school, student_no=number)
        existing[number] = student
        created += 1
    if created:
        print(f"学生：{teacher_no}-{letter} 新建 {created} 名。")
    return [existing[number] for number in numbers]


def ensure_course(
    school: School,
    term: Term,
    teacher: TeacherProfile,
    letter: str,
    name: str,
    slots,
    preferred_room: Classroom,
) -> None:
    code = f"TEAMFILL-{teacher.teacher_no}-{letter}"
    course_code = f"TEAMFILL-C-{teacher.teacher_no}-{letter}"
    course = Course.objects.filter(school=school, course_code=course_code).first()
    if course is None:
        course = Course.objects.create(school=school, course_code=course_code, name=name)

    class_code = f"TEAMFILL-TC-{teacher.teacher_no}-{letter}"
    teaching_class = TeachingClass.objects.filter(term=term, class_code=class_code).first()
    if teaching_class is None:
        teaching_class = TeachingClass.objects.create(
            term=term,
            class_code=class_code,
            name=f"2026级示例教学班{code}",
        )

    offering_code = f"TEAMFILL-OFF-{teacher.teacher_no}-{letter}"
    offering = CourseOffering.objects.filter(term=term, offering_code=offering_code).first()
    if offering is None:
        offering = CourseOffering.objects.create(
            term=term,
            offering_code=offering_code,
            course=course,
            teaching_class=teaching_class,
            target_hours=TARGET_HOURS,
            lead_teacher=teacher,
            required_equipment=["projector"],
        )

    _, assignment_created = TeachingAssignment.objects.get_or_create(
        offering=offering,
        teacher=teacher,
        effective_from=term.start_date,
        defaults={
            "role": AssignmentRole.LEAD,
            "effective_until": term.end_date + timedelta(days=1),
        },
    )
    if assignment_created:
        teacher.assignment_scope_version += 1
        teacher.save(update_fields=["assignment_scope_version", "updated_at"])

    for index, (weekday, start_period, end_period) in enumerate(slots, start=1):
        rule_code = f"{FILL_RULE_PREFIX}{teacher.teacher_no}-{letter}-{index}"
        if ScheduleRule.objects.filter(offering=offering, rule_code=rule_code).exists():
            continue
        room = pick_room(
            term,
            teacher=teacher,
            preferred=preferred_room,
            weekday=weekday,
            start=start_period,
            end=end_period,
        )
        rule = ScheduleRule.objects.create(
            offering=offering,
            rule_code=rule_code,
            weekday=weekday,
            start_period=start_period,
            end_period=end_period,
            weeks=list(ALL_WEEKS),
            classroom=room,
            planned_hours=PER_SESSION_HOURS,
        )
        rule.teachers.set([teacher])
        print(f"规则：{rule_code} 周{weekday} {start_period}-{end_period} 节 {room.classroom_code}")

    for student in ensure_students(school, teacher.teacher_no, letter):
        Enrollment.objects.get_or_create(
            offering=offering,
            student=student,
            effective_from=term.start_date,
            defaults={"effective_until": term.end_date + timedelta(days=1)},
        )


def generate_missing_sessions(term: Term) -> None:
    with transaction.atomic():
        result = generate_sessions_for_term(term)
    print(f"课次生成：新增 {result.created}、原有未变 {result.unchanged}、保留差异 {result.mismatched_count}。")
    if result.mismatched_count:
        print("提示：已有课次与规则展开不同（可能由演示者调课），已保留现状。")


def main() -> None:
    school = School.objects.filter(code=SCHOOL_CODE).first()
    if school is None:
        fail("未找到演示学校，请先运行 seed_demo.py。")
    term = Term.objects.filter(school=school, code=TERM_CODE).first()
    if term is None:
        fail(f"未找到学期 {TERM_CODE}，请先运行 seed_demo.py。")

    rooms = team_rooms()
    teachers = {
        teacher_no: TeacherProfile.objects.filter(school=school, teacher_no=teacher_no).first()
        for teacher_no in TEACHER_COURSES
    }
    for teacher_no, teacher in teachers.items():
        if teacher is None:
            fail(f"未找到教师 {teacher_no}，请先运行 seed_demo_team.py。")

    for teacher_no, courses in TEACHER_COURSES.items():
        teacher = teachers[teacher_no]
        for letter, name, slots in courses:
            ensure_course(school, term, teacher, letter, name, slots, rooms[teacher_no])

    generate_missing_sessions(term)

    print(
        {
            "school": school.code,
            "teachers": TeacherProfile.objects.filter(school=school).count(),
            "courses": Course.objects.filter(school=school).count(),
            "students": Student.objects.filter(school=school).count(),
            "offerings": CourseOffering.objects.filter(term=term).count(),
            "sessions": ClassSession.objects.filter(offering__term=term).count(),
            "checked_at": timezone.now().isoformat(),
        }
    )


try:
    main()
except Exception as exc:  # noqa: BLE001 - seed 失败必须让调用方看到
    print(f"SEED_TEAM_EXTRA_FAILED: {exc}", file=sys.stderr)
    raise
