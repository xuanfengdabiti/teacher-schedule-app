"""Classroom, course, teaching-class and offering projections."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.academics.models import (
    Course,
    CourseOffering,
    TeacherProfile,
    TeachingAssignment,
    TeachingClass,
)
from apps.classrooms.models import Classroom
from apps.imports.parsing import ParsedRow
from apps.imports.projection.base import (
    ENTITY_CLASSROOM,
    ENTITY_COURSE,
    ENTITY_OFFERING,
    ENTITY_TEACHING_CLASS,
    ERROR,
    AssignmentChange,
    BuildContext,
    DeferredAssignmentCheck,
    FieldDifference,
    diff_fields,
    external_id_for_teaching_class,
    issue_for,
    resolve_object,
    teacher_ref,
    term_ref,
)
from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching.models import TeachingPlan


def build_classrooms(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("classrooms"):
        code = row.values.get("classroom_code")
        if not code or code in ctx.graph.classrooms:
            continue
        data = Classroom(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            classroom_code=code,
            campus=row.values.get("campus") or "",
            building=row.values.get("building") or "",
            room_no=row.values.get("room_no") or "",
            capacity=row.values.get("capacity") or 0,
            equipment=sorted(row.values.get("equipment") or []),
            is_active=bool(row.values.get("is_active")),
        )
        natural = Classroom.objects.filter(school_id=ctx.batch.school_id, classroom_code=code).first()
        identity = ctx.existing.identity(ENTITY_CLASSROOM, code)
        target = ctx.existing.target(ENTITY_CLASSROOM, code) if identity else None
        updates = []
        if isinstance(target, Classroom):
            updates = diff_fields(
                target,
                {
                    "campus": data.campus,
                    "building": data.building,
                    "room_no": data.room_no,
                    "capacity": data.capacity,
                    "equipment": sorted(data.equipment or []),
                    "is_active": data.is_active,
                },
                ["campus", "building", "room_no", "capacity", "equipment", "is_active"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_CLASSROOM,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            updates=updates,
        )
        if isinstance(target, Classroom):
            conflict = (
                Classroom.objects.filter(
                    school_id=ctx.batch.school_id,
                    campus=data.campus,
                    building=data.building,
                    room_no=data.room_no,
                )
                .exclude(pk=target.pk)
                .exists()
            )
            if conflict:
                obj.state = ERROR
                ctx.add_issue(
                    issue_for(
                        row,
                        "room_no",
                        "ROOM_NO_CONFLICT",
                        "校区、楼宇和房间号组合已被另一间教室占用；请修正教室编号或房间信息。",
                    )
                )
        ctx.graph.classrooms[code] = obj


def build_courses(ctx: BuildContext) -> None:
    grouped: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in ctx.parsed.rows_for("offerings"):
        code = row.values.get("course_code")
        if code:
            grouped[code].append(row)
    for code, rows in grouped.items():
        first = rows[0]
        data = Course(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            course_code=code,
            name=first.values.get("course_name") or "",
        )
        natural = Course.objects.filter(school_id=ctx.batch.school_id, course_code=code).first()
        identity = ctx.existing.identity(ENTITY_COURSE, code)
        target = ctx.existing.target(ENTITY_COURSE, code) if identity else None
        updates = []
        if isinstance(target, Course):
            updates = diff_fields(target, {"name": data.name}, ["name"])
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_COURSE,
            external_id=code,
            data=data,
            rows=rows,
            natural=natural,
            updates=updates,
        )
        ctx.graph.courses[code] = obj


def build_teaching_classes(ctx: BuildContext) -> None:
    grouped: dict[tuple[str, str], list[ParsedRow]] = defaultdict(list)
    for row in ctx.parsed.rows_for("offerings"):
        term_code = row.values.get("term_code")
        class_code = row.values.get("class_code")
        if term_code and class_code:
            grouped[(term_code, class_code)].append(row)
    for (term_code, class_code), rows in grouped.items():
        term = term_ref(ctx, term_code)
        if term is None:
            ctx.add_issues(
                [
                    issue_for(
                        row,
                        "term_code",
                        "FK_NOT_FOUND",
                        f"学期 {term_code} 不存在于本批次或此前同来源导入的数据中。",
                    )
                    for row in rows
                ]
            )
            continue
        first = rows[0]
        data = TeachingClass(
            id=uuid.uuid4(),
            term_id=term.pk,
            class_code=class_code,
            name=first.values.get("class_name") or "",
        )
        natural = TeachingClass.objects.filter(term_id=term.pk, class_code=class_code).first() if term.pk else None
        external_id = external_id_for_teaching_class(term_code, class_code)
        identity = ctx.existing.identity(ENTITY_TEACHING_CLASS, external_id)
        target = ctx.existing.target(ENTITY_TEACHING_CLASS, external_id) if identity else None
        updates = []
        if isinstance(target, TeachingClass):
            updates = diff_fields(target, {"name": data.name}, ["name"])
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_TEACHING_CLASS,
            external_id=external_id,
            data=data,
            rows=rows,
            natural=natural,
            updates=updates,
        )
        ctx.graph.teaching_classes[(term_code, class_code)] = obj


def _active_assignments(offering: CourseOffering, school_date: date, term) -> list[TeachingAssignment]:
    assignments = list(
        TeachingAssignment.objects.filter(offering_id=offering.pk).select_related("teacher").order_by("effective_from")
    )
    full_term_end = term.end_date + timedelta(days=1)
    return [
        assignment
        for assignment in assignments
        if assignment.effective_until is None
        or assignment.effective_until > school_date
        or assignment.effective_until == full_term_end
    ]


def _has_future_scheduled_session(offering: CourseOffering, teacher_id, *, school_date: date, term) -> bool:
    tzinfo = ZoneInfo(term.timezone)
    day_start = datetime.combine(school_date, time.min, tzinfo=tzinfo)
    return ClassSession.objects.filter(
        offering_id=offering.pk,
        session_teachers__teacher_id=teacher_id,
        status=SessionStatus.SCHEDULED,
        start_at__gte=day_start,
    ).exists()


def _plan_assignment_changes(
    ctx: BuildContext,
    *,
    row: ParsedRow,
    offering: CourseOffering,
    term,
    teacher_codes: list[str],
    lead_code: str,
    school_date: date,
) -> list[AssignmentChange]:
    desired: dict[str, TeacherProfile] = {}
    for code in teacher_codes:
        teacher = teacher_ref(ctx, code)
        if teacher is not None:
            desired[code] = teacher
    if len(desired) != len(set(teacher_codes)) or lead_code not in desired:
        return []

    active = _active_assignments(offering, school_date, term)
    active_by_teacher: dict = defaultdict(list)
    for assignment in active:
        active_by_teacher[assignment.teacher_id].append(assignment)
    historical = list(TeachingAssignment.objects.filter(offering_id=offering.pk).order_by("effective_from"))
    historical_by_teacher: dict = defaultdict(list)
    for assignment in historical:
        historical_by_teacher[assignment.teacher_id].append(assignment)

    changes: list[AssignmentChange] = []
    for code, teacher in desired.items():
        desired_role = "lead" if code == lead_code else "teacher"
        current = active_by_teacher.get(teacher.pk, [])
        if current:
            for assignment in current:
                if assignment.role != desired_role:
                    changes.append(
                        AssignmentChange(
                            action="role_changed",
                            teacher_code=code,
                            teacher_id=teacher.pk,
                            offering_id=offering.pk,
                            offering_code=offering.offering_code,
                            old_role=assignment.role,
                            new_role=desired_role,
                            assignment_id=assignment.pk,
                        )
                    )
            continue

        previous = historical_by_teacher.get(teacher.pk, [])
        effective_from = term.start_date
        if previous:
            # Re-adding a teacher after an earlier end starts a new visible
            # interval; reopen history only when today is a valid start.
            effective_from = max(term.start_date, school_date)
        changes.append(
            AssignmentChange(
                action="added",
                teacher_code=code,
                teacher_id=teacher.pk,
                offering_id=offering.pk,
                offering_code=offering.offering_code,
                new_role=desired_role,
                effective_from=effective_from,
                effective_until=term.end_date + timedelta(days=1),
            )
        )

    desired_ids = {teacher.pk for teacher in desired.values()}
    for teacher_id, assignments in active_by_teacher.items():
        if teacher_id in desired_ids:
            continue
        teacher = assignments[0].teacher
        if _has_future_scheduled_session(offering, teacher_id, school_date=school_date, term=term):
            # The batch may also change the rule teachers and reassign every
            # one of these sessions.  Keep the planned end and let the
            # reconciliation stage reject the batch only if a future scheduled
            # target would still include this teacher.
            ctx.deferred_assignment_checks.append(
                DeferredAssignmentCheck(
                    teacher_id=teacher_id,
                    teacher_no=teacher.teacher_no,
                    offering_id=offering.pk,
                    row=row,
                )
            )
        for assignment in assignments:
            if assignment.effective_from >= school_date:
                # A future assignment has no history yet and cannot satisfy the
                # exclusive-end check with effective_until <= today.
                changes.append(
                    AssignmentChange(
                        action="ended",
                        teacher_code=teacher.teacher_no,
                        teacher_id=teacher_id,
                        offering_id=offering.pk,
                        offering_code=offering.offering_code,
                        old_role=assignment.role,
                        assignment_id=assignment.pk,
                        delete_unstarted=True,
                    )
                )
            else:
                changes.append(
                    AssignmentChange(
                        action="ended",
                        teacher_code=teacher.teacher_no,
                        teacher_id=teacher_id,
                        offering_id=offering.pk,
                        offering_code=offering.offering_code,
                        old_role=assignment.role,
                        assignment_id=assignment.pk,
                        effective_until=school_date,
                    )
                )
    return changes


def build_offerings(ctx: BuildContext, *, school_date: date) -> None:
    for row in ctx.parsed.rows_for("offerings"):
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        course_code = row.values.get("course_code")
        class_code = row.values.get("class_code")
        if not term_code or not offering_code or not course_code or not class_code:
            continue
        key = (term_code, offering_code)
        if key in ctx.graph.offerings:
            continue
        term = term_ref(ctx, term_code)
        course = ctx.graph.courses.get(course_code)
        teaching_class = ctx.graph.teaching_classes.get((term_code, class_code))
        if term is None:
            ctx.add_issue(
                issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在或编号无效。"),
            )
            continue
        if course is None:
            ctx.add_issue(issue_for(row, "course_code", "FK_NOT_FOUND", f"课程 {course_code} 无法建立。"))
            continue
        if teaching_class is None:
            ctx.add_issue(issue_for(row, "class_code", "FK_NOT_FOUND", f"教学班 {class_code} 无法建立。"))
            continue

        teacher_codes = list(row.values.get("teacher_codes") or [])
        teachers = {code: teacher_ref(ctx, code) for code in teacher_codes}
        missing_teachers = [code for code, teacher in teachers.items() if teacher is None]
        for code in missing_teachers:
            ctx.add_issue(
                issue_for(
                    row,
                    "teacher_codes",
                    "FK_NOT_FOUND",
                    f"教师 {code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
        lead_code = row.values.get("lead_teacher_code") or ""
        lead = teacher_ref(ctx, lead_code)
        if lead is None:
            ctx.add_issue(
                issue_for(
                    row,
                    "lead_teacher_code",
                    "FK_NOT_FOUND",
                    f"课程负责人 {lead_code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )

        data = CourseOffering(
            id=uuid.uuid4(),
            term_id=term.pk,
            offering_code=offering_code,
            course_id=course.data.pk,
            teaching_class_id=teaching_class.data.pk,
            target_hours=row.values.get("target_hours") or Decimal("0"),
            lead_teacher_id=lead.pk if lead is not None else None,
            minutes_per_hour=term.hour_minutes or 1,
            required_equipment=sorted(row.values.get("required_equipment") or []),
        )
        natural = (
            CourseOffering.objects.filter(term_id=term.pk, offering_code=offering_code).first() if term.pk else None
        )
        identity = ctx.existing.identity(ENTITY_OFFERING, offering_code)
        target = ctx.existing.target(ENTITY_OFFERING, offering_code) if identity else None
        unsupported = []
        updates = []
        assignment_changes: list[AssignmentChange] = []
        plan_owner_change = None
        if isinstance(target, CourseOffering):
            unsupported = diff_fields(
                target,
                {
                    "term_id": data.term_id,
                    "course_id": data.course_id,
                    "teaching_class_id": data.teaching_class_id,
                    "minutes_per_hour": data.minutes_per_hour,
                },
                ["term_id", "course_id", "teaching_class_id", "minutes_per_hour"],
            )
            updates = diff_fields(
                target,
                {
                    "target_hours": data.target_hours,
                    "required_equipment": sorted(data.required_equipment or []),
                },
                ["target_hours", "required_equipment"],
            )
            active = _active_assignments(target, school_date, term)
            actual_teacher_ids = {assignment.teacher_id for assignment in active}
            expected_teacher_ids = {teacher.pk for teacher in teachers.values() if teacher is not None}
            if expected_teacher_ids != actual_teacher_ids:
                updates.append(
                    FieldDifference(
                        field="teacher_codes",
                        expected="|".join(sorted(teacher_codes)),
                        actual="|".join(
                            sorted(
                                TeacherProfile.objects.filter(pk__in=actual_teacher_ids).values_list(
                                    "teacher_no", flat=True
                                )
                            )
                        ),
                    )
                )
            actual_lead_id = next(
                (assignment.teacher_id for assignment in active if assignment.role == "lead"),
                target.lead_teacher_id,
            )
            lead_changed = data.lead_teacher_id != target.lead_teacher_id
            if data.lead_teacher_id != actual_lead_id or lead_changed:
                actual_lead_no = (
                    target.lead_teacher.teacher_no
                    if target.lead_teacher_id
                    else TeacherProfile.objects.filter(pk=actual_lead_id).values_list("teacher_no", flat=True).first()
                )
                updates.append(
                    FieldDifference(
                        field="lead_teacher_code",
                        expected=lead_code,
                        actual=actual_lead_no,
                    )
                )
            assignment_changes = _plan_assignment_changes(
                ctx,
                row=row,
                offering=target,
                term=term,
                teacher_codes=teacher_codes,
                lead_code=lead_code,
                school_date=school_date,
            )
            plan = TeachingPlan.objects.filter(offering_id=target.pk).only("id", "owner_id").first()
            if plan is not None and plan.owner_id == target.lead_teacher_id and data.lead_teacher_id != plan.owner_id:
                plan_owner_change = {
                    "old_owner_id": plan.owner_id,
                    "new_owner_id": data.lead_teacher_id,
                    "old_owner_code": target.lead_teacher.teacher_no if target.lead_teacher_id else "",
                    "new_owner_code": lead_code,
                }

        obj = resolve_object(
            ctx,
            entity_type=ENTITY_OFFERING,
            external_id=offering_code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        obj.metadata["teacher_codes"] = teacher_codes
        obj.metadata["lead_teacher_code"] = lead_code
        obj.metadata["term_code"] = term_code
        obj.metadata["assignment_changes"] = assignment_changes
        obj.metadata["plan_owner_change"] = plan_owner_change
        obj.metadata["lead_changed"] = bool(
            isinstance(target, CourseOffering) and data.lead_teacher_id != target.lead_teacher_id
        )
        ctx.graph.offerings[key] = obj
