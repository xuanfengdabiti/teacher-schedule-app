"""Import preview orchestration and persisted preview statistics."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.utils import timezone as django_timezone

from apps.academics.models import TeacherProfile, TeachingAssignment, Term
from apps.imports.models import ImportBatch, ImportStatus
from apps.imports.parsing import ImportIssue, ParseResult, make_issue, parse_files
from apps.imports.projection import build_graph
from apps.imports.projection.base import (
    CHANGED,
    ENTITY_OFFERING,
    ENTITY_TERM,
    ERROR,
    NEW,
    UNCHANGED,
    BuildContext,
    ExistingLookup,
    ImportGraph,
    PreviewResult,
    display,
)
from apps.imports.projection.planning import PlanData, referenced_term_codes
from apps.imports.storage import batch_file_entries, hash_named_contents
from apps.imports.validation import validate_business_rows
from apps.scheduling import selectors
from apps.scheduling.models import SessionStatus
from apps.teaching.models import TeachingPlan


def dedupe_issues(issues: list[ImportIssue]) -> list[ImportIssue]:
    result: list[ImportIssue] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for issue in issues:
        key = (issue.file, issue.sheet, issue.row, issue.field, issue.code)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    result.sort(key=lambda item: (item.file, item.sheet, item.row, item.field, item.code, item.message))
    return result


def parse_batch_files(batch: ImportBatch) -> tuple[ParseResult, list[ImportIssue], str]:
    entries = batch_file_entries(batch)
    issues: list[ImportIssue] = []
    present_entries: list[tuple[str, Path]] = []
    hash_entries: list[tuple[str, bytes]] = []
    for display_name, path in entries:
        if not path.is_file():
            issues.append(
                make_issue(
                    file=display_name,
                    sheet="",
                    row=0,
                    field="",
                    value=display_name,
                    code="MISSING_FILE",
                    message="批次登记的原始文件在私有目录中不存在；请重新上传。",
                )
            )
            continue
        present_entries.append((display_name, path))
        hash_entries.append((display_name, path.read_bytes()))
    if not entries:
        issues.append(
            make_issue(
                file="",
                sheet="",
                row=0,
                field="",
                value="",
                code="NO_FILES",
                message="批次没有原始文件；请至少上传一个 CSV 或 XLSX。",
            )
        )
    parsed = parse_files(present_entries)
    file_hash = hash_named_contents(hash_entries) if hash_entries else ""
    return parsed, issues + parsed.issues, file_hash


def summary_for(graph: ImportGraph, plan: PlanData) -> dict[str, Any]:
    status_counts: dict[str, dict[str, int]] = {
        NEW: defaultdict(int),
        CHANGED: defaultdict(int),
        UNCHANGED: defaultdict(int),
        ERROR: defaultdict(int),
    }
    for obj in graph.objects():
        status_counts[obj.state][obj.entity_type] += 1
    return {
        "new_counts": dict(sorted(status_counts[NEW].items())),
        "changed_counts": dict(sorted(status_counts[CHANGED].items())),
        "unchanged_counts": dict(sorted(status_counts[UNCHANGED].items())),
        "error_counts": dict(sorted(status_counts[ERROR].items())),
        "expected_new_sessions": plan.expected_new,
    }


def estimated_hours(graph: ImportGraph) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for obj in graph.sessions.values():
        session = obj.data
        if obj.state == NEW and session.status == SessionStatus.COMPLETED and session.actual_hours is not None:
            offering_code = obj.metadata.get("offering_code")
            if offering_code:
                totals[offering_code] += session.actual_hours
    return {code: str(value) for code, value in sorted(totals.items())}


def row_counts(
    parsed: ParseResult,
    graph: ImportGraph,
    issues: list[ImportIssue],
) -> dict[str, dict[str, int]]:
    empty = {NEW: 0, CHANGED: 0, UNCHANGED: 0, ERROR: 0}
    counts: dict[str, dict[str, int]] = {}
    for parsed_file in parsed.files:
        counts[parsed_file.display_key] = dict(empty)
    for issue in issues:
        key = f"{issue.file} / {issue.sheet}" if issue.sheet else issue.file
        counts.setdefault(key, dict(empty))

    error_issues = [issue for issue in issues if issue.level == "error"]
    error_rows: set[str] = {f"{issue.file}\x1f{issue.sheet}\x1f{issue.row}" for issue in error_issues if issue.row > 0}
    for parsed_file in parsed.files:
        file_counts = counts.setdefault(parsed_file.display_key, dict(empty))
        for row in parsed_file.rows:
            if row.source_key in error_rows:
                file_counts[ERROR] += 1
                continue
            primary = graph.row_primary.get(row.source_key)
            if primary is None:
                if any(
                    issue.file == row.file and issue.sheet == row.sheet and issue.row == row.row_number
                    for issue in error_issues
                ):
                    file_counts[ERROR] += 1
                continue
            file_counts[primary.state] = file_counts.get(primary.state, 0) + 1

    for issue in issues:
        if issue.level != "error":
            continue
        key = f"{issue.file} / {issue.sheet}" if issue.sheet else issue.file
        file_counts = counts.setdefault(key, dict(empty))
        if issue.row == 0 or (issue.row == 1 and not parsed_file_has_rows(parsed, issue.file, issue.sheet)):
            if file_counts[ERROR] == 0:
                file_counts[ERROR] = 1
    return counts


def parsed_file_has_rows(parsed: ParseResult, file_name: str, sheet: str) -> bool:
    return any(
        parsed_file.file_name == file_name and parsed_file.sheet == sheet and parsed_file.rows
        for parsed_file in parsed.files
    )


def batch_term(ctx: BuildContext) -> Term | None:
    codes = referenced_term_codes(ctx.graph, ctx.batch)
    if len(codes) != 1:
        return None
    code = next(iter(codes))
    obj = ctx.graph.terms.get(code)
    if obj is not None:
        return obj.existing if isinstance(obj.existing, Term) else None
    target = ctx.existing.target(ENTITY_TERM, code)
    if isinstance(target, Term):
        return target
    if ctx.batch.term_id and ctx.batch.term is not None and ctx.batch.term.code == code:
        return ctx.batch.term
    return None


def preview_base(ctx: BuildContext) -> int | None:
    term = batch_term(ctx)
    return term.schedule_version if term is not None else None


def build_change_summary(graph: ImportGraph) -> dict[str, list[dict[str, Any]]]:
    fields: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    for obj in graph.objects():
        row = obj.rows[0] if obj.rows else None
        location = {
            "file": row.file if row else "",
            "sheet": row.sheet if row else "",
            "row": row.row_number if row else 0,
        }
        for difference in obj.changes:
            fields.append(
                {
                    **location,
                    "entity_type": obj.entity_type,
                    "object": f"{obj.entity_type}:{obj.external_id}",
                    "field": difference.field,
                    "old": display(difference.actual),
                    "new": display(difference.expected),
                }
            )
        if obj.entity_type != ENTITY_OFFERING:
            continue
        for change in obj.metadata.get("assignment_changes", []):
            assignments.append(
                {
                    **location,
                    "offering_code": change.offering_code,
                    "action": change.action,
                    "teacher_code": change.teacher_code,
                    "old_role": change.old_role or "",
                    "new_role": change.new_role or "",
                    "effective_from": change.effective_from.isoformat() if change.effective_from else "",
                    "effective_until": change.effective_until.isoformat() if change.effective_until else "",
                    "delete_unstarted": change.delete_unstarted,
                }
            )
        owner_change = obj.metadata.get("plan_owner_change")
        if owner_change:
            assignments.append(
                {
                    **location,
                    "offering_code": obj.external_id,
                    "action": "plan_owner_changed",
                    "teacher_code": owner_change["new_owner_code"],
                    "old_role": owner_change["old_owner_code"],
                    "new_role": owner_change["new_owner_code"],
                    "effective_from": "",
                    "effective_until": "",
                    "delete_unstarted": False,
                }
            )
    fields.sort(key=lambda item: (item["file"], item["sheet"], item["row"], item["object"], item["field"]))
    assignments.sort(
        key=lambda item: (
            item["file"],
            item["sheet"],
            item["row"],
            item["offering_code"],
            item["teacher_code"],
            item["action"],
        )
    )
    return {"fields": fields, "assignments": assignments}


def preview_fingerprint(
    *,
    batch: ImportBatch,
    graph: ImportGraph,
    row_counts: dict[str, dict[str, int]],
    issues: list[ImportIssue],
    change_summary: dict[str, list[dict[str, Any]]],
    base_version: int | None,
    file_hash: str,
    school_date: date,
) -> str:
    teacher_ids = [
        obj.data.pk
        for obj in graph.objects()
        if obj.entity_type == "teacher" and getattr(obj.data, "pk", None) is not None
    ]
    offering_ids = [
        obj.existing.pk
        for obj in graph.offerings.values()
        if obj.existing is not None and getattr(obj.existing, "pk", None) is not None
    ]
    term_ids = [obj.existing.pk for obj in graph.terms.values() if isinstance(obj.existing, Term)]
    payload = {
        "batch": str(batch.pk),
        "source_system": batch.source_system,
        "file_hash": file_hash,
        "base_version": base_version,
        "school_date": school_date.isoformat(),
        "row_counts": row_counts,
        "issues": [issue.as_dict() for issue in issues],
        "change_summary": change_summary,
        "terms": list(
            Term.objects.filter(pk__in=term_ids)
            .order_by("pk")
            .values("id", "name", "start_date", "end_date", "first_week_monday", "schedule_version")
        ),
        "objects": [
            {
                "entity_type": obj.entity_type,
                "external_id": obj.external_id,
                "state": obj.state,
                "pk": getattr(obj.existing, "pk", None),
                "changes": [
                    [difference.field, display(difference.actual), display(difference.expected)]
                    for difference in obj.changes
                ],
            }
            for obj in sorted(graph.objects(), key=lambda item: (item.entity_type, item.external_id))
        ],
        "teachers": list(
            TeacherProfile.objects.filter(pk__in=teacher_ids)
            .order_by("pk")
            .values(
                "id",
                "teacher_no",
                "formal_name",
                "display_name",
                "department",
                "email",
                "phone",
                "phone_visibility",
                "wechat",
                "wechat_visibility",
                "is_active",
                "user_id",
                "teacher_status",
                "assignment_scope_version",
            )
        ),
        "assignments": list(
            TeachingAssignment.objects.filter(offering_id__in=offering_ids)
            .order_by("pk")
            .values("id", "offering_id", "teacher_id", "role", "effective_from", "effective_until")
        ),
        "plans": list(
            TeachingPlan.objects.filter(offering_id__in=offering_ids)
            .order_by("pk")
            .values("id", "offering_id", "owner_id", "version", "status")
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_preview(
    batch: ImportBatch,
    *,
    now: datetime | None = None,
    school_date: date | None = None,
    persist: bool = True,
) -> PreviewResult:
    now = now or django_timezone.now()
    school_date = school_date or selectors.current_school_date()
    parsed, parse_issues, file_hash = parse_batch_files(batch)
    issues = list(parse_issues)
    issues.extend(validate_business_rows(parsed))

    ctx = BuildContext(
        batch=batch,
        parsed=parsed,
        existing=ExistingLookup.load(batch),
        graph=ImportGraph(),
        issues=issues,
    )
    plan = build_graph(ctx, now=now, school_date=school_date)

    final_issues = dedupe_issues(ctx.issues)
    counts = row_counts(parsed, ctx.graph, final_issues)
    summary = summary_for(ctx.graph, plan)
    hours = estimated_hours(ctx.graph)
    summary["estimated_hours_by_offering"] = hours
    base_version = preview_base(ctx)
    batch_term_value = batch_term(ctx)
    change_summary = build_change_summary(ctx.graph)
    fingerprint = preview_fingerprint(
        batch=batch,
        graph=ctx.graph,
        row_counts=counts,
        issues=final_issues,
        change_summary=change_summary,
        base_version=base_version,
        file_hash=file_hash,
        school_date=school_date,
    )

    if persist:
        batch.status = ImportStatus.PREVIEWED
        batch.row_counts = counts
        batch.errors_json = [issue.as_dict() for issue in final_issues]
        batch.change_summary = change_summary
        batch.preview_fingerprint = fingerprint
        batch.preview_base_schedule_version = base_version
        batch.term = batch_term_value
        if file_hash:
            batch.file_hash = file_hash
        batch.save(
            update_fields=[
                "status",
                "row_counts",
                "errors_json",
                "change_summary",
                "preview_fingerprint",
                "preview_base_schedule_version",
                "term",
                "file_hash",
                "updated_at",
            ]
        )

    return PreviewResult(
        batch=batch,
        issues=tuple(final_issues),
        row_counts=counts,
        summary=summary,
        expected_new_sessions=plan.expected_new,
        estimated_hours_by_offering=hours,
        preview_base_schedule_version=base_version,
        file_hash=file_hash,
        graph=ctx.graph,
        planned_sessions=tuple(plan.planned),
        now=now,
        school_date=school_date,
        change_summary=change_summary,
        fingerprint=fingerprint,
    )


def preview_batch(
    batch: ImportBatch,
    *,
    now: datetime | None = None,
    school_date: date | None = None,
) -> PreviewResult:
    """Parse, validate and persist the preview outcome on ``batch``."""
    if batch.status == ImportStatus.COMMITTED:
        raise ValueError("已提交的导入批次不能重新预览。")
    result = build_preview(batch, now=now, school_date=school_date)
    if result.has_errors:
        batch.status = ImportStatus.PREVIEWED
    return result
