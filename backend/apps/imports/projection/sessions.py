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
    FieldDifference,
    classroom_ref,
    diff_fields,
    issue_for,
    offering_ref,
    offering_scope,
    resolve_object,
    rule_ref,
    teacher_ref,
    term_ref,
)
from apps.imports.projection.planning import PlanData
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


def session_diff(target: ClassSession, data: ClassSession, teacher_ids: set[UUID]) -> list[FieldDifference]:
    fields = [
        "offering_id",
        "rule_id",
        "origin_teaching_date",
        "origin_period",
        "start_period",
        "end_period",
        "start_at",
        "end_at",
        "classroom_id",
        "status",
        "planned_hours",
        "actual_hours",
        "completed_by_teacher_id",
        "completed_at",
        "completion_ref",
    ]
    differences = diff_fields(
        target,
        {field_name: getattr(data, field_name) for field_name in fields},
        fields,
    )
    actual_teacher_ids = set(target.teachers.values_list("id", flat=True))
    if actual_teacher_ids != teacher_ids:
        differences.append(
            FieldDifference(
                field="teacher_codes",
                expected=len(teacher_ids),
                actual=len(actual_teacher_ids),
            )
        )
    return differences


def generated_session_layout_issues(
    *,
    row: ParsedRow,
    term: Term,
    offering: CourseOffering,
    classroom: Classroom,
    teacher_ids: set[UUID],
    planned: calendar.PlannedSession,
    start_time: time | None,
    end_time: time | None,
) -> list:
    from apps.imports.parsing import ImportIssue

    issues: list[ImportIssue] = []
    checks: list[tuple[str, object, object]] = [
        ("session_date", planned.session_date, row.values.get("session_date")),
        ("start_period", planned.start_period, row.values.get("start_period")),
        ("end_period", planned.end_period, row.values.get("end_period")),
        ("classroom_code", planned.classroom_id, classroom.pk),
        ("teacher_codes", set(planned.teacher_ids), teacher_ids),
        ("planned_hours", planned.planned_hours, row.values.get("planned_hours")),
    ]
    for field_name, expected, actual in checks:
        if expected != actual:
            issues.append(
                issue_for(
                    row,
                    field_name,
                    "CHANGE_NOT_SUPPORTED_YET",
                    (
                        "该行与已有规则展开的课次不一致。首次导入的当前日期/节次/教室/教师"
                        "属于单次变化，B12a 不自动改课；请让课次与规则/校历一致，"
                        "或等待 B12b 的调课与人工决定项。"
                    ),
                    value=actual,
                )
            )
    if start_time is not None or end_time is not None:
        tzinfo = term_timezone(term)
        if tzinfo is None:
            return issues
        expected_start = planned.start_at.astimezone(tzinfo).time()
        expected_end = planned.end_at.astimezone(tzinfo).time()
        if start_time is not None and start_time != expected_start:
            issues.append(
                issue_for(
                    row,
                    "start_time",
                    "CHANGE_NOT_SUPPORTED_YET",
                    "显式开始时间与规则生成时间不同；B12a 不支持单次时间变化。",
                )
            )
        if end_time is not None and end_time != expected_end:
            issues.append(
                issue_for(
                    row,
                    "end_time",
                    "CHANGE_NOT_SUPPORTED_YET",
                    "显式结束时间与规则生成时间不同；B12a 不支持单次时间变化。",
                )
            )
    if planned.offering_id != offering.pk:
        issues.append(
            issue_for(
                row,
                "offering_code",
                "RULE_SESSION_NOT_FOUND",
                "规则生成课次与 sessions 行的 offering 不一致，不能模糊配对。",
            )
        )
    return issues


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


def build_sessions(ctx: BuildContext, plan: PlanData, now: datetime) -> None:
    bound_planned: dict[tuple[str, str, date, int], ParsedRow] = {}

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
        session_date = row.values.get("session_date")
        if (
            isinstance(session_date, date)
            and term.start_date
            and term.end_date
            and not (term.start_date <= session_date <= term.end_date)
        ):
            ctx.add_issue(issue_for(row, "session_date", "DATE_OUTSIDE_TERM", "课次日期必须在学期起止日期内。"))
        classroom_code = row.values.get("classroom_code")
        classroom = classroom_ref(ctx, classroom_code or "")
        if classroom is None:
            ctx.add_issue(issue_for(row, "classroom_code", "FK_NOT_FOUND", f"教室 {classroom_code} 不存在。"))
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
        session_date = row.values.get("session_date")
        start_period = row.values.get("start_period")
        end_period = row.values.get("end_period")
        start_time = row.values.get("start_time")
        end_time = row.values.get("end_time")

        existing_identity = ctx.existing.identity(ENTITY_SESSION, external_session_id)
        existing_target = ctx.existing.target(ENTITY_SESSION, external_session_id) if existing_identity else None

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
            planned_key = (term_code, origin_rule_code, origin_date, origin_period)
            planned = plan.planned_by_key.get(planned_key)
            if planned is None:
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
            layout_issues = generated_session_layout_issues(
                row=row,
                term=term,
                offering=offering,
                classroom=classroom,
                teacher_ids=teacher_ids,
                planned=planned,
                start_time=start_time,
                end_time=end_time,
            )
            if layout_issues:
                ctx.add_issues(layout_issues)
                continue
            generation_key = (planned.rule_id, planned.origin_teaching_date, planned.origin_period)
            if existing_identity is None and generation_key in plan.existing_generation_keys:
                ctx.add_issue(
                    issue_for(
                        row,
                        "external_session_id",
                        "CHANGE_NOT_SUPPORTED_YET",
                        (
                            "该规则课次已经在数据库生成，但还没有 external_session_id 映射。"
                            "B12a 只支持首次导入或完全相同内容的重复导入；绑定已有课次属于 B12b。"
                        ),
                    )
                )
                continue
            data = session_desired_data(
                term=term,
                offering=offering,
                row=row,
                rule_id=planned.rule_id,
                origin_teaching_date=planned.origin_teaching_date,
                origin_period=planned.origin_period,
                start_at=planned.start_at,
                end_at=planned.end_at,
                classroom=classroom,
                planned_hours=planned.planned_hours,
                status_reason=planned.status_reason if status == planned.status else "",
            )
            data.start_period = planned.start_period
            data.end_period = planned.end_period
            data.classroom_id = planned.classroom_id
            data.planned_hours = planned.planned_hours
            validate_completion_row(
                ctx=ctx,
                row=row,
                end_at=planned.end_at,
                now=now,
                teacher_ids=teacher_ids,
            )
            data.completed_by_teacher_id = row.values.get("_completed_by_teacher_id")
            differences: list[FieldDifference] = []
            if isinstance(existing_target, ClassSession):
                differences = session_diff(existing_target, data, teacher_ids)
            obj = resolve_object(
                ctx,
                entity_type=ENTITY_SESSION,
                external_id=external_session_id,
                data=data,
                rows=[row],
                natural=None,
                differences=differences,
            )
            obj.metadata["teacher_ids"] = teacher_ids
            obj.metadata["teacher_codes"] = teacher_codes
            obj.metadata["term_code"] = term_code
            obj.metadata["offering_code"] = offering_code
            obj.planned_key = planned_key
            obj.generated_session = True
            ctx.graph.sessions[external_session_id] = obj
            bound_planned[planned_key] = row
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
        data = session_desired_data(
            term=term,
            offering=offering,
            row=row,
            rule_id=None,
            origin_teaching_date=None,
            origin_period=None,
            start_at=start_at,
            end_at=end_at,
            classroom=classroom,
            planned_hours=row.values.get("planned_hours") or Decimal("0"),
        )
        validate_completion_row(
            ctx=ctx,
            row=row,
            end_at=end_at,
            now=now,
            teacher_ids=teacher_ids,
        )
        data.completed_by_teacher_id = row.values.get("_completed_by_teacher_id")
        differences = []
        if isinstance(existing_target, ClassSession):
            differences = session_diff(existing_target, data, teacher_ids)
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_SESSION,
            external_id=external_session_id,
            data=data,
            rows=[row],
            natural=None,
            differences=differences,
        )
        obj.metadata["teacher_ids"] = teacher_ids
        obj.metadata["teacher_codes"] = teacher_codes
        obj.metadata["term_code"] = term_code
        obj.metadata["offering_code"] = offering_code
        obj.generated_session = False
        ctx.graph.sessions[external_session_id] = obj
