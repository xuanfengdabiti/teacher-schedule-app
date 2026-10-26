"""Teacher and student projections."""

from __future__ import annotations

import uuid
from collections import defaultdict

from apps.academics.models import Student, TeacherProfile
from apps.accounts.models import normalize_user_email
from apps.imports.projection.base import (
    ENTITY_STUDENT,
    ENTITY_TEACHER,
    BuildContext,
    diff_fields,
    norm_optional,
    resolve_object,
)


def _keep_if_blank(incoming: str, current: str) -> str:
    return incoming or current


def build_teachers(ctx: BuildContext) -> None:
    for row in ctx.parsed.rows_for("teachers"):
        code = row.values.get("teacher_code")
        if not code or code in ctx.graph.teachers:
            continue
        natural = TeacherProfile.objects.filter(school_id=ctx.batch.school_id, teacher_no=code).first()
        identity = ctx.existing.identity(ENTITY_TEACHER, code)
        target = ctx.existing.target(ENTITY_TEACHER, code) if identity else None
        existing = target if isinstance(target, TeacherProfile) else None
        linked = bool(existing and existing.user_id)

        department = norm_optional(row.values.get("department"))
        email = normalize_user_email(row.values.get("email") or "")
        phone = norm_optional(row.values.get("phone"))
        wechat = norm_optional(row.values.get("wechat"))
        phone_visibility = row.values.get("phone_visibility") or "private"
        wechat_visibility = row.values.get("wechat_visibility") or "private"
        if existing is not None:
            department = _keep_if_blank(department, existing.department)
            email = _keep_if_blank(email, existing.email)
            if linked:
                # Contact details belong to the teacher account once linked.
                phone = existing.phone
                phone_visibility = existing.phone_visibility
                wechat = existing.wechat
                wechat_visibility = existing.wechat_visibility
            else:
                phone = _keep_if_blank(phone, existing.phone)
                wechat = _keep_if_blank(wechat, existing.wechat)

        data = TeacherProfile(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            teacher_no=code,
            formal_name=row.values.get("formal_name") or "",
            department=department,
            email=email,
            phone=phone,
            phone_visibility=phone_visibility,
            wechat=wechat,
            wechat_visibility=wechat_visibility,
            is_active=bool(row.values.get("is_active")),
        )
        updates = []
        if existing is not None:
            updates = diff_fields(
                existing,
                {
                    "formal_name": data.formal_name,
                    "department": data.department,
                    "email": data.email,
                    "phone": data.phone,
                    "phone_visibility": data.phone_visibility,
                    "wechat": data.wechat,
                    "wechat_visibility": data.wechat_visibility,
                    "is_active": data.is_active,
                },
                [
                    "formal_name",
                    "department",
                    "email",
                    "phone",
                    "phone_visibility",
                    "wechat",
                    "wechat_visibility",
                    "is_active",
                ],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_TEACHER,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            updates=updates,
        )
        ctx.graph.teachers[code] = obj


def build_students(ctx: BuildContext) -> None:
    grouped: dict[str, list] = defaultdict(list)
    for row in ctx.parsed.rows_for("enrollments"):
        code = row.values.get("student_code")
        if code:
            grouped[code].append(row)
    for code, rows in grouped.items():
        data = Student(id=uuid.uuid4(), school_id=ctx.batch.school_id, student_no=code)
        natural = Student.objects.filter(school_id=ctx.batch.school_id, student_no=code).first()
        identity = ctx.existing.identity(ENTITY_STUDENT, code)
        target = ctx.existing.target(ENTITY_STUDENT, code) if identity else None
        differences = []
        if isinstance(target, Student):
            differences = diff_fields(target, {"student_no": code}, ["student_no"])
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_STUDENT,
            external_id=code,
            data=data,
            rows=rows,
            natural=natural,
            differences=differences,
        )
        ctx.graph.students[code] = obj
