"""Small write helpers shared by the import commit implementation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import models

from apps.academics.models import CourseOffering, TeacherProfile, TeachingAssignment, Term
from apps.imports.models import ImportBatch, ImportIdentity, ImportStatus
from apps.imports.parsing import make_issue
from apps.imports.projection.base import CommitResult, ImportObject, PreviewResult
from apps.scheduling.changelog import ChangeEntry
from apps.scheduling.models import ChangeEntityType


def make_identity(batch: ImportBatch, obj: ImportObject, object_id: UUID) -> None:
    ImportIdentity.objects.create(
        school_id=batch.school_id,
        source_system=batch.source_system,
        entity_type=obj.entity_type,
        external_id=obj.external_id,
        object_id=object_id,
        batch=batch,
    )


def persist_new(obj: ImportObject) -> models.Model:
    instance = obj.data
    instance.save(force_insert=True)
    return instance


def apply_changes(instance: models.Model, changes, *, allowed: set[str] | None = None) -> list[str]:
    update_fields: list[str] = []
    for difference in changes:
        if allowed is not None and difference.field not in allowed:
            continue
        setattr(instance, difference.field, difference.expected)
        update_fields.append(difference.field)
    if not update_fields:
        return []
    instance.save(update_fields=[*update_fields, "updated_at"])
    return update_fields


def create_teaching_assignments(
    obj: ImportObject,
    term: Term,
    teacher_lookup: dict[str, TeacherProfile],
) -> None:
    offering = obj.data
    if not isinstance(offering, CourseOffering):
        return
    until = term.end_date + timedelta(days=1)
    teacher_codes = obj.metadata.get("teacher_codes", [])
    lead_code = obj.metadata.get("lead_teacher_code")
    for code in teacher_codes:
        teacher = teacher_lookup.get(code)
        if teacher is None:
            continue
        TeachingAssignment.objects.create(
            offering=offering,
            teacher=teacher,
            role="lead" if code == lead_code else "teacher",
            effective_from=term.start_date,
            effective_until=until,
        )


def assignment_teacher_ids(graph) -> set[UUID]:
    ids: set[UUID] = set()
    for obj in graph.offerings.values():
        for change in obj.metadata.get("assignment_changes", []):
            ids.add(change.teacher_id)
    return ids


def queue_change(changes: dict[tuple[str, UUID], ChangeEntry], entry: ChangeEntry) -> None:
    changes[(entry.entity_type, entry.entity_id)] = entry


def add_session_display_changes(
    changes: dict[tuple[str, UUID], ChangeEntry],
    queryset,
) -> None:
    for session_id, offering_id, version in queryset.values_list("id", "offering_id", "version"):
        queue_change(
            changes,
            ChangeEntry(
                entity_type=ChangeEntityType.SESSION,
                entity_id=session_id,
                version=version,
                offering_id=offering_id,
            ),
        )


def apply_assignment_changes(
    offering: CourseOffering,
    term: Term | None,
    obj: ImportObject,
) -> set[UUID]:
    bumped: set[UUID] = set()
    for change in obj.metadata.get("assignment_changes", []):
        if change.action == "added":
            effective_until = change.effective_until
            if effective_until is None and term is not None:
                effective_until = term.end_date + timedelta(days=1)
            existing = TeachingAssignment.objects.filter(
                offering_id=offering.pk,
                teacher_id=change.teacher_id,
                effective_from=change.effective_from,
            ).first()
            if existing is None:
                TeachingAssignment.objects.create(
                    offering_id=offering.pk,
                    teacher_id=change.teacher_id,
                    role=change.new_role,
                    effective_from=change.effective_from,
                    effective_until=effective_until,
                )
            else:
                existing.role = change.new_role or existing.role
                existing.effective_until = effective_until
                existing.save(update_fields=["role", "effective_until", "updated_at"])
        elif change.action == "role_changed":
            assignment = TeachingAssignment.objects.filter(pk=change.assignment_id).first()
            if assignment is not None and assignment.role != change.new_role:
                assignment.role = change.new_role
                assignment.save(update_fields=["role", "updated_at"])
        elif change.action == "ended":
            assignment = TeachingAssignment.objects.filter(pk=change.assignment_id).first()
            if assignment is None:
                continue
            if change.delete_unstarted:
                assignment.delete()
            else:
                assignment.effective_until = change.effective_until
                assignment.save(update_fields=["effective_until", "updated_at"])
        bumped.add(change.teacher_id)
    return bumped


def dedupe_issue_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for item in items:
        key = (
            item.get("file"),
            item.get("sheet"),
            item.get("row"),
            item.get("field"),
            item.get("code"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def stale_result(
    batch: ImportBatch,
    preview: PreviewResult,
    baseline_errors: list[dict],
    *,
    code: str = "PREVIEW_STALE",
    message: str = "预览之后相关数据发生变化；请重新预览并确认，旧预览不能提交。",
) -> CommitResult:
    stale = make_issue(
        file="",
        sheet="",
        row=0,
        field="",
        value="",
        code=code,
        message=message,
    )
    batch.status = ImportStatus.UPLOADED
    batch.row_counts = preview.row_counts
    batch.errors_json = dedupe_issue_dicts(
        [*baseline_errors, *[issue.as_dict() for issue in preview.issues], stale.as_dict()]
    )
    batch.preview_base_schedule_version = None
    batch.preview_fingerprint = ""
    batch.change_summary = {}
    batch.save(
        update_fields=[
            "status",
            "row_counts",
            "errors_json",
            "change_summary",
            "preview_base_schedule_version",
            "preview_fingerprint",
            "updated_at",
        ]
    )
    return CommitResult(
        batch=batch,
        committed=False,
        code=code,
        message=stale.message,
    )


def mark_failed(batch: ImportBatch, exc: Exception) -> None:
    batch.refresh_from_db()
    error = make_issue(
        file="",
        sheet="",
        row=0,
        field="",
        value=type(exc).__name__,
        code="COMMIT_FAILED",
        message=f"提交事务已整体回滚：{str(exc)[:500]}",
    )
    batch.status = ImportStatus.FAILED
    batch.errors_json = [*(batch.errors_json or []), error.as_dict()]
    batch.save(update_fields=["status", "errors_json", "updated_at"])
