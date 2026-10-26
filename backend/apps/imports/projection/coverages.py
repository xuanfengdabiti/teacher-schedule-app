"""Data-coverage declaration projections."""

from __future__ import annotations

import uuid
from collections import defaultdict

from apps.academics.models import CoverageCompleteness, DataCoverage
from apps.imports.parsing import ParsedRow
from apps.imports.projection.base import (
    COVERAGE_SCOPES,
    ENTITY_COVERAGE,
    ERROR,
    BuildContext,
    classroom_ref,
    diff_fields,
    issue_for,
    offering_ref,
    resolve_object,
    student_ref,
    teacher_ref,
    term_ref,
)


def coverage_scope_issue(ctx: BuildContext, row: ParsedRow, term_code: str, kind: str, scope_key: str) -> bool:
    if scope_key == "school":
        return False
    allowed = COVERAGE_SCOPES.get(kind, set())
    if ":" not in scope_key:
        ctx.add_issue(
            issue_for(
                row,
                "scope_key",
                "SCOPE_NOT_FOUND",
                "scope_key 必须是 school 或 kind 允许的资源范围，例如 offering:开课编号。",
            )
        )
        return False
    prefix, value = scope_key.split(":", 1)
    if prefix not in allowed or not value:
        ctx.add_issue(
            issue_for(
                row,
                "scope_key",
                "SCOPE_NOT_FOUND",
                f"coverage.kind={kind} 不支持范围键 {scope_key}。",
            )
        )
        return False
    if prefix == "offering" and offering_ref(ctx, term_code, value) is None:
        ctx.add_issue(issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的开课编号 {value} 不存在。"))
        return False
    if prefix == "teacher" and teacher_ref(ctx, value) is None:
        ctx.add_issue(issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的教师编号 {value} 不存在。"))
        return False
    if prefix == "student" and student_ref(ctx, value) is None:
        ctx.add_issue(issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的学生编号 {value} 不存在。"))
        return False
    if prefix == "classroom" and classroom_ref(ctx, value) is None:
        ctx.add_issue(issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的教室编号 {value} 不存在。"))
        return False
    return True


def build_coverages(ctx: BuildContext) -> None:
    coverage_rows: list[ParsedRow] = []
    for row in ctx.parsed.rows_for("coverage"):
        code = row.values.get("coverage_code")
        term_code = row.values.get("term_code")
        kind = row.values.get("kind")
        scope_key = row.values.get("scope_key")
        if not code or not term_code or not kind or not scope_key or code in ctx.graph.coverages:
            continue
        term = term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        coverage_scope_issue(ctx, row, term_code, kind, scope_key)
        data = DataCoverage(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            term_id=term.pk,
            kind=kind,
            scope_key=scope_key,
            from_date=row.values.get("from_date"),
            until_date=row.values.get("until_date"),
            completeness=row.values.get("completeness") or "",
            source_updated_at=row.values.get("source_updated_at"),
            confirmation_ref=row.values.get("confirmation_ref") or "",
        )
        natural = (
            DataCoverage.objects.filter(
                term_id=term.pk,
                kind=kind,
                scope_key=scope_key,
                from_date=data.from_date,
                until_date=data.until_date,
            ).first()
            if term.pk and data.from_date and data.until_date
            else None
        )
        identity = ctx.existing.identity(ENTITY_COVERAGE, code)
        target = ctx.existing.target(ENTITY_COVERAGE, code) if identity else None
        unsupported = []
        updates = []
        if isinstance(target, DataCoverage):
            unsupported = diff_fields(
                target,
                {
                    "term_id": data.term_id,
                    "kind": data.kind,
                    "scope_key": data.scope_key,
                },
                ["term_id", "kind", "scope_key"],
            )
            updates = diff_fields(
                target,
                {
                    "from_date": data.from_date,
                    "until_date": data.until_date,
                    "completeness": data.completeness,
                    "source_updated_at": data.source_updated_at,
                    "confirmation_ref": data.confirmation_ref,
                },
                [
                    "from_date",
                    "until_date",
                    "completeness",
                    "source_updated_at",
                    "confirmation_ref",
                ],
            )
        obj = resolve_object(
            ctx,
            entity_type=ENTITY_COVERAGE,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=unsupported,
            updates=updates,
        )
        if (
            isinstance(target, DataCoverage)
            and target.completeness in {CoverageCompleteness.PARTIAL, CoverageCompleteness.MISSING}
            and data.completeness == CoverageCompleteness.COMPLETE
            and not unsupported
        ):
            ctx.add_issue(
                issue_for(
                    row,
                    "completeness",
                    "COVERAGE_COMPLETENESS_UPGRADE",
                    (
                        f"原覆盖声明为 {target.completeness}，本次改为 complete。"
                        "新来源不能静默覆盖学校已明确的缺口；请确认 confirmation_ref 依据充分，本提示不阻止提交。"
                    ),
                    level="warning",
                )
            )
        if data.term_id and data.kind and data.scope_key and data.from_date and data.until_date and not unsupported:
            overlap_query = DataCoverage.objects.filter(
                term_id=data.term_id,
                kind=data.kind,
                scope_key=data.scope_key,
                from_date__lt=data.until_date,
                until_date__gt=data.from_date,
            )
            if isinstance(target, DataCoverage):
                overlap_query = overlap_query.exclude(pk=target.pk)
            overlap = overlap_query.exists()
            if overlap:
                obj.state = ERROR
                ctx.add_issue(
                    issue_for(
                        row,
                        "from_date",
                        "COVERAGE_OVERLAP",
                        "该覆盖声明与数据库既有声明的有效期重叠；请明确替换或裁剪区间。",
                    )
                )
        obj.metadata["term_code"] = term_code
        ctx.graph.coverages[code] = obj
        coverage_rows.append(row)

    by_scope: dict[tuple[str, str, str], list[tuple[ParsedRow, DataCoverage]]] = defaultdict(list)
    for row in coverage_rows:
        code = row.values.get("coverage_code")
        obj = ctx.graph.coverages.get(code)
        if obj is None or not isinstance(obj.data, DataCoverage):
            continue
        data = obj.data
        if data.term_id and data.kind and data.scope_key and data.from_date and data.until_date:
            by_scope[(str(data.term_id), data.kind, data.scope_key)].append((row, data))
    for group in by_scope.values():
        for index, (left_row, left) in enumerate(group):
            for right_row, right in group[index + 1 :]:
                if left.from_date < right.until_date and right.from_date < left.until_date:
                    ctx.add_issue(
                        issue_for(
                            left_row,
                            "from_date",
                            "COVERAGE_OVERLAP",
                            "同一学期、kind 与 scope_key 的覆盖声明有效期重叠；请明确替换或裁剪区间。",
                        )
                    )
                    ctx.add_issue(
                        issue_for(
                            right_row,
                            "from_date",
                            "COVERAGE_OVERLAP",
                            "同一学期、kind 与 scope_key 的覆盖声明有效期重叠；请明确替换或裁剪区间。",
                        )
                    )
