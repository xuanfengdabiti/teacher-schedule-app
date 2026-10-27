"""本地演示扩充数据（在 seed_demo.py 之后执行）。

只造虚构演示数据，不新增功能：
- 把原有的 3 个示例教室改到用户指定的校区/楼宇（保留 id 与 classroom_code）；
- 生成 27 间新教室，共 30 间，全部落在团河/木犀两个校区的历警楼、慧警楼、高警察楼；
- 新增 20 门示例课程、8 位示例教师（0003–0010）、500 名虚构学生，共 22 门课、约 840 课次；
- 课程/教室/规则均使用 ``EXTRA-`` 稳定编码，重复执行只保留已有数据，不更新、不覆盖；
- 应用研究生节次时间表（每节 40 分钟），并把当前 scheduled 课次改到新钟点。

课次生成只调用现有 ``generate_sessions_for_term``；既有课次的钟点修正走
``apply_import_session_batch``，不直接写 ClassSession。
"""

from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
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
    Period,
    School,
    Student,
    TeacherProfile,
    TeacherStatus,
    TeachingAssignment,
    TeachingClass,
    Term,
)
from apps.classrooms.models import Classroom
from apps.imports.models import ImportBatch, ImportStatus
from apps.scheduling.import_changes import (
    IMPORT_UPDATE,
    ImportSessionTarget,
    ImportSessionWrite,
    apply_import_session_batch,
)
from apps.scheduling.models import ClassSession, ScheduleRule, SchoolSyncState, SessionStatus
from apps.scheduling.services import generate_sessions_for_term

SCHOOL_CODE = "DEMO-SCHOOL"
TERM_CODE = "2026-autumn"
TEACHER_PASSWORD = "TeacherDemo2026!"
STAFF_EMAIL = "academic-staff@example.invalid"
PERIOD_SOURCE_SYSTEM = "demo_periods_v2"
User = get_user_model()

# 用户指定的两个校区、三栋楼与三位数房号。
CAMPUSES = ("团河校区", "木犀校区")
BUILDINGS = ("历警楼", "慧警楼", "高警察楼")
CAPACITIES = (30, 40, 60, 80, 120)

# 原 3 个教室：保留 id 和 classroom_code，只改展示地点。
LEGACY_LOCATIONS = {
    "A301": ("团河校区", "历警楼", "301"),
    "A302": ("团河校区", "历警楼", "302"),
    "B201": ("木犀校区", "慧警楼", "201"),
}

# 新增 27 间（避开上面 3 个已占用的同校区同楼同房）。
NEW_ROOMS = {
    ("团河校区", "历警楼"): ("101", "102", "201", "202"),
    ("团河校区", "慧警楼"): ("101", "102", "201", "301", "302"),
    ("团河校区", "高警察楼"): ("101", "102", "201", "301", "302"),
    ("木犀校区", "历警楼"): ("101", "102", "201", "301", "302"),
    ("木犀校区", "慧警楼"): ("101", "102", "301", "302"),
    ("木犀校区", "高警察楼"): ("101", "102", "201", "301"),
}

# 新教师 0003–0010，每人一门课。
NEW_TEACHERS = (
    ("0003", "示例教师丙"),
    ("0004", "示例教师丁"),
    ("0005", "示例教师戊"),
    ("0006", "示例教师己"),
    ("0007", "示例教师庚"),
    ("0008", "示例教师辛"),
    ("0009", "示例教师壬"),
    ("0010", "示例教师癸"),
)

# 新课程：编号、名称、授课教师工号、每周两次的 (weekday, start_period, end_period)。
# 0001 与 0002 各 6 门，周一至周五每天 2–3 次课；新规则避开周一 1–4 节，
# 0002 另避开周三 3–4 节（10/28 已有会议）。其余教师每人一门。
NEW_COURSES = (
    ("1001", "刑事诉讼法（示例）", "0001", ((2, 1, 2), (4, 3, 4))),
    ("1002", "证据法（示例）", "0001", ((2, 3, 4), (4, 7, 8))),
    ("1003", "犯罪心理学（示例）", "0001", ((2, 5, 6), (5, 1, 2))),
    ("1004", "网络安全（示例）", "0001", ((1, 5, 6), (3, 1, 2))),
    ("1005", "电子数据取证（示例）", "0001", ((3, 5, 6), (5, 5, 6))),
    ("1006", "警务英语（示例）", "0001", ((4, 1, 2), (5, 7, 8))),
    ("1007", "公安情报（示例）", "0002", ((2, 1, 2), (4, 3, 4))),
    ("1008", "法庭科学（示例）", "0002", ((2, 3, 4), (4, 7, 8))),
    ("1009", "公共安全管理（示例）", "0002", ((2, 5, 6), (5, 1, 2))),
    ("1010", "警务沟通（示例）", "0002", ((1, 5, 6), (3, 1, 2))),
    ("1011", "调查方法（示例）", "0002", ((3, 5, 6), (5, 5, 6))),
    ("1012", "法律文书（示例）", "0002", ((1, 7, 8), (4, 1, 2))),
    ("1013", "统计方法（示例）", "0003", ((2, 1, 2), (4, 1, 2))),
    ("1014", "信息检索（示例）", "0004", ((2, 3, 4), (4, 3, 4))),
    ("1015", "应急处置（示例）", "0005", ((2, 5, 6), (4, 5, 6))),
    ("1016", "侦查思维（示例）", "0006", ((2, 7, 8), (4, 7, 8))),
    ("1017", "现场摄影（示例）", "0007", ((3, 1, 2), (5, 1, 2))),
    ("1018", "社会调查（示例）", "0008", ((3, 3, 4), (5, 3, 4))),
    ("1019", "人工智能（示例）", "0009", ((3, 5, 6), (5, 5, 6))),
    ("1020", "视频图像分析（示例）", "0010", ((3, 7, 8), (5, 7, 8))),
)

ALL_WEEKS = tuple(range(1, 21))
PER_SESSION_HOURS = Decimal("2.0")
TARGET_HOURS = Decimal("80.0")
STUDENTS_PER_COURSE = 25

# 研究生节次：每节 40 分钟，上午 2/3 节之间 20 分钟，其余 10 分钟。
PERIOD_MINUTES = 40
NEW_PERIODS = (
    (1, time(8, 10), time(8, 50), "morning"),
    (2, time(9, 0), time(9, 40), "morning"),
    (3, time(10, 0), time(10, 40), "morning"),
    (4, time(10, 50), time(11, 30), "morning"),
    (5, time(14, 0), time(14, 40), "afternoon"),
    (6, time(14, 50), time(15, 30), "afternoon"),
    (7, time(15, 40), time(16, 20), "afternoon"),
    (8, time(16, 30), time(17, 10), "afternoon"),
)


def fail(message: str) -> None:
    raise RuntimeError(message)


def ensure_periods(term: Term) -> None:
    """应用研究生节次时间表；Term 与 demo 开课每学时分钟数同步为 40。"""
    created = 0
    updated = 0
    for number, start_time, end_time, block_code in NEW_PERIODS:
        period, was_created = Period.objects.get_or_create(
            term=term,
            period_number=number,
            defaults={"start_time": start_time, "end_time": end_time, "block_code": block_code},
        )
        if was_created:
            created += 1
            continue
        if (period.start_time, period.end_time, period.block_code) != (start_time, end_time, block_code):
            period.start_time = start_time
            period.end_time = end_time
            period.block_code = block_code
            period.save(update_fields=["start_time", "end_time", "block_code", "updated_at"])
            updated += 1
    if term.hour_minutes != PERIOD_MINUTES:
        term.hour_minutes = PERIOD_MINUTES
        term.save(update_fields=["hour_minutes", "updated_at"])
    offerings = CourseOffering.objects.filter(term=term).exclude(minutes_per_hour=PERIOD_MINUTES)
    offerings_updated = offerings.update(minutes_per_hour=PERIOD_MINUTES, updated_at=timezone.now())
    print(
        f"节次表：新建 {created} 节、更新 {updated} 节；Term.hour_minutes={PERIOD_MINUTES}，"
        f"{offerings_updated} 个开课的 minutes_per_hour 同步为 {PERIOD_MINUTES}。"
    )


def _retime_targets(term: Term) -> list[ImportSessionWrite]:
    """按新节次钟点算 scheduled 课次的目标；只改钟点，其余字段保持原值。"""
    periods = {period.period_number: period for period in Period.objects.filter(term=term)}
    sessions = (
        ClassSession.objects.filter(offering__term=term, status=SessionStatus.SCHEDULED)
        .select_related("offering__term")
        .prefetch_related("teachers")
        .order_by("start_at", "id")
    )
    writes: list[ImportSessionWrite] = []
    for session in sessions:
        start_period = periods.get(session.start_period)
        end_period = periods.get(session.end_period)
        if start_period is None or end_period is None:
            continue
        local_day = session.start_at.astimezone(term.tzinfo).date()
        start_at = datetime.combine(local_day, start_period.start_time, tzinfo=term.tzinfo)
        end_at = datetime.combine(local_day, end_period.end_time, tzinfo=term.tzinfo)
        if session.start_at == start_at and session.end_at == end_at:
            continue
        writes.append(
            ImportSessionWrite(
                action=IMPORT_UPDATE,
                session_id=session.id,
                change_ref="演示节次时间表更新（每节 40 分钟）",
                target=ImportSessionTarget(
                    term_code=term.code,
                    offering_id=session.offering_id,
                    rule_id=session.rule_id,
                    origin_teaching_date=session.origin_teaching_date,
                    origin_period=session.origin_period,
                    start_at=start_at,
                    end_at=end_at,
                    start_period=session.start_period,
                    end_period=session.end_period,
                    classroom_id=session.classroom_id,
                    teacher_ids=tuple(sorted((teacher.id for teacher in session.teachers.all()), key=str)),
                    planned_hours=session.planned_hours,
                    status=session.status,
                    status_reason=session.status_reason,
                    actual_hours=session.actual_hours,
                ),
            )
        )
    return writes


def retime_scheduled_sessions(term: Term) -> None:
    """把既有 scheduled 课次改到新钟点，走统一批量写入入口并留痕。"""
    writes = _retime_targets(term)
    if not writes:
        print("课次钟点：已是最新，无需修正（不新建批次、不发通知）。")
        return
    staff = User.objects.filter(email=STAFF_EMAIL).first()
    if staff is None:
        fail(f"未找到演示教务账号 {STAFF_EMAIL}，请先运行 seed_demo.py。")
    now = timezone.now()
    with transaction.atomic():
        SchoolSyncState.objects.select_for_update().get_or_create(school_id=term.school_id)
        locked_term = Term.objects.select_for_update().get(pk=term.pk)
        batch = ImportBatch.objects.create(
            school=term.school,
            source_system=PERIOD_SOURCE_SYSTEM,
            term=locked_term,
            mode="merge",
            source_updated_at=now,
            uploaded_by=staff,
            file_names=["(demo period retime)"],
        )
        result = apply_import_session_batch(
            writes,
            batch=batch,
            actor=staff,
            now=now,
            terms_by_code={term.code: locked_term},
        )
        batch.status = ImportStatus.COMMITTED
        batch.committed_by = staff
        batch.committed_at = now
        batch.change_summary = {"period_retime": {"updated": result.sessions_updated}}
        batch.save(update_fields=["status", "committed_by", "committed_at", "change_summary", "updated_at"])
    print(f"课次钟点：按新节次表修正 {result.sessions_updated} 个 scheduled 课次（批次 {batch.pk}）。")


def ensure_legacy_classrooms(school: School) -> None:
    for code, (campus, building, room_no) in LEGACY_LOCATIONS.items():
        classroom = Classroom.objects.filter(school=school, classroom_code=code).first()
        if classroom is None:
            fail(f"未找到示例教室 {code}，请先运行 seed_demo.py。")
        classroom.campus = campus
        classroom.building = building
        classroom.room_no = room_no
        classroom.save(update_fields=["campus", "building", "room_no", "updated_at"])


def ensure_classrooms(school: School) -> dict[str, Classroom]:
    """共 30 间：旧 3 间改地点，新 27 间按 校区/楼宇/房号 生成。"""
    ensure_legacy_classrooms(school)
    created = 0
    for (campus, building), room_numbers in NEW_ROOMS.items():
        for room_no in room_numbers:
            code = f"{campus}-{building}{room_no}"
            if Classroom.objects.filter(school=school, classroom_code=code).exists():
                continue
            index = len(NEW_ROOMS) * int(room_no[0]) + len(building) + len(campus)
            Classroom.objects.create(
                school=school,
                classroom_code=code,
                campus=campus,
                building=building,
                room_no=room_no,
                capacity=CAPACITIES[index % len(CAPACITIES)],
                equipment=["projector", "computer"] if index % 2 else ["projector"],
            )
            created += 1
    print(f"教室：新建 {created} 间；3 个示例教室已定位到指定校区/楼宇。")
    return {classroom.classroom_code: classroom for classroom in Classroom.objects.filter(school=school)}


def ensure_teachers(school: School) -> dict[str, TeacherProfile]:
    created = 0
    for teacher_no, formal_name in NEW_TEACHERS:
        teacher = TeacherProfile.objects.filter(school=school, teacher_no=teacher_no).first()
        if teacher is None:
            teacher = TeacherProfile.objects.create(
                school=school,
                teacher_no=teacher_no,
                formal_name=formal_name,
                department="虚构侦查教研室",
                email=f"t{teacher_no}@example.invalid",
            )
            created += 1
        if teacher.user_id is None:
            user, user_created = User.objects.get_or_create(email=teacher.email)
            if user_created:
                user.set_password(TEACHER_PASSWORD)
                user.save()
            EmailAddress.objects.update_or_create(
                user=user,
                email=user.email,
                defaults={"verified": True, "primary": True},
            )
            teacher.user = user
        if teacher.teacher_status != TeacherStatus.ACTIVE:
            teacher.teacher_status = TeacherStatus.ACTIVE
        teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    print(f"新教师：新建 {created} 位（0003–0010）。")
    return {teacher.teacher_no: teacher for teacher in TeacherProfile.objects.filter(school=school)}


def ensure_students(school: School) -> dict[str, list[Student]]:
    """每门新课程 25 名独立虚构学生：EXTRA-<课程号>-001…025。"""
    result: dict[str, list[Student]] = {}
    created = 0
    for course_number, _, _, _ in NEW_COURSES:
        numbers = [f"EXTRA-{course_number}-{index:03d}" for index in range(1, STUDENTS_PER_COURSE + 1)]
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
    print(f"虚构学生：新建 {created} 名（每门新课程 25 名，互不共用）。")
    return result


def ensure_course_data(
    school: School,
    term: Term,
    teachers: dict[str, TeacherProfile],
    students: dict[str, list[Student]],
    classrooms: dict[str, Classroom],
) -> tuple[int, int]:
    """课程、教学班、开课、任课、选课和规则；已有 code 一律保留。"""
    courses_created = 0
    rules_created = 0
    # 新课程只使用新教室，每门课固定一间，互不共享。
    new_room_codes = sorted(code for code in classrooms if code not in LEGACY_LOCATIONS)
    for index, (number, name, teacher_no, slots) in enumerate(NEW_COURSES):
        teacher = teachers[teacher_no]
        course_code = f"EXTRA-C-{number}"
        course = Course.objects.filter(school=school, course_code=course_code).first()
        if course is None:
            course = Course.objects.create(school=school, course_code=course_code, name=name)
            courses_created += 1

        class_code = f"EXTRA-TC-{number}"
        teaching_class = TeachingClass.objects.filter(term=term, class_code=class_code).first()
        if teaching_class is None:
            teaching_class = TeachingClass.objects.create(
                term=term,
                class_code=class_code,
                name=f"2026级示例教学班{number}",
            )

        offering_code = f"EXTRA-OFF-{number}"
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

        room = classrooms[new_room_codes[index % len(new_room_codes)]]
        for slot_index, (weekday, start_period, end_period) in enumerate(slots, start=1):
            rule_code = f"EXTRA-R-{number}-{slot_index}"
            if ScheduleRule.objects.filter(offering=offering, rule_code=rule_code).exists():
                continue
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
            rules_created += 1

        for student in students[number]:
            Enrollment.objects.get_or_create(
                offering=offering,
                student=student,
                effective_from=term.start_date,
                defaults={"effective_until": term.end_date + timedelta(days=1)},
            )
    print(f"新课程：新建 {courses_created} 门、每门 2 条规则，共新建规则 {rules_created} 条。")
    return courses_created, rules_created


def generate_missing_sessions(term: Term) -> None:
    """只补缺失课次；已有用户调课造成的差异只提示，不失败、不改动。"""
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

    ensure_periods(term)
    classrooms = ensure_classrooms(school)
    teachers = ensure_teachers(school)
    students = ensure_students(school)
    ensure_course_data(school, term, teachers, students, classrooms)
    retime_scheduled_sessions(term)
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
    print(f"SEED_EXTRA_FAILED: {exc}", file=sys.stderr)
    raise
