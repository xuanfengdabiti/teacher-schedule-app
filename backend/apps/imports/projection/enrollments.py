"""Enrollment projections."""

from __future__ import annotations

import uuid
from datetime import date

from django.db import models

from apps.academics.models import Enrollment
from apps.imports.projection.base import (
    ENTITY_ENROLLMENT,
    ERROR,
    BuildContext,
    diff_fields,
    issue_for,
    offering_ref,
    resolve_object,
    student_ref,
    term_ref,
)


def build_enrollments(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("enrollments"):
        enrollment_code = row.values.get("enrollment_code")
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        student_code = row.values.get("student_code")
        if not enrollment_code or not term_code or not offering_code or not student_code:
            continue
        if enrollment_code in ctx.graph.enrollments:
            continue
        offering = offering_ref(ctx, term_code, offering_code)
        student = student_ref(ctx, student_code)
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
            ctx.add_issue(issue_for(row, "term_code", "TERM_MISMATCH", "选课学期必须与课程开设所属学期一致。"))
            continue
        if student is None:
            ctx.add_issue(
                issue_for(
                    row,
                    "student_code",
                    "FK_NOT_FOUND",
                    f"学生 {student_code} 无法建立。",
                )
            )
            continue
        data = Enrollment(
            id=uuid.uuid4(),
            offering_id=offering.pk,
            student_id=student.pk,
            effective_from=row.values.get("effective_from"),
            effective_until=row.values.get("effective_until"),
        )
        natural = (
            Enrollment.objects.filter(
                offering_id=offering.pk,
                student_id=student.pk,
                effective_from=data.effective_from,
            ).first()
            if offering.pk and student.pk and data.effective_from
            else None
        )
        identity = ctx.existing.identity(ENTITY_ENROLLMENT, enrollment_code)
        target = ctx.existing.target(ENTITY_ENROLLMENT, enrollment_code) if identity else None
        unsupported = []
        updates = []
        if isinstance(target, Enrollment):
            unsupported = diff_fields(
                target,
                {
                    "offering_id": data.offering_id,
                    "student_id": data.student_id,
                    "effective_from": data.effective_from,
                },
                ["offering_id", "student_id", "effective_from"],
            )
            updates = diff_fields(
                target,
                {"effective_until": data.effective_until},
                ["effective_until"],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_ENROLLMENT,
            external_id=enrollment_code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        if isinstance(target, Enrollment) and updates:
            desired_until = data.effective_until or date.max
            overlap = (
                Enrollment.objects.filter(
                    offering_id=target.offering_id,
                    student_id=target.student_id,
                )
                .exclude(pk=target.pk)
                .filter(effective_from__lt=desired_until)
                .filter(models.Q(effective_until__isnull=True) | models.Q(effective_until__gt=target.effective_from))
                .exists()
            )
            if overlap:
                obj.state = ERROR
                ctx.add_issue(
                    issue_for(
                        row,
                        "effective_until",
                        "ENROLLMENT_OVERLAP",
                        "修改后的选课有效期与同一课程开设、同一学生的其他选课重叠；请拆成不重叠的区间。",
                    )
                )
        obj.metadata["term_code"] = term_code
        ctx.graph.enrollments[enrollment_code] = obj
