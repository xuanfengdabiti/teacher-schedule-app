"""Room and person unavailability projections."""

from __future__ import annotations

import uuid
from datetime import datetime

from apps.classrooms.models import RoomBlock
from apps.imports.parsing import ParsedRow
from apps.imports.projection.base import (
    ENTITY_PERSON_BLOCK,
    ENTITY_ROOM_BLOCK,
    BuildContext,
    classroom_ref,
    diff_fields,
    issue_for,
    resolve_object,
    student_ref,
    teacher_ref,
    term_ref,
)
from apps.imports.projection.sessions import term_timezone
from apps.scheduling.models import PersonBlock


def validate_block_term_range(
    ctx: BuildContext,
    row: ParsedRow,
    term,
    start_at: datetime | None,
    end_at: datetime | None,
) -> None:
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return
    tzinfo = term_timezone(term)
    if tzinfo is None or not (term.start_date and term.end_date):
        return
    local_start = start_at.astimezone(tzinfo).date()
    local_end = end_at.astimezone(tzinfo).date()
    if not (term.start_date <= local_start <= term.end_date and term.start_date <= local_end <= term.end_date):
        ctx.add_issue(
            issue_for(
                row,
                "start_at",
                "BLOCK_OUTSIDE_TERM",
                "占用起止时间必须落在学期日期范围内。",
            )
        )


def build_room_blocks(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("room_blocks"):
        code = row.values.get("block_code")
        term_code = row.values.get("term_code")
        classroom_code = row.values.get("classroom_code")
        if not code or not term_code or not classroom_code or code in ctx.graph.room_blocks:
            continue
        term = term_ref(ctx, term_code)
        classroom = classroom_ref(ctx, classroom_code)
        if term is None:
            ctx.add_issue(issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        if classroom is None:
            ctx.add_issue(issue_for(row, "classroom_code", "FK_NOT_FOUND", f"教室 {classroom_code} 不存在。"))
            continue
        validate_block_term_range(
            ctx,
            row,
            term,
            row.values.get("start_at"),
            row.values.get("end_at"),
        )
        data = RoomBlock(
            id=uuid.uuid4(),
            classroom_id=classroom.pk,
            start_at=row.values.get("start_at"),
            end_at=row.values.get("end_at"),
            reason=row.values.get("reason") or "",
            block_code=code,
        )
        natural = RoomBlock.objects.filter(block_code=code).first()
        identity = ctx.existing.identity(ENTITY_ROOM_BLOCK, code)
        target = ctx.existing.target(ENTITY_ROOM_BLOCK, code) if identity else None
        unsupported = []
        updates = []
        if isinstance(target, RoomBlock):
            unsupported = diff_fields(
                target,
                {"classroom_id": data.classroom_id, "block_code": data.block_code},
                ["classroom_id", "block_code"],
            )
            updates = diff_fields(
                target,
                {"start_at": data.start_at, "end_at": data.end_at, "reason": data.reason},
                ["start_at", "end_at", "reason"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_ROOM_BLOCK,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.room_blocks[code] = obj


def build_person_blocks(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("resource_unavailability"):
        code = row.values.get("block_code")
        term_code = row.values.get("term_code")
        resource_type = row.values.get("resource_type")
        resource_code = row.values.get("resource_code")
        if not code or not term_code or not resource_type or not resource_code or code in ctx.graph.person_blocks:
            continue
        term = term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        teacher = None
        student = None
        if resource_type == "teacher":
            teacher = teacher_ref(ctx, resource_code)
            if teacher is None:
                ctx.add_issue(issue_for(row, "resource_code", "FK_NOT_FOUND", f"教师 {resource_code} 不存在。"))
                continue
        elif resource_type == "student":
            student = student_ref(ctx, resource_code)
            if student is None:
                ctx.add_issue(issue_for(row, "resource_code", "FK_NOT_FOUND", f"学生 {resource_code} 不存在。"))
                continue
        else:
            continue
        validate_block_term_range(
            ctx,
            row,
            term,
            row.values.get("start_at"),
            row.values.get("end_at"),
        )
        data = PersonBlock(
            id=uuid.uuid4(),
            teacher_id=teacher.pk if teacher else None,
            student_id=student.pk if student else None,
            start_at=row.values.get("start_at"),
            end_at=row.values.get("end_at"),
            reason=row.values.get("reason") or "",
            block_code=code,
        )
        natural = PersonBlock.objects.filter(block_code=code).first()
        identity = ctx.existing.identity(ENTITY_PERSON_BLOCK, code)
        target = ctx.existing.target(ENTITY_PERSON_BLOCK, code) if identity else None
        unsupported = []
        updates = []
        if isinstance(target, PersonBlock):
            unsupported = diff_fields(
                target,
                {
                    "teacher_id": data.teacher_id,
                    "student_id": data.student_id,
                    "block_code": data.block_code,
                },
                ["teacher_id", "student_id", "block_code"],
            )
            updates = diff_fields(
                target,
                {"start_at": data.start_at, "end_at": data.end_at, "reason": data.reason},
                ["start_at", "end_at", "reason"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_PERSON_BLOCK,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.person_blocks[code] = obj
