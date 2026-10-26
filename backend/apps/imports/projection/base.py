"""Shared import projection types and resolution helpers.

Projection modules build an in-memory graph of typed source rows without writing
formal business tables.  Preview and commit both consume the same graph so a
second validation under the write locks cannot silently use a different merge
decision.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from django.db import models

from apps.academics.models import (
    CalendarException,
    Course,
    CourseOffering,
    DataCoverage,
    Enrollment,
    Period,
    Student,
    TeacherProfile,
    TeachingAssignment,
    TeachingClass,
    Term,
)
from apps.classrooms.models import Classroom, RoomBlock
from apps.imports.models import ImportBatch, ImportIdentity
from apps.imports.parsing import ImportIssue, ParsedRow, ParseResult, make_issue
from apps.scheduling import calendar
from apps.scheduling.models import ClassSession, PersonBlock, ScheduleRule

NEW = "new"
CHANGED = "changed"
UNCHANGED = "unchanged"
ERROR = "error"

ENTITY_TERM = "term"
ENTITY_PERIOD = "period"
ENTITY_TEACHER = "teacher"
ENTITY_STUDENT = "student"
ENTITY_CLASSROOM = "classroom"
ENTITY_COURSE = "course"
ENTITY_TEACHING_CLASS = "teaching_class"
ENTITY_OFFERING = "offering"
ENTITY_ENROLLMENT = "enrollment"
ENTITY_CALENDAR_EXCEPTION = "calendar_exception"
ENTITY_RULE = "rule"
ENTITY_SESSION = "session"
ENTITY_ROOM_BLOCK = "room_block"
ENTITY_PERSON_BLOCK = "person_block"
ENTITY_COVERAGE = "coverage"

MODEL_BY_ENTITY: dict[str, type[models.Model]] = {
    ENTITY_TERM: Term,
    ENTITY_PERIOD: Period,
    ENTITY_TEACHER: TeacherProfile,
    ENTITY_STUDENT: Student,
    ENTITY_CLASSROOM: Classroom,
    ENTITY_COURSE: Course,
    ENTITY_TEACHING_CLASS: TeachingClass,
    ENTITY_OFFERING: CourseOffering,
    ENTITY_ENROLLMENT: Enrollment,
    ENTITY_CALENDAR_EXCEPTION: CalendarException,
    ENTITY_RULE: ScheduleRule,
    ENTITY_SESSION: ClassSession,
    ENTITY_ROOM_BLOCK: RoomBlock,
    ENTITY_PERSON_BLOCK: PersonBlock,
    ENTITY_COVERAGE: DataCoverage,
}

COVERAGE_SCOPES: dict[str, set[str]] = {
    "calendar": {"school"},
    "teacher_assignments": {"school", "offering"},
    "teacher_schedule": {"school", "teacher"},
    "student_enrollments": {"school", "offering"},
    "student_schedule": {"school", "student"},
    "classroom_schedule": {"school", "classroom"},
    "room_blocks": {"school", "classroom"},
    "person_blocks": {"school", "teacher", "student"},
}


@dataclass
class ImportObject:
    entity_type: str
    external_id: str
    data: models.Model
    rows: list[ParsedRow] = field(default_factory=list)
    state: str = NEW
    identity: ImportIdentity | None = None
    existing: models.Model | None = None
    planned_key: tuple[Any, ...] | None = None
    generated_session: bool = False
    changes: list[FieldDifference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FieldDifference:
    field: str
    expected: Any
    actual: Any


@dataclass(frozen=True)
class AssignmentChange:
    """One planned teaching-scope mutation, materialised during commit."""

    action: str
    teacher_code: str
    teacher_id: UUID
    offering_id: UUID
    offering_code: str
    old_role: str | None = None
    new_role: str | None = None
    assignment_id: UUID | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    delete_unstarted: bool = False


@dataclass
class ImportGraph:
    terms: dict[str, ImportObject] = field(default_factory=dict)
    periods: dict[tuple[str, int], ImportObject] = field(default_factory=dict)
    teachers: dict[str, ImportObject] = field(default_factory=dict)
    students: dict[str, ImportObject] = field(default_factory=dict)
    classrooms: dict[str, ImportObject] = field(default_factory=dict)
    courses: dict[str, ImportObject] = field(default_factory=dict)
    teaching_classes: dict[tuple[str, str], ImportObject] = field(default_factory=dict)
    offerings: dict[tuple[str, str], ImportObject] = field(default_factory=dict)
    enrollments: dict[str, ImportObject] = field(default_factory=dict)
    calendar_exceptions: dict[str, ImportObject] = field(default_factory=dict)
    rules: dict[tuple[str, str], ImportObject] = field(default_factory=dict)
    sessions: dict[str, ImportObject] = field(default_factory=dict)
    room_blocks: dict[str, ImportObject] = field(default_factory=dict)
    person_blocks: dict[str, ImportObject] = field(default_factory=dict)
    coverages: dict[str, ImportObject] = field(default_factory=dict)
    by_entity: dict[str, dict[str, ImportObject]] = field(default_factory=lambda: defaultdict(dict))
    row_primary: dict[str, ImportObject] = field(default_factory=dict)

    def add(self, obj: ImportObject) -> ImportObject:
        mapping = self.by_entity[obj.entity_type]
        mapping.setdefault(obj.external_id, obj)
        return obj

    def objects(self) -> list[ImportObject]:
        result: list[ImportObject] = []
        seen: set[tuple[str, str]] = set()
        for mapping in self.by_entity.values():
            for obj in mapping.values():
                key = (obj.entity_type, obj.external_id)
                if key in seen:
                    continue
                seen.add(key)
                result.append(obj)
        return result


@dataclass(frozen=True)
class PreviewResult:
    batch: ImportBatch
    issues: tuple[ImportIssue, ...]
    row_counts: dict[str, dict[str, int]]
    summary: dict[str, Any]
    expected_new_sessions: int
    estimated_hours_by_offering: dict[str, str]
    preview_base_schedule_version: int | None
    file_hash: str
    graph: ImportGraph
    planned_sessions: tuple[calendar.PlannedSession, ...]
    now: datetime
    school_date: date
    change_summary: dict[str, Any]
    fingerprint: str

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [issue.as_dict() for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [issue.as_dict() for issue in self.issues if issue.level == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(issue.level == "error" for issue in self.issues)


@dataclass(frozen=True)
class CommitResult:
    batch: ImportBatch
    committed: bool
    code: str = ""
    message: str = ""
    created: int = 0
    changed: int = 0
    sessions_generated: int = 0
    sessions_matched: int = 0
    sessions_changed: int = 0
    unchanged: int = 0


class ImportCommitError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def issue_for(
    row: ParsedRow,
    field_name: str,
    code: str,
    message: str,
    *,
    value: Any = None,
    level: str = "error",
) -> ImportIssue:
    raw = row.values.get(field_name) if value is None else value
    return make_issue(
        file=row.file,
        sheet=row.sheet,
        row=row.row_number,
        field=field_name,
        value=raw,
        code=code,
        message=message,
        level=level,
    )


def issues_for_rows(
    rows: list[ParsedRow],
    field_name: str,
    code: str,
    message: str,
    *,
    value: Any = None,
    level: str = "error",
) -> list[ImportIssue]:
    return [issue_for(row, field_name, code, message, value=value, level=level) for row in rows]


def display(value: Any) -> str:
    if value is None:
        return "空"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def difference_issue(row: ParsedRow, difference: FieldDifference) -> ImportIssue:
    return issue_for(
        row,
        difference.field,
        "CHANGE_NOT_SUPPORTED_YET",
        (
            f"数据库字段 {difference.field} 的当前值为“{display(difference.actual)}”，"
            f"本批次为“{display(difference.expected)}”。该字段不在 B12b 允许的基础资料"
            "增量更新表内，整批不能提交；会影响课表的变更留待 B12c。"
        ),
        value=difference.expected,
    )


def diff(field_name: str, expected: Any, actual: Any) -> FieldDifference | None:
    if expected != actual:
        return FieldDifference(field=field_name, expected=expected, actual=actual)
    return None


def diff_fields(model: models.Model, expected: dict[str, Any], fields: list[str]) -> list[FieldDifference]:
    differences: list[FieldDifference] = []
    for field_name in fields:
        difference = diff(field_name, expected.get(field_name), getattr(model, field_name))
        if difference is not None:
            differences.append(difference)
    return differences


def norm_optional(value: Any) -> str:
    return value or ""


def identity_key(entity_type: str, external_id: str) -> tuple[str, str]:
    return entity_type, external_id


def external_id_for_period(term_code: str, period_number: int) -> str:
    return f"{term_code}:{period_number}"


def external_id_for_teaching_class(term_code: str, class_code: str) -> str:
    return f"{term_code}:{class_code}"


def chunks(values: list[UUID], size: int = 500) -> list[list[UUID]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


@dataclass
class ExistingLookup:
    identities: dict[tuple[str, str], ImportIdentity]
    targets: dict[tuple[str, str], models.Model]

    @classmethod
    def load(cls, batch: ImportBatch) -> ExistingLookup:
        identities = {
            (identity.entity_type, identity.external_id): identity
            for identity in ImportIdentity.objects.filter(
                school_id=batch.school_id,
                source_system=batch.source_system,
            )
        }
        targets: dict[tuple[str, str], models.Model] = {}
        by_entity: dict[str, list[ImportIdentity]] = defaultdict(list)
        for identity in identities.values():
            if identity.entity_type in MODEL_BY_ENTITY:
                by_entity[identity.entity_type].append(identity)
        for entity_type, entity_identities in by_entity.items():
            model = MODEL_BY_ENTITY[entity_type]
            for chunk in chunks([identity.object_id for identity in entity_identities]):
                targets_by_id = {target.pk: target for target in model.objects.filter(pk__in=chunk)}
                for identity in entity_identities:
                    target = targets_by_id.get(identity.object_id)
                    if target is not None:
                        targets[(entity_type, identity.external_id)] = target
        return cls(identities=identities, targets=targets)

    def identity(self, entity_type: str, external_id: str) -> ImportIdentity | None:
        return self.identities.get(identity_key(entity_type, external_id))

    def target(self, entity_type: str, external_id: str) -> models.Model | None:
        return self.targets.get(identity_key(entity_type, external_id))


@dataclass
class BuildContext:
    batch: ImportBatch
    parsed: ParseResult
    existing: ExistingLookup
    graph: ImportGraph
    issues: list[ImportIssue]

    def add_issue(self, issue: ImportIssue) -> None:
        self.issues.append(issue)

    def add_issues(self, issues: list[ImportIssue]) -> None:
        self.issues.extend(issues)


def resolve_object(
    ctx: BuildContext,
    *,
    entity_type: str,
    external_id: str,
    data: models.Model,
    rows: list[ParsedRow],
    natural: models.Model | None,
    differences: list[FieldDifference] | None = None,
    updates: list[FieldDifference] | None = None,
) -> ImportObject:
    identity = ctx.existing.identity(entity_type, external_id)
    target = ctx.existing.target(entity_type, external_id) if identity else None
    obj = ImportObject(
        entity_type=entity_type,
        external_id=external_id,
        data=data,
        rows=rows,
        identity=identity,
        existing=target,
    )

    if identity is not None:
        if target is None:
            obj.state = ERROR
            ctx.add_issues(
                issues_for_rows(
                    rows,
                    "external_id",
                    "IDENTITY_TARGET_MISSING",
                    f"来源映射 {entity_type}:{external_id} 指向的本地对象已不存在；请修复数据或建立新的来源映射。",
                    value=external_id,
                )
            )
        else:
            data.pk = target.pk
            differences = differences if differences is not None else []
            updates = updates if updates is not None else []
            if differences:
                obj.state = ERROR
                for difference in differences:
                    for row in rows:
                        ctx.add_issue(difference_issue(row, difference))
            elif updates:
                obj.state = CHANGED
                obj.changes = updates
            else:
                obj.state = UNCHANGED
    elif natural is not None:
        obj.state = ERROR
        ctx.add_issues(
            issues_for_rows(
                rows,
                "external_id",
                "CHANGE_NOT_SUPPORTED_YET",
                (
                    f"数据库已有编号 {external_id} 的 {entity_type} 对象，但没有同来源的身份映射。"
                    "merge 不按编号或名称静默合并，也不更新已有对象；请清理冲突对象或"
                    "先建立明确来源映射。"
                ),
                value=external_id,
            )
        )
    else:
        obj.state = NEW

    ctx.graph.add(obj)
    for row in rows:
        ctx.graph.row_primary.setdefault(row.source_key, obj)
    return obj


def term_ref(ctx: BuildContext, code: str) -> Term | None:
    obj = ctx.graph.terms.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_TERM, code)
    if isinstance(target, Term):
        return target
    if ctx.batch.term_id and ctx.batch.term is not None and ctx.batch.term.code == code:
        return ctx.batch.term
    return None


def teacher_ref(ctx: BuildContext, code: str) -> TeacherProfile | None:
    obj = ctx.graph.teachers.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_TEACHER, code)
    if isinstance(target, TeacherProfile):
        return target
    return None


def student_ref(ctx: BuildContext, code: str) -> Student | None:
    obj = ctx.graph.students.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_STUDENT, code)
    if isinstance(target, Student):
        return target
    return None


def classroom_ref(ctx: BuildContext, code: str) -> Classroom | None:
    obj = ctx.graph.classrooms.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_CLASSROOM, code)
    if isinstance(target, Classroom):
        return target
    return None


def offering_ref(ctx: BuildContext, term_code: str, offering_code: str) -> CourseOffering | None:
    obj = ctx.graph.offerings.get((term_code, offering_code))
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_OFFERING, offering_code)
    if isinstance(target, CourseOffering) and target.term.code == term_code:
        return target
    return None


def rule_ref(ctx: BuildContext, term_code: str, rule_code: str) -> ScheduleRule | None:
    obj = ctx.graph.rules.get((term_code, rule_code))
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_RULE, rule_code)
    if isinstance(target, ScheduleRule) and target.offering.term.code == term_code:
        return target
    return None


def period_exists(ctx: BuildContext, term: Term, number: int) -> bool:
    if (term.code, number) in ctx.graph.periods:
        return True
    if term.pk is None:
        return False
    return Period.objects.filter(term_id=term.pk, period_number=number).exists()


def offering_scope(ctx: BuildContext, offering: CourseOffering) -> tuple[set[UUID], UUID | None]:
    for obj in ctx.graph.offerings.values():
        if obj.data.pk == offering.pk:
            teacher_ids = {
                teacher_ref(ctx, code).pk
                for code in obj.metadata.get("teacher_codes", [])
                if teacher_ref(ctx, code) is not None
            }
            lead = teacher_ref(ctx, obj.metadata.get("lead_teacher_code", ""))
            return teacher_ids, lead.pk if lead is not None else None
    if offering.pk is None:
        return set(), None
    assignments = list(TeachingAssignment.objects.filter(offering_id=offering.pk))
    return {assignment.teacher_id for assignment in assignments}, (
        next((assignment.teacher_id for assignment in assignments if assignment.role == "lead"), None)
    )
