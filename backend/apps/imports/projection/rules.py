"""Schedule-rule projections."""

from __future__ import annotations

import uuid
from decimal import Decimal

from apps.academics.models import TeacherProfile
from apps.imports.projection.base import (
    ENTITY_RULE,
    BuildContext,
    FieldDifference,
    classroom_ref,
    diff_fields,
    issue_for,
    offering_ref,
    offering_scope,
    period_exists,
    resolve_object,
    teacher_ref,
    term_ref,
)
from apps.scheduling.models import ScheduleRule


def build_rules(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("rules"):
        rule_code = row.values.get("rule_code")
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        classroom_code = row.values.get("classroom_code")
        if not rule_code or not term_code or not offering_code or not classroom_code:
            continue
        key = (term_code, rule_code)
        if key in ctx.graph.rules:
            continue
        offering = offering_ref(ctx, term_code, offering_code)
        classroom = classroom_ref(ctx, classroom_code)
        if offering is None:
            ctx.add_issue(
                issue_for(
                    row,
                    "offering_code",
                    "FK_NOT_FOUND",
                    f"课程开设 {offering_code} 不存在于学期 {term_code} 的本批或已导入数据中。",
                )
            )
            continue
        term = term_ref(ctx, term_code)
        if term is None or offering.term_id != term.pk:
            ctx.add_issue(issue_for(row, "term_code", "TERM_MISMATCH", "规则学期必须与课程开设学期一致。"))
            continue
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

        weeks = list(row.values.get("weeks") or [])
        invalid_weeks = [week for week in weeks if not 1 <= week <= (term.teaching_week_count or 0)]
        if invalid_weeks:
            ctx.add_issue(
                issue_for(
                    row,
                    "weeks",
                    "WEEK_OUT_OF_RANGE",
                    f"教学周必须落在 1…{term.teaching_week_count} 范围内。",
                )
            )

        teacher_codes = list(row.values.get("teacher_codes") or [])
        teachers = {code: teacher_ref(ctx, code) for code in teacher_codes}
        missing = [code for code, teacher in teachers.items() if teacher is None]
        for code in missing:
            ctx.add_issue(
                issue_for(
                    row,
                    "teacher_codes",
                    "FK_NOT_FOUND",
                    f"教师 {code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
        teacher_ids = tuple(sorted((teacher.pk for teacher in teachers.values() if teacher is not None), key=str))

        scope_ids, _ = offering_scope(ctx, offering)
        for code, teacher in teachers.items():
            if teacher is not None and teacher.pk not in scope_ids:
                ctx.add_issue(
                    issue_for(
                        row,
                        "teacher_codes",
                        "TEACHER_NOT_IN_OFFERING",
                        f"教师 {code} 不在该 offering 的 teacher_codes 任课范围内。",
                    )
                )

        start_period = row.values.get("start_period")
        end_period = row.values.get("end_period")
        missing_periods: list[int] = []
        if isinstance(start_period, int) and isinstance(end_period, int) and start_period <= end_period:
            missing_periods = [
                number for number in range(start_period, end_period + 1) if not period_exists(ctx, term, number)
            ]
        if missing_periods:
            ctx.add_issue(
                issue_for(
                    row,
                    "start_period",
                    "FK_NOT_FOUND",
                    f"学期 {term_code} 中不存在节次 {missing_periods}；请先导入节次或修正范围。",
                )
            )

        data = ScheduleRule(
            id=uuid.uuid4(),
            offering_id=offering.pk,
            rule_code=rule_code,
            weekday=row.values.get("weekday") or 0,
            start_period=start_period or 0,
            end_period=end_period or 0,
            weeks=sorted(row.values.get("weeks") or []),
            classroom_id=classroom.pk,
            planned_hours=row.values.get("planned_hours") or Decimal("0"),
        )
        natural = (
            ScheduleRule.objects.filter(offering__term_id=offering.term_id, rule_code=rule_code).first()
            if offering.pk
            else None
        )
        identity = ctx.existing.identity(ENTITY_RULE, rule_code)
        target = ctx.existing.target(ENTITY_RULE, rule_code) if identity else None
        unsupported: list[FieldDifference] = []
        updates: list[FieldDifference] = []
        if isinstance(target, ScheduleRule):
            unsupported = diff_fields(
                target,
                {
                    "offering_id": data.offering_id,
                    "rule_code": data.rule_code,
                },
                ["offering_id", "rule_code"],
            )
            updates = diff_fields(
                target,
                {
                    "weekday": data.weekday,
                    "start_period": data.start_period,
                    "end_period": data.end_period,
                    "weeks": sorted(data.weeks or []),
                    "classroom_id": data.classroom_id,
                    "planned_hours": data.planned_hours,
                },
                [
                    "weekday",
                    "start_period",
                    "end_period",
                    "weeks",
                    "classroom_id",
                    "planned_hours",
                ],
            )
            actual_teacher_ids = {teacher_id for teacher_id in target.teachers.values_list("id", flat=True)}
            if set(teacher_ids) != actual_teacher_ids:
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
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_RULE,
            external_id=rule_code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        obj.metadata["teacher_ids"] = teacher_ids
        obj.metadata["teacher_codes"] = teacher_codes
        obj.metadata["term_code"] = term_code
        obj.metadata["offering_code"] = offering_code
        ctx.graph.rules[key] = obj
