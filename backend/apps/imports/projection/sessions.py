"""Session binding, validation and one-off session projections."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.academics.models import CourseOffering, Period, TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.imports.parsing import ParsedRow
from apps.imports.projection.base import (
    ENTITY_SESSION,
    BuildContext,
    ImportObject,
    classroom_ref,
    issue_for,
    offering_ref,
    offering_scope,
    rule_ref,
    teacher_ref,
    term_ref,
)
from apps.imports.projection.planning import PlanData, planned_for_origin
from apps.scheduling import calendar
from apps.scheduling.models import ClassSession, SessionStatus


def period_for(ctx: BuildContext, term: Term, number: int) -> Period | None:
    obj = ctx.graph.periods.get((term.code, number))
    if obj is not None and isinstance(obj.data, Period):
        period = obj.data
        if period.start_time and period.end_time:
            return period
    if term.pk:
        return Period.objects.filter(term_id=term.pk, period_number=number).first()
    return None


def term_timezone(term: Term) -> ZoneInfo | None:
    try:
        return ZoneInfo(term.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def session_bounds(
    *,
    term: Term,
    session_date: date | None,
    start_period: int | None,
    end_period: int | None,
    start_time: time | None,
    end_time: time | None,
    ctx: BuildContext,
) -> tuple[datetime | None, datetime | None]:
    if not session_date:
        return None, None
    tzinfo = term_timezone(term)
    if tzinfo is None:
        return None, None
    if start_time is not None and end_time is not None:
        return (
            datetime.combine(session_date, start_time, tzinfo=tzinfo),
            datetime.combine(session_date, end_time, tzinfo=tzinfo),
        )
    if not isinstance(start_period, int) or not isinstance(end_period, int):
        return None, None
    start = period_for(ctx, term, start_period)
    end = period_for(ctx, term, end_period)
    if start is None or end is None:
        return None, None
    return (
        datetime.combine(session_date, start.start_time, tzinfo=tzinfo),
        datetime.combine(session_date, end.end_time, tzinfo=tzinfo),
    )


def session_desired_data(
    *,
    term: Term,
    offering: CourseOffering,
    row: ParsedRow,
    rule_id: UUID | None,
    origin_teaching_date: date | None,
    origin_period: int | None,
    start_at: datetime,
    end_at: datetime,
    classroom: Classroom,
    planned_hours: Decimal,
    status_reason: str = "",
) -> ClassSession:
    status = row.values.get("status") or SessionStatus.SCHEDULED
    actual_hours = row.values.get("actual_hours")
    if status == SessionStatus.CANCELLED:
        actual_hours = Decimal("0.0")
    elif status == SessionStatus.SCHEDULED:
        actual_hours = None
    completed_by = teacher_id_for_code(row, row.values.get("completed_by_teacher_code"))
    return ClassSession(
        id=uuid.uuid4(),
        offering_id=offering.pk,
        rule_id=rule_id,
        origin_teaching_date=origin_teaching_date,
        origin_period=origin_period,
        start_period=row.values.get("start_period") or 0,
        end_period=row.values.get("end_period") or 0,
        start_at=start_at,
        end_at=end_at,
        classroom_id=classroom.pk,
        status=status,
        status_reason=status_reason,
        planned_hours=row.values.get("planned_hours") or Decimal("0"),
        actual_hours=actual_hours,
        completed_by_teacher_id=completed_by,
        completed_at=row.values.get("completed_at") if status == SessionStatus.COMPLETED else None,
        completion_ref=(row.values.get("completion_ref") or "") if status == SessionStatus.COMPLETED else "",
    )


def teacher_id_for_code(row: ParsedRow, code: str | None) -> UUID | None:
    if not code:
        return None
    return row.values.get("_resolved_teacher_ids", {}).get(code)


def session_teacher_links(ctx: BuildContext, row: ParsedRow, teacher_codes: list[str]) -> dict[str, TeacherProfile]:
    result: dict[str, TeacherProfile] = {}
    for code in teacher_codes:
        teacher = teacher_ref(ctx, code)
        if teacher is None:
            ctx.add_issue(
                issue_for(
                    row,
                    "teacher_codes",
                    "FK_NOT_FOUND",
                    f"教师 {code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
            continue
        result[code] = teacher
    row.values["_resolved_teacher_ids"] = {code: teacher.pk for code, teacher in result.items()}
    return result


def _planned_layout(planned: calendar.PlannedSession) -> tuple:
    return (
        planned.session_date,
        planned.start_period,
        planned.end_period,
        planned.start_at,
        planned.end_at,
        planned.classroom_id,
        tuple(sorted(set(planned.teacher_ids), key=str)),
    )


def _session_layout(session: ClassSession, teacher_ids: set[UUID], tzinfo) -> tuple:
    return (
        session.start_at.astimezone(tzinfo).date(),
        session.start_period,
        session.end_period,
        session.start_at,
        session.end_at,
        session.classroom_id,
        tuple(sorted(set(teacher_ids), key=str)),
    )


def _desired_layout(
    *,
    session_date: date,
    start_period: int,
    end_period: int,
    start_at: datetime,
    end_at: datetime,
    classroom_id: UUID,
    teacher_ids: set[UUID],
) -> tuple:
    return (
        session_date,
        start_period,
        end_period,
        start_at,
        end_at,
        classroom_id,
        tuple(sorted(set(teacher_ids), key=str)),
    )


def sessions_equal(
    existing: ClassSession,
    data: ClassSession,
    *,
    teacher_ids: set[UUID],
    tzinfo,
) -> bool:
    """Whether every field B12 owns already matches; status_reason is display-only."""
    if _session_layout(existing, set(existing.teachers.values_list("id", flat=True)), tzinfo) != _desired_layout(
        session_date=data.start_at.astimezone(tzinfo).date(),
        start_period=data.start_period,
        end_period=data.end_period,
        start_at=data.start_at,
        end_at=data.end_at,
        classroom_id=data.classroom_id,
        teacher_ids=teacher_ids,
    ):
        return False
    if existing.status != data.status or existing.planned_hours != data.planned_hours:
        return False
    if data.status == SessionStatus.COMPLETED:
        return (
            existing.actual_hours == data.actual_hours
            and existing.completed_by_teacher_id == data.completed_by_teacher_id
            and existing.completed_at == data.completed_at
            and existing.completion_ref == data.completion_ref
        )
    return True


def _layout_differs_from_plan(
    *,
    planned: calendar.PlannedSession,
    session_date: date,
    start_period: int,
    end_period: int,
    start_at: datetime,
    end_at: datetime,
    classroom_id: UUID,
    teacher_ids: set[UUID],
) -> bool:
    return _planned_layout(planned) != _desired_layout(
        session_date=session_date,
        start_period=start_period,
        end_period=end_period,
        start_at=start_at,
        end_at=end_at,
        classroom_id=classroom_id,
        teacher_ids=teacher_ids,
    )


def _layout_differs_from_session(
    existing: ClassSession,
    *,
    session_date: date,
    start_period: int,
    end_period: int,
    start_at: datetime,
    end_at: datetime,
    classroom_id: UUID,
    teacher_ids: set[UUID],
    tzinfo,
) -> bool:
    return _session_layout(existing, set(existing.teachers.values_list("id", flat=True)), tzinfo) != _desired_layout(
        session_date=session_date,
        start_period=start_period,
        end_period=end_period,
        start_at=start_at,
        end_at=end_at,
        classroom_id=classroom_id,
        teacher_ids=teacher_ids,
    )


def _session_object(
    ctx: BuildContext,
    *,
    row: ParsedRow,
    external_session_id: str,
    data: ClassSession,
    existing_identity,
    existing_target: ClassSession | None,
    state: str,
) -> ImportObject:
    obj = ImportObject(
        entity_type=ENTITY_SESSION,
        external_id=external_session_id,
        data=data,
        rows=[row],
        identity=existing_identity,
        existing=existing_target,
        state=state,
    )
    ctx.graph.add(obj)
    ctx.graph.row_primary.setdefault(row.source_key, obj)
    return obj


def build_sessions(ctx: BuildContext, plan: PlanData, now: datetime) -> None:
    bound_planned: set[tuple[UUID, date, int]] = set()

    for row in ctx.parsed.rows_for("sessions"):
        external_session_id = row.values.get("external_session_id")
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        if not external_session_id or not term_code or not offering_code:
            continue
        if external_session_id in ctx.graph.sessions:
            continue
        term = term_ref(ctx, term_code)
        offering = offering_ref(ctx, term_code, offering_code)
        if term is None:
            ctx.add_issue(issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        if offering is None:
            ctx.add_issue(issue_for(row, "offering_code", "FK_NOT_FOUND", f"课程开设 {offering_code} 不存在。"))
            continue
        if offering.term_id != term.pk:
            ctx.add_issue(issue_for(row, "term_code", "TERM_MISMATCH", "课次学期必须与课程开设学期一致。"))
            continue
        if term_code not in plan.term_plans:
            ctx.add_issue(
                issue_for(
                    row,
                    "term_code",
                    "TERM_NOT_PLANNED",
                    "学期日历不完整（日期、首周周一、周数或时区无效），无法校验规则课次。",
                )
            )
            continue
        session_date = row.values.get("session_date")
        if (
            isinstance(session_date, date)
            and term.start_date
            and term.end_date
            and not (term.start_date <= session_date <= term.end_date)
        ):
            ctx.add_issue(issue_for(row, "session_date", "DATE_OUTSIDE_TERM", "课次日期必须在学期起止日期内。"))
        elif (
            isinstance(session_date, date)
            and term.start_date
            and term.end_date
            and term.first_week_monday
            and calendar.teaching_week(term, session_date) is None
        ):
            ctx.add_issue(
                issue_for(
                    row,
                    "session_date",
                    "DATE_OUTSIDE_TEACHING_WEEK",
                    "课次日期不在学期教学周内；规则不会在该日期生成课次。",
                )
            )
        classroom_code = row.values.get("classroom_code")
        classroom = classroom_ref(ctx, classroom_code or "")
        if classroom is None:
            ctx.add_issue(issue_for(row, "classroom_code", "FK_NOT_FOUND", f"教室 {classroom_code} 不存在。"))
            continue
        if classroom.school_id != term.school_id:
            ctx.add_issue(
                issue_for(
                    row,
                    "classroom_code",
                    "CLASSROOM_SCHOOL_MISMATCH",
                    "教室必须与学期属于同一学校。",
                )
            )
            continue

        teacher_codes = list(row.values.get("teacher_codes") or [])
        teachers = session_teacher_links(ctx, row, teacher_codes)
        teacher_ids = {teacher.pk for teacher in teachers.values()}
        scope_ids, _ = offering_scope(ctx, offering)
        for code, teacher in teachers.items():
            if teacher.pk not in scope_ids:
                ctx.add_issue(
                    issue_for(
                        row,
                        "teacher_codes",
                        "TEACHER_NOT_IN_OFFERING",
                        f"教师 {code} 不在该 offering 的任课范围内。",
                    )
                )

        origin_rule_code = row.values.get("origin_rule_code")
        origin_date = row.values.get("origin_teaching_date")
        origin_period = row.values.get("origin_start_period")
        has_origin = origin_rule_code is not None or origin_date is not None or origin_period is not None
        status = row.values.get("status")
        start_period = row.values.get("start_period")
        end_period = row.values.get("end_period")
        start_time = row.values.get("start_time")
        end_time = row.values.get("end_time")
        change_ref = (row.values.get("change_ref") or "").strip()

        existing_identity = ctx.existing.identity(ENTITY_SESSION, external_session_id)
        existing_target = ctx.existing.target(ENTITY_SESSION, external_session_id) if existing_identity else None
        if existing_identity is not None and not isinstance(existing_target, ClassSession):
            ctx.add_issue(
                issue_for(
                    row,
                    "external_session_id",
                    "IDENTITY_TARGET_MISSING",
                    f"来源映射 session:{external_session_id} 指向的本地课次已不存在；请修复数据或建立新的来源映射。",
                )
            )
            continue
        if existing_target is not None and existing_target.offering_id != offering.pk:
            ctx.add_issue(
                issue_for(
                    row,
                    "offering_code",
                    "SESSION_OFFERING_MISMATCH",
                    "该 external_session_id 已映射到另一门课程开设的课次，不能通过改 offering_code 迁移课次。",
                )
            )
            continue

        planned: calendar.PlannedSession | None = None
        rule = None
        if has_origin:
            if not (origin_rule_code and isinstance(origin_date, date) and isinstance(origin_period, int)):
                continue
            rule = rule_ref(ctx, term_code, origin_rule_code)
            if rule is None:
                ctx.add_issue(
                    issue_for(
                        row,
                        "origin_rule_code",
                        "RULE_NOT_FOUND",
                        f"规则 {origin_rule_code} 不存在于本批次或此前同来源导入的数据中。",
                    )
                )
                continue
            if rule.offering_id != offering.pk:
                ctx.add_issue(
                    issue_for(
                        row,
                        "origin_rule_code",
                        "SESSION_OFFERING_MISMATCH",
                        "origin_rule_code 指向的规则不属于本行的 offering_code；不能用其他课程的规则生成课次。",
                    )
                )
                continue
            if (
                existing_target is not None
                and existing_target.rule_id is not None
                and existing_target.rule_id != rule.pk
            ):
                ctx.add_issue(
                    issue_for(
                        row,
                        "origin_rule_code",
                        "SESSION_RULE_MISMATCH",
                        "外部编号已映射到另一条规则的课次，不能把本行绑定到当前规则展开结果。",
                    )
                )
                continue
            planned = planned_for_origin(
                plan,
                term,
                term_code=term_code,
                rule=rule,
                origin_date=origin_date,
                origin_period=origin_period,
                allow_week_fallback=existing_target is not None,
            )
            if planned is None:
                if existing_target is None:
                    ctx.add_issue(
                        issue_for(
                            row,
                            "origin_teaching_date",
                            "RULE_SESSION_NOT_FOUND",
                            (
                                "按 origin_rule_code、origin_teaching_date、origin_start_period "
                                "找不到规则展开课次；不能做近似匹配。"
                            ),
                        )
                    )
                    continue
            else:
                planned_key = (planned.rule_id, planned.origin_teaching_date, planned.origin_period)
                if planned_key in bound_planned:
                    ctx.add_issue(
                        issue_for(
                            row,
                            "external_session_id",
                            "DUPLICATE_RULE_SESSION",
                            "本批次的另一行已经绑定同一个规则生成课次；不能重复绑定。",
                        )
                    )
                    continue
                if existing_target is None and planned_key in plan.existing_generation_keys:
                    ctx.add_issue(
                        issue_for(
                            row,
                            "external_session_id",
                            "SESSION_NOT_BOUND",
                            (
                                "该规则课次已经在数据库生成，但还没有 external_session_id 映射；"
                                "请先提供原课次编号或在预览中明确配对，不能按周次猜测绑定。"
                            ),
                        )
                    )
                    continue
        elif existing_target is not None and existing_target.rule_id is not None:
            ctx.add_issue(
                issue_for(
                    row,
                    "origin_rule_code",
                    "ORIGIN_REQUIRED_FOR_RULE_SESSION",
                    "该外部编号已映射到规则生成的课次；本行必须填写 origin 三字段。",
                )
            )
            continue

        start_at, end_at = session_bounds(
            term=term,
            session_date=session_date,
            start_period=start_period,
            end_period=end_period,
            start_time=start_time,
            end_time=end_time,
            ctx=ctx,
        )
        if start_at is None or end_at is None:
            ctx.add_issue(
                issue_for(
                    row,
                    "start_period",
                    "FK_NOT_FOUND",
                    "无法按节次派生课次时间；请确认节次存在，或同时填写 start_time/end_time。",
                )
            )
            continue
        if end_at <= start_at:
            ctx.add_issue(issue_for(row, "end_time", "INVALID_TIME_RANGE", "课次结束时间必须晚于开始时间。"))
            continue

        if planned is not None and existing_target is not None:
            if existing_target.rule_id != planned.rule_id:
                ctx.add_issue(
                    issue_for(
                        row,
                        "origin_rule_code",
                        "SESSION_RULE_MISMATCH",
                        "外部编号已映射到另一条规则的课次，不能把本行绑定到当前规则展开结果。",
                    )
                )
                continue
        if planned is not None and existing_target is None and existing_identity is None:
            rule_id = planned.rule_id
            origin_teaching_date = planned.origin_teaching_date
            origin_period = planned.origin_period
        elif existing_target is not None:
            rule_id = existing_target.rule_id
            origin_teaching_date = existing_target.origin_teaching_date
            origin_period = existing_target.origin_period
        else:
            rule_id = None
            origin_teaching_date = None
            origin_period = None

        status_reason = ""
        if planned is not None and status == planned.status:
            status_reason = planned.status_reason
        if status == SessionStatus.CANCELLED and not status_reason:
            status_reason = "导入来源标记为已取消"
        data = session_desired_data(
            term=term,
            offering=offering,
            row=row,
            rule_id=rule_id,
            origin_teaching_date=origin_teaching_date,
            origin_period=origin_period,
            start_at=start_at,
            end_at=end_at,
            classroom=classroom,
            planned_hours=row.values.get("planned_hours") or Decimal("0"),
            status_reason=status_reason,
        )
        data.start_period = start_period or 0
        data.end_period = end_period or 0
        data.classroom_id = classroom.pk
        data.planned_hours = row.values.get("planned_hours") or Decimal("0")
        validate_completion_row(
            ctx=ctx,
            row=row,
            end_at=end_at,
            now=now,
            teacher_ids=teacher_ids,
        )
        data.completed_by_teacher_id = row.values.get("_completed_by_teacher_id")

        tzinfo = term_timezone(term)
        if tzinfo is None:
            ctx.add_issue(issue_for(row, "term_code", "INVALID_TIMEZONE", "学期时区无效，无法计算课次日期。"))
            continue
        explicit_change = False
        if planned is not None:
            explicit_change = _layout_differs_from_plan(
                planned=planned,
                session_date=session_date,
                start_period=data.start_period,
                end_period=data.end_period,
                start_at=data.start_at,
                end_at=data.end_at,
                classroom_id=data.classroom_id,
                teacher_ids=teacher_ids,
            )
        elif existing_target is not None:
            explicit_change = _layout_differs_from_session(
                existing_target,
                session_date=session_date,
                start_period=data.start_period,
                end_period=data.end_period,
                start_at=data.start_at,
                end_at=data.end_at,
                classroom_id=data.classroom_id,
                teacher_ids=teacher_ids,
                tzinfo=tzinfo,
            )
        if explicit_change and not change_ref:
            ctx.add_issue(
                issue_for(
                    row,
                    "change_ref",
                    "CHANGE_REF_REQUIRED",
                    "该行与规则/校历计算结果不同，属于单次正式时间、节次、教室或教师变化；必须填写 change_ref。",
                )
            )
            _session_object(
                ctx,
                row=row,
                external_session_id=external_session_id,
                data=data,
                existing_identity=existing_identity,
                existing_target=existing_target,
                state="error",
            )
            continue

        if existing_target is None:
            state = "new"
        elif sessions_equal(existing_target, data, teacher_ids=teacher_ids, tzinfo=tzinfo):
            state = "unchanged"
        else:
            state = "changed"

        obj = _session_object(
            ctx,
            row=row,
            external_session_id=external_session_id,
            data=data,
            existing_identity=existing_identity,
            existing_target=existing_target,
            state=state,
        )
        obj.metadata["teacher_ids"] = teacher_ids
        obj.metadata["teacher_codes"] = teacher_codes
        obj.metadata["term_code"] = term_code
        obj.metadata["offering_code"] = offering_code
        obj.metadata["explicit_change"] = explicit_change
        obj.metadata["change_ref"] = change_ref
        obj.metadata["planned"] = planned
        if planned is not None:
            planned_key = (planned.rule_id, planned.origin_teaching_date, planned.origin_period)
            obj.metadata["planned_key"] = planned_key
            obj.planned_key = planned_key
            bound_planned.add(planned_key)
        obj.generated_session = has_origin
        ctx.graph.sessions[external_session_id] = obj


def validate_completion_row(
    *,
    ctx: BuildContext,
    row: ParsedRow,
    end_at: datetime | None,
    now: datetime,
    teacher_ids: set[UUID],
) -> None:
    if row.values.get("status") != SessionStatus.COMPLETED:
        return
    completed_by_code = row.values.get("completed_by_teacher_code")
    completed_teacher = teacher_ref(ctx, completed_by_code) if completed_by_code else None
    row.values["_completed_by_teacher_id"] = completed_teacher.pk if completed_teacher is not None else None
    if completed_by_code and completed_teacher is None:
        ctx.add_issue(
            issue_for(
                row,
                "completed_by_teacher_code",
                "FK_NOT_FOUND",
                f"完成确认教师 {completed_by_code} 不存在。",
            )
        )
    elif completed_teacher is not None and completed_teacher.pk not in teacher_ids:
        ctx.add_issue(
            issue_for(
                row,
                "completed_by_teacher_code",
                "COMPLETED_BY_NOT_TEACHER",
                "完成确认教师必须属于该课次的实际授课教师。",
            )
        )
    completed_at = row.values.get("completed_at")
    if isinstance(completed_at, datetime):
        if end_at is not None and completed_at < end_at:
            ctx.add_issue(
                issue_for(
                    row,
                    "completed_at",
                    "COMPLETION_BEFORE_END",
                    f"完成确认时间不能早于课次结束时间 {end_at.isoformat()}。",
                )
            )
        if completed_at > now:
            ctx.add_issue(
                issue_for(
                    row,
                    "completed_at",
                    "COMPLETION_IN_FUTURE",
                    "完成确认时间不能晚于导入时刻；不能把未来课次标记为已完成。",
                )
            )
