"""Import preview and commit services for the B12a scope.

This module intentionally supports only two commit outcomes:

* every source object is new (a first import for that school/term), or
* every source object is byte-for-byte equal in domain fields (a repeated
  import, which writes no business rows and no change log).

Any existing object whose mapped fields differ is reported as
``CHANGE_NOT_SUPPORTED_YET``.  B12b owns updates, snapshots and human decisions.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone as django_timezone

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
from apps.accounts.models import normalize_user_email
from apps.classrooms.models import Classroom, RoomBlock
from apps.imports.models import ImportBatch, ImportIdentity, ImportStatus
from apps.imports.parsing import ImportIssue, ParsedRow, ParseResult, make_issue, parse_files
from apps.imports.storage import batch_file_entries, hash_named_contents
from apps.imports.validation import validate_business_rows
from apps.scheduling import calendar
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import (
    ChangeEntityType,
    ClassSession,
    PersonBlock,
    ScheduleRule,
    SchoolSyncState,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.services import generate_sessions_for_term

NEW = "new"
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
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FieldDifference:
    field: str
    expected: Any
    actual: Any


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

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [issue.as_dict() for issue in self.issues]

    @property
    def has_errors(self) -> bool:
        return bool(self.issues)


@dataclass(frozen=True)
class CommitResult:
    batch: ImportBatch
    committed: bool
    code: str = ""
    message: str = ""
    created: int = 0
    sessions_generated: int = 0
    sessions_matched: int = 0
    sessions_changed: int = 0
    unchanged: int = 0


class ImportCommitError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _issue_for(
    row: ParsedRow,
    field_name: str,
    code: str,
    message: str,
    *,
    value: Any = None,
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
    )


def _issues_for_rows(
    rows: list[ParsedRow],
    field_name: str,
    code: str,
    message: str,
    *,
    value: Any = None,
) -> list[ImportIssue]:
    return [_issue_for(row, field_name, code, message, value=value) for row in rows]


def _display(value: Any) -> str:
    if value is None:
        return "空"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def _difference_issue(row: ParsedRow, difference: FieldDifference) -> ImportIssue:
    return _issue_for(
        row,
        difference.field,
        "CHANGE_NOT_SUPPORTED_YET",
        (
            f"数据库字段 {difference.field} 的当前值为“{_display(difference.actual)}”，"
            f"本批次为“{_display(difference.expected)}”。B12a 只支持全新对象或完全相同内容；"
            "字段更新和人工决定项将在 B12b 实现。"
        ),
        value=difference.expected,
    )


def _diff(field_name: str, expected: Any, actual: Any) -> FieldDifference | None:
    if expected != actual:
        return FieldDifference(field=field_name, expected=expected, actual=actual)
    return None


def _diff_fields(model: models.Model, expected: dict[str, Any], fields: list[str]) -> list[FieldDifference]:
    differences: list[FieldDifference] = []
    for field_name in fields:
        diff = _diff(field_name, expected.get(field_name), getattr(model, field_name))
        if diff is not None:
            differences.append(diff)
    return differences


def _norm_optional(value: Any) -> str:
    return value or ""


def _identity_key(entity_type: str, external_id: str) -> tuple[str, str]:
    return entity_type, external_id


def external_id_for_period(term_code: str, period_number: int) -> str:
    return f"{term_code}:{period_number}"


def external_id_for_teaching_class(term_code: str, class_code: str) -> str:
    return f"{term_code}:{class_code}"


def _chunks(values: list[UUID], size: int = 500) -> list[list[UUID]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


@dataclass
class _ExistingLookup:
    identities: dict[tuple[str, str], ImportIdentity]
    targets: dict[tuple[str, str], models.Model]

    @classmethod
    def load(cls, batch: ImportBatch) -> _ExistingLookup:
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
            for chunk in _chunks([identity.object_id for identity in entity_identities]):
                for target in model.objects.filter(pk__in=chunk):
                    matching = next(
                        (identity for identity in entity_identities if identity.object_id == target.pk),
                        None,
                    )
                    if matching is not None:
                        targets[(entity_type, matching.external_id)] = target
        return cls(identities=identities, targets=targets)

    def identity(self, entity_type: str, external_id: str) -> ImportIdentity | None:
        return self.identities.get(_identity_key(entity_type, external_id))

    def target(self, entity_type: str, external_id: str) -> models.Model | None:
        return self.targets.get(_identity_key(entity_type, external_id))


@dataclass
class _BuildContext:
    batch: ImportBatch
    parsed: ParseResult
    existing: _ExistingLookup
    graph: ImportGraph
    issues: list[ImportIssue]

    def add_issue(self, issue: ImportIssue) -> None:
        self.issues.append(issue)

    def add_issues(self, issues: list[ImportIssue]) -> None:
        self.issues.extend(issues)


def _resolve_object(
    ctx: _BuildContext,
    *,
    entity_type: str,
    external_id: str,
    data: models.Model,
    rows: list[ParsedRow],
    natural: models.Model | None,
    differences: list[FieldDifference] | None = None,
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
                _issues_for_rows(
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
            if differences:
                obj.state = ERROR
                for difference in differences:
                    for row in rows:
                        ctx.add_issue(_difference_issue(row, difference))
            else:
                obj.state = UNCHANGED
    elif natural is not None:
        obj.state = ERROR
        ctx.add_issues(
            _issues_for_rows(
                rows,
                "external_id",
                "CHANGE_NOT_SUPPORTED_YET",
                (
                    f"数据库已有编号 {external_id} 的 {entity_type} 对象，但没有同来源的身份映射。"
                    "B12a 不按编号或名称静默合并，也不更新已有对象；请清理冲突对象、"
                    "先建立明确来源映射，或等待 B12b。"
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


def _term_ref(ctx: _BuildContext, code: str) -> Term | None:
    obj = ctx.graph.terms.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_TERM, code)
    if isinstance(target, Term):
        return target
    if ctx.batch.term_id and ctx.batch.term is not None and ctx.batch.term.code == code:
        return ctx.batch.term
    return None


def _teacher_ref(ctx: _BuildContext, code: str) -> TeacherProfile | None:
    obj = ctx.graph.teachers.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_TEACHER, code)
    if isinstance(target, TeacherProfile):
        return target
    return None


def _student_ref(ctx: _BuildContext, code: str) -> Student | None:
    obj = ctx.graph.students.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_STUDENT, code)
    if isinstance(target, Student):
        return target
    return None


def _classroom_ref(ctx: _BuildContext, code: str) -> Classroom | None:
    obj = ctx.graph.classrooms.get(code)
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_CLASSROOM, code)
    if isinstance(target, Classroom):
        return target
    return None


def _offering_ref(ctx: _BuildContext, term_code: str, offering_code: str) -> CourseOffering | None:
    obj = ctx.graph.offerings.get((term_code, offering_code))
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_OFFERING, offering_code)
    if isinstance(target, CourseOffering) and target.term.code == term_code:
        return target
    return None


def _rule_ref(ctx: _BuildContext, term_code: str, rule_code: str) -> ScheduleRule | None:
    obj = ctx.graph.rules.get((term_code, rule_code))
    if obj is not None:
        return obj.data  # type: ignore[return-value]
    target = ctx.existing.target(ENTITY_RULE, rule_code)
    if isinstance(target, ScheduleRule) and target.offering.term.code == term_code:
        return target
    return None


def _period_exists(ctx: _BuildContext, term: Term, number: int) -> bool:
    if (term.code, number) in ctx.graph.periods:
        return True
    if term.pk is None:
        return False
    return Period.objects.filter(term_id=term.pk, period_number=number).exists()


def _offering_scope(ctx: _BuildContext, offering: CourseOffering) -> tuple[set[UUID], UUID | None]:
    for obj in ctx.graph.offerings.values():
        if obj.data.pk == offering.pk:
            teacher_ids = {
                _teacher_ref(ctx, code).pk
                for code in obj.metadata.get("teacher_codes", [])
                if _teacher_ref(ctx, code) is not None
            }
            lead = _teacher_ref(ctx, obj.metadata.get("lead_teacher_code", ""))
            return teacher_ids, lead.pk if lead is not None else None
    if offering.pk is None:
        return set(), None
    assignments = list(TeachingAssignment.objects.filter(offering_id=offering.pk))
    return {assignment.teacher_id for assignment in assignments}, (
        next((assignment.teacher_id for assignment in assignments if assignment.role == "lead"), None)
    )


def _build_terms(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("terms"):
        code = row.values.get("term_code")
        if not code or code in ctx.graph.terms:
            continue
        data = Term(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            code=code,
            name=row.values.get("term_name") or "",
            start_date=row.values.get("start_date"),
            end_date=row.values.get("end_date"),
            first_week_monday=row.values.get("first_week_monday"),
            teaching_week_count=row.values.get("teaching_week_count") or 0,
            timezone=row.values.get("timezone") or "",
            hour_minutes=row.values.get("hour_minutes") or 0,
        )
        natural = Term.objects.filter(school_id=ctx.batch.school_id, code=code).first()
        identity = ctx.existing.identity(ENTITY_TERM, code)
        target = ctx.existing.target(ENTITY_TERM, code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, Term):
            differences = _diff_fields(
                target,
                {
                    "name": data.name,
                    "start_date": data.start_date,
                    "end_date": data.end_date,
                    "first_week_monday": data.first_week_monday,
                    "teaching_week_count": data.teaching_week_count,
                    "timezone": data.timezone,
                    "hour_minutes": data.hour_minutes,
                },
                [
                    "name",
                    "start_date",
                    "end_date",
                    "first_week_monday",
                    "teaching_week_count",
                    "timezone",
                    "hour_minutes",
                ],
            )
        _resolve_object(
            ctx,
            entity_type=ENTITY_TERM,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        ctx.graph.terms[code] = ctx.graph.by_entity[ENTITY_TERM][code]


def _build_periods(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("periods"):
        term_code = row.values.get("term_code")
        number = row.values.get("period_number")
        if not term_code or not isinstance(number, int):
            continue
        key = (term_code, number)
        if key in ctx.graph.periods:
            continue
        term = _term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(
                _issue_for(
                    row,
                    "term_code",
                    "FK_NOT_FOUND",
                    f"学期 {term_code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
            continue
        data = Period(
            id=uuid.uuid4(),
            term_id=term.pk,
            period_number=number,
            start_time=row.values.get("start_time"),
            end_time=row.values.get("end_time"),
            block_code=row.values.get("block_code") or "",
        )
        natural = Period.objects.filter(term_id=term.pk, period_number=number).first() if term.pk else None
        identity = ctx.existing.identity(ENTITY_PERIOD, external_id_for_period(term_code, number))
        target = ctx.existing.target(ENTITY_PERIOD, external_id_for_period(term_code, number)) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, Period):
            differences = _diff_fields(
                target,
                {
                    "period_number": data.period_number,
                    "start_time": data.start_time,
                    "end_time": data.end_time,
                    "block_code": data.block_code,
                },
                ["period_number", "start_time", "end_time", "block_code"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_PERIOD,
            external_id=external_id_for_period(term_code, number),
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.periods[key] = obj


def _build_teachers(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("teachers"):
        code = row.values.get("teacher_code")
        if not code or code in ctx.graph.teachers:
            continue
        data = TeacherProfile(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            teacher_no=code,
            formal_name=row.values.get("formal_name") or "",
            department=_norm_optional(row.values.get("department")),
            email=normalize_user_email(row.values.get("email") or ""),
            phone=_norm_optional(row.values.get("phone")),
            phone_visibility=row.values.get("phone_visibility") or "private",
            wechat=_norm_optional(row.values.get("wechat")),
            wechat_visibility=row.values.get("wechat_visibility") or "private",
            is_active=bool(row.values.get("is_active")),
        )
        natural = TeacherProfile.objects.filter(school_id=ctx.batch.school_id, teacher_no=code).first()
        identity = ctx.existing.identity(ENTITY_TEACHER, code)
        target = ctx.existing.target(ENTITY_TEACHER, code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, TeacherProfile):
            differences = _diff_fields(
                target,
                {
                    "teacher_no": data.teacher_no,
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
                    "teacher_no",
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
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_TEACHER,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        ctx.graph.teachers[code] = obj


def _build_classrooms(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("classrooms"):
        code = row.values.get("classroom_code")
        if not code or code in ctx.graph.classrooms:
            continue
        data = Classroom(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            classroom_code=code,
            campus=row.values.get("campus") or "",
            building=row.values.get("building") or "",
            room_no=row.values.get("room_no") or "",
            capacity=row.values.get("capacity") or 0,
            equipment=sorted(row.values.get("equipment") or []),
            is_active=bool(row.values.get("is_active")),
        )
        natural = Classroom.objects.filter(school_id=ctx.batch.school_id, classroom_code=code).first()
        identity = ctx.existing.identity(ENTITY_CLASSROOM, code)
        target = ctx.existing.target(ENTITY_CLASSROOM, code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, Classroom):
            differences = _diff_fields(
                target,
                {
                    "classroom_code": data.classroom_code,
                    "campus": data.campus,
                    "building": data.building,
                    "room_no": data.room_no,
                    "capacity": data.capacity,
                    "equipment": sorted(data.equipment or []),
                    "is_active": data.is_active,
                },
                ["classroom_code", "campus", "building", "room_no", "capacity", "equipment", "is_active"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_CLASSROOM,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        ctx.graph.classrooms[code] = obj


def _build_courses(ctx: _BuildContext) -> None:
    grouped: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in ctx.parsed.rows_for("offerings"):
        code = row.values.get("course_code")
        if code:
            grouped[code].append(row)
    for code, rows in grouped.items():
        first = rows[0]
        data = Course(
            id=uuid.uuid4(),
            school_id=ctx.batch.school_id,
            course_code=code,
            name=first.values.get("course_name") or "",
        )
        natural = Course.objects.filter(school_id=ctx.batch.school_id, course_code=code).first()
        identity = ctx.existing.identity(ENTITY_COURSE, code)
        target = ctx.existing.target(ENTITY_COURSE, code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, Course):
            differences = _diff_fields(target, {"course_code": code, "name": data.name}, ["course_code", "name"])
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_COURSE,
            external_id=code,
            data=data,
            rows=rows,
            natural=natural,
            differences=differences,
        )
        ctx.graph.courses[code] = obj


def _build_teaching_classes(ctx: _BuildContext) -> None:
    grouped: dict[tuple[str, str], list[ParsedRow]] = defaultdict(list)
    for row in ctx.parsed.rows_for("offerings"):
        term_code = row.values.get("term_code")
        class_code = row.values.get("class_code")
        if term_code and class_code:
            grouped[(term_code, class_code)].append(row)
    for (term_code, class_code), rows in grouped.items():
        term = _term_ref(ctx, term_code)
        if term is None:
            ctx.add_issues(
                _issues_for_rows(
                    rows,
                    "term_code",
                    "FK_NOT_FOUND",
                    f"学期 {term_code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
            continue
        first = rows[0]
        data = TeachingClass(
            id=uuid.uuid4(),
            term_id=term.pk,
            class_code=class_code,
            name=first.values.get("class_name") or "",
        )
        natural = TeachingClass.objects.filter(term_id=term.pk, class_code=class_code).first() if term.pk else None
        external_id = external_id_for_teaching_class(term_code, class_code)
        identity = ctx.existing.identity(ENTITY_TEACHING_CLASS, external_id)
        target = ctx.existing.target(ENTITY_TEACHING_CLASS, external_id) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, TeachingClass):
            differences = _diff_fields(
                target,
                {"class_code": class_code, "name": data.name},
                ["class_code", "name"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_TEACHING_CLASS,
            external_id=external_id,
            data=data,
            rows=rows,
            natural=natural,
            differences=differences,
        )
        ctx.graph.teaching_classes[(term_code, class_code)] = obj


def _build_offerings(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("offerings"):
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        course_code = row.values.get("course_code")
        class_code = row.values.get("class_code")
        if not term_code or not offering_code or not course_code or not class_code:
            continue
        key = (term_code, offering_code)
        if key in ctx.graph.offerings:
            continue
        term = _term_ref(ctx, term_code)
        course = ctx.graph.courses.get(course_code)
        teaching_class = ctx.graph.teaching_classes.get((term_code, class_code))
        if term is None:
            ctx.add_issue(
                _issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在或编号无效。"),
            )
            continue
        if course is None:
            ctx.add_issue(_issue_for(row, "course_code", "FK_NOT_FOUND", f"课程 {course_code} 无法建立。"))
            continue
        if teaching_class is None:
            ctx.add_issue(_issue_for(row, "class_code", "FK_NOT_FOUND", f"教学班 {class_code} 无法建立。"))
            continue

        teacher_codes = list(row.values.get("teacher_codes") or [])
        teachers = {code: _teacher_ref(ctx, code) for code in teacher_codes}
        missing_teachers = [code for code, teacher in teachers.items() if teacher is None]
        for code in missing_teachers:
            ctx.add_issue(
                _issue_for(
                    row,
                    "teacher_codes",
                    "FK_NOT_FOUND",
                    f"教师 {code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
        lead_code = row.values.get("lead_teacher_code") or ""
        lead = _teacher_ref(ctx, lead_code)
        if lead is None:
            ctx.add_issue(
                _issue_for(
                    row,
                    "lead_teacher_code",
                    "FK_NOT_FOUND",
                    f"课程负责人 {lead_code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )

        data = CourseOffering(
            id=uuid.uuid4(),
            term_id=term.pk,
            offering_code=offering_code,
            course_id=course.data.pk,
            teaching_class_id=teaching_class.data.pk,
            target_hours=row.values.get("target_hours") or Decimal("0"),
            lead_teacher_id=lead.pk if lead is not None else None,
            minutes_per_hour=term.hour_minutes or 1,
            required_equipment=sorted(row.values.get("required_equipment") or []),
        )
        natural = (
            CourseOffering.objects.filter(term_id=term.pk, offering_code=offering_code).first() if term.pk else None
        )
        identity = ctx.existing.identity(ENTITY_OFFERING, offering_code)
        target = ctx.existing.target(ENTITY_OFFERING, offering_code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, CourseOffering):
            differences = _diff_fields(
                target,
                {
                    "term_id": data.term_id,
                    "offering_code": data.offering_code,
                    "course_id": data.course_id,
                    "teaching_class_id": data.teaching_class_id,
                    "target_hours": data.target_hours,
                    "lead_teacher_id": data.lead_teacher_id,
                    "minutes_per_hour": data.minutes_per_hour,
                    "required_equipment": sorted(data.required_equipment or []),
                },
                [
                    "term_id",
                    "offering_code",
                    "course_id",
                    "teaching_class_id",
                    "target_hours",
                    "lead_teacher_id",
                    "minutes_per_hour",
                    "required_equipment",
                ],
            )
            actual_teacher_ids, actual_lead_id = _offering_scope(ctx, target)
            expected_teacher_ids = {teacher.pk for teacher in teachers.values() if teacher is not None}
            if expected_teacher_ids != actual_teacher_ids:
                differences.append(
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
            if data.lead_teacher_id != actual_lead_id:
                actual_lead_no = (
                    TeacherProfile.objects.filter(pk=actual_lead_id).values_list("teacher_no", flat=True).first()
                )
                differences.append(
                    FieldDifference(
                        field="lead_teacher_code",
                        expected=lead_code,
                        actual=actual_lead_no,
                    )
                )

        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_OFFERING,
            external_id=offering_code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["teacher_codes"] = teacher_codes
        obj.metadata["lead_teacher_code"] = lead_code
        obj.metadata["term_code"] = term_code
        ctx.graph.offerings[key] = obj


def _build_students(ctx: _BuildContext) -> None:
    grouped: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in ctx.parsed.rows_for("enrollments"):
        code = row.values.get("student_code")
        if code:
            grouped[code].append(row)
    for code, rows in grouped.items():
        data = Student(id=uuid.uuid4(), school_id=ctx.batch.school_id, student_no=code)
        natural = Student.objects.filter(school_id=ctx.batch.school_id, student_no=code).first()
        identity = ctx.existing.identity(ENTITY_STUDENT, code)
        target = ctx.existing.target(ENTITY_STUDENT, code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, Student):
            differences = _diff_fields(target, {"student_no": code}, ["student_no"])
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_STUDENT,
            external_id=code,
            data=data,
            rows=rows,
            natural=natural,
            differences=differences,
        )
        ctx.graph.students[code] = obj


def _build_enrollments(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("enrollments"):
        enrollment_code = row.values.get("enrollment_code")
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        student_code = row.values.get("student_code")
        if not enrollment_code or not term_code or not offering_code or not student_code:
            continue
        if enrollment_code in ctx.graph.enrollments:
            continue
        offering = _offering_ref(ctx, term_code, offering_code)
        student = _student_ref(ctx, student_code)
        if offering is None:
            ctx.add_issue(
                _issue_for(
                    row,
                    "offering_code",
                    "FK_NOT_FOUND",
                    f"课程开设 {offering_code} 不存在于学期 {term_code} 的本批或已导入数据中。",
                )
            )
            continue
        term = _term_ref(ctx, term_code)
        if term is None or offering.term_id != term.pk:
            ctx.add_issue(_issue_for(row, "term_code", "TERM_MISMATCH", "选课学期必须与课程开设所属学期一致。"))
            continue
        if student is None:
            ctx.add_issue(
                _issue_for(
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
        differences: list[FieldDifference] = []
        if isinstance(target, Enrollment):
            differences = _diff_fields(
                target,
                {
                    "offering_id": data.offering_id,
                    "student_id": data.student_id,
                    "effective_from": data.effective_from,
                    "effective_until": data.effective_until,
                },
                ["offering_id", "student_id", "effective_from", "effective_until"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_ENROLLMENT,
            external_id=enrollment_code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.enrollments[enrollment_code] = obj


def _build_calendar_exceptions(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("calendar_exceptions"):
        code = row.values.get("exception_code")
        term_code = row.values.get("term_code")
        if not code or not term_code or code in ctx.graph.calendar_exceptions:
            continue
        term = _term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(_issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在或编号无效。"))
            continue
        data = CalendarException(
            id=uuid.uuid4(),
            term_id=term.pk,
            date=row.values.get("date"),
            kind=row.values.get("kind") or "",
            source_teaching_date=row.values.get("source_teaching_date"),
            reason=row.values.get("reason") or "",
        )
        natural = (
            CalendarException.objects.filter(term_id=term.pk, date=data.date).first() if term.pk and data.date else None
        )
        identity = ctx.existing.identity(ENTITY_CALENDAR_EXCEPTION, code)
        target = ctx.existing.target(ENTITY_CALENDAR_EXCEPTION, code) if identity else None
        differences: list[FieldDifference] = []
        if isinstance(target, CalendarException):
            differences = _diff_fields(
                target,
                {
                    "term_id": data.term_id,
                    "date": data.date,
                    "kind": data.kind,
                    "source_teaching_date": data.source_teaching_date,
                    "reason": data.reason,
                },
                ["term_id", "date", "kind", "source_teaching_date", "reason"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_CALENDAR_EXCEPTION,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.calendar_exceptions[code] = obj


def _build_rules(ctx: _BuildContext) -> None:
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
        offering = _offering_ref(ctx, term_code, offering_code)
        classroom = _classroom_ref(ctx, classroom_code)
        if offering is None:
            ctx.add_issue(
                _issue_for(
                    row,
                    "offering_code",
                    "FK_NOT_FOUND",
                    f"课程开设 {offering_code} 不存在于学期 {term_code} 的本批或已导入数据中。",
                )
            )
            continue
        term = _term_ref(ctx, term_code)
        if term is None or offering.term_id != term.pk:
            ctx.add_issue(_issue_for(row, "term_code", "TERM_MISMATCH", "规则学期必须与课程开设学期一致。"))
            continue
        if classroom is None:
            ctx.add_issue(_issue_for(row, "classroom_code", "FK_NOT_FOUND", f"教室 {classroom_code} 不存在。"))
            continue

        weeks = list(row.values.get("weeks") or [])
        invalid_weeks = [week for week in weeks if not 1 <= week <= (term.teaching_week_count or 0)]
        if invalid_weeks:
            ctx.add_issue(
                _issue_for(
                    row,
                    "weeks",
                    "WEEK_OUT_OF_RANGE",
                    f"教学周必须落在 1…{term.teaching_week_count} 范围内。",
                )
            )

        teacher_codes = list(row.values.get("teacher_codes") or [])
        teachers = {code: _teacher_ref(ctx, code) for code in teacher_codes}
        missing = [code for code, teacher in teachers.items() if teacher is None]
        for code in missing:
            ctx.add_issue(
                _issue_for(
                    row,
                    "teacher_codes",
                    "FK_NOT_FOUND",
                    f"教师 {code} 不存在于本批次或此前同来源导入的数据中。",
                )
            )
        teacher_ids = tuple(sorted((teacher.pk for teacher in teachers.values() if teacher is not None), key=str))

        scope_ids, _ = _offering_scope(ctx, offering)
        for code, teacher in teachers.items():
            if teacher is not None and teacher.pk not in scope_ids:
                ctx.add_issue(
                    _issue_for(
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
                number for number in range(start_period, end_period + 1) if not _period_exists(ctx, term, number)
            ]
        if missing_periods:
            ctx.add_issue(
                _issue_for(
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
        differences: list[FieldDifference] = []
        if isinstance(target, ScheduleRule):
            differences = _diff_fields(
                target,
                {
                    "offering_id": data.offering_id,
                    "rule_code": data.rule_code,
                    "weekday": data.weekday,
                    "start_period": data.start_period,
                    "end_period": data.end_period,
                    "weeks": sorted(data.weeks or []),
                    "classroom_id": data.classroom_id,
                    "planned_hours": data.planned_hours,
                },
                [
                    "offering_id",
                    "rule_code",
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
                differences.append(
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
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_RULE,
            external_id=rule_code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["teacher_ids"] = teacher_ids
        obj.metadata["teacher_codes"] = teacher_codes
        obj.metadata["term_code"] = term_code
        obj.metadata["offering_code"] = offering_code
        ctx.graph.rules[key] = obj


@dataclass
class _PlanData:
    planned: list[calendar.PlannedSession]
    planned_by_key: dict[tuple[str, str, date, int], calendar.PlannedSession]
    existing_generation_keys: set[tuple[UUID, date, int]]
    expected_new: int


def _objects_for_term(ctx: _BuildContext, entity_type: str, term_code: str) -> list[ImportObject]:
    mapping = ctx.graph.by_entity.get(entity_type, {})
    result: list[ImportObject] = []
    for obj in mapping.values():
        obj_term = obj.metadata.get("term_code")
        if obj_term is None and isinstance(obj.data, (Period, CalendarException, Term)):
            obj_term = getattr(getattr(obj.data, "term", None), "code", None)
        if obj_term == term_code:
            result.append(obj)
    return result


def _rule_plan_from_object(ctx: _BuildContext, obj: ImportObject) -> calendar.RulePlan | None:
    rule = obj.data
    if not isinstance(rule, ScheduleRule):
        return None
    if not (rule.start_period and rule.end_period and rule.classroom_id and rule.planned_hours is not None):
        return None
    teacher_ids = tuple(obj.metadata.get("teacher_ids", ()))
    if not teacher_ids:
        return None
    return calendar.RulePlan(
        rule_id=rule.pk,
        rule_code=rule.rule_code,
        offering_id=rule.offering_id,
        weekday=rule.weekday,
        start_period=rule.start_period,
        end_period=rule.end_period,
        weeks=tuple(rule.weeks or ()),
        classroom_id=rule.classroom_id,
        planned_hours=rule.planned_hours,
        teacher_ids=teacher_ids,
    )


def _referenced_term_codes(graph: ImportGraph, batch: ImportBatch) -> set[str]:
    codes = set(graph.terms)
    for obj in graph.objects():
        term_code = obj.metadata.get("term_code")
        if term_code:
            codes.add(term_code)
    if batch.term_id:
        term = batch.term
        if term is not None:
            codes.add(term.code)
    return codes


def _plan_all_sessions(ctx: _BuildContext) -> _PlanData:
    planned: list[calendar.PlannedSession] = []
    planned_by_key: dict[tuple[str, str, date, int], calendar.PlannedSession] = {}
    existing_generation_keys: set[tuple[UUID, date, int]] = set()
    expected_new = 0

    for term_code in sorted(_referenced_term_codes(ctx.graph, ctx.batch)):
        term_obj = ctx.graph.terms.get(term_code)
        term = term_obj.data if term_obj is not None else _term_ref(ctx, term_code)
        if not isinstance(term, Term):
            continue
        if not all(
            [
                term.start_date,
                term.end_date,
                term.first_week_monday,
                term.timezone,
                term.teaching_week_count,
            ]
        ):
            continue
        try:
            ZoneInfo(term.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            continue

        periods: dict[int, Period] = {}
        if term.pk:
            periods.update({period.period_number: period for period in Period.objects.filter(term_id=term.pk)})
        for (period_term, number), period_obj in ctx.graph.periods.items():
            if period_term == term_code and isinstance(period_obj.data, Period):
                period = period_obj.data
                if period.start_time and period.end_time:
                    periods[number] = period

        exception_objects = _objects_for_term(ctx, ENTITY_CALENDAR_EXCEPTION, term_code)
        exceptions_by_date: dict[date, CalendarException] = {}
        if term.pk:
            for exception in CalendarException.objects.filter(term_id=term.pk):
                exceptions_by_date[exception.date] = exception
        for obj in exception_objects:
            exception = obj.data
            if isinstance(exception, CalendarException) and exception.date and exception.kind:
                exceptions_by_date[exception.date] = exception
        exceptions = list(exceptions_by_date.values())

        rules = _objects_for_term(ctx, ENTITY_RULE, term_code)
        rule_plans_by_id: dict[UUID, calendar.RulePlan] = {}
        for obj in rules:
            plan = _rule_plan_from_object(ctx, obj)
            if plan is not None:
                rule_plans_by_id.setdefault(plan.rule_id, plan)
        if term.pk:
            db_rules = (
                ScheduleRule.objects.filter(offering__term_id=term.pk)
                .select_related("offering")
                .prefetch_related("teachers")
            )
            for rule in db_rules:
                if rule.pk in rule_plans_by_id:
                    continue
                if not (rule.start_period and rule.end_period and rule.classroom_id):
                    continue
                rule_plans_by_id[rule.pk] = calendar.RulePlan(
                    rule_id=rule.pk,
                    rule_code=rule.rule_code,
                    offering_id=rule.offering_id,
                    weekday=rule.weekday,
                    start_period=rule.start_period,
                    end_period=rule.end_period,
                    weeks=tuple(rule.weeks or ()),
                    classroom_id=rule.classroom_id,
                    planned_hours=rule.planned_hours,
                    teacher_ids=tuple(sorted((teacher.pk for teacher in rule.teachers.all()), key=str)),
                )
        rule_plans = list(rule_plans_by_id.values())

        if not rule_plans:
            continue
        try:
            term_planned = calendar.plan_term_sessions(
                term=term,
                rules=rule_plans,
                periods=periods,
                exceptions=exceptions,
            )
        except ValidationError as exc:
            rows = rules[0].rows if rules else []
            if rows:
                ctx.add_issue(
                    _issue_for(
                        rows[0],
                        "rule_code",
                        "RULE_EXPANSION_FAILED",
                        f"规则展开失败：{' '.join(exc.messages)}",
                    )
                )
            continue

        existing_sessions: dict[tuple[UUID, date, int], ClassSession] = {}
        if term.pk:
            for session in ClassSession.objects.filter(
                offering__term_id=term.pk,
                rule__isnull=False,
            ):
                existing_sessions[(session.rule_id, session.origin_teaching_date, session.origin_period)] = session
        for planned_session in term_planned:
            key = (
                term_code,
                planned_session.rule_code,
                planned_session.origin_teaching_date,
                planned_session.origin_period,
            )
            planned_by_key[key] = planned_session
            generation_key = (
                planned_session.rule_id,
                planned_session.origin_teaching_date,
                planned_session.origin_period,
            )
            if generation_key in existing_sessions:
                existing_generation_keys.add(generation_key)
            else:
                expected_new += 1
        planned.extend(term_planned)

    return _PlanData(
        planned=planned,
        planned_by_key=planned_by_key,
        existing_generation_keys=existing_generation_keys,
        expected_new=expected_new,
    )


def _period_for(ctx: _BuildContext, term: Term, number: int) -> Period | None:
    obj = ctx.graph.periods.get((term.code, number))
    if obj is not None and isinstance(obj.data, Period):
        period = obj.data
        if period.start_time and period.end_time:
            return period
    if term.pk:
        return Period.objects.filter(term_id=term.pk, period_number=number).first()
    return None


def _term_timezone(term: Term) -> ZoneInfo | None:
    try:
        return ZoneInfo(term.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _session_bounds(
    *,
    term: Term,
    session_date: date | None,
    start_period: int | None,
    end_period: int | None,
    start_time: time | None,
    end_time: time | None,
    ctx: _BuildContext,
) -> tuple[datetime | None, datetime | None]:
    if not session_date:
        return None, None
    tzinfo = _term_timezone(term)
    if tzinfo is None:
        return None, None
    if start_time is not None and end_time is not None:
        return (
            datetime.combine(session_date, start_time, tzinfo=tzinfo),
            datetime.combine(session_date, end_time, tzinfo=tzinfo),
        )
    if not isinstance(start_period, int) or not isinstance(end_period, int):
        return None, None
    start = _period_for(ctx, term, start_period)
    end = _period_for(ctx, term, end_period)
    if start is None or end is None:
        return None, None
    return (
        datetime.combine(session_date, start.start_time, tzinfo=tzinfo),
        datetime.combine(session_date, end.end_time, tzinfo=tzinfo),
    )


def _session_desired_data(
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
    completed_by = _teacher_id_for_code(row, row.values.get("completed_by_teacher_code"))
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


def _teacher_id_for_code(row: ParsedRow, code: str | None) -> UUID | None:
    # Filled by _session_teacher_links below before comparison.
    if not code:
        return None
    return row.values.get("_resolved_teacher_ids", {}).get(code)


def _session_teacher_links(ctx: _BuildContext, row: ParsedRow, teacher_codes: list[str]) -> dict[str, TeacherProfile]:
    result: dict[str, TeacherProfile] = {}
    for code in teacher_codes:
        teacher = _teacher_ref(ctx, code)
        if teacher is None:
            ctx.add_issue(
                _issue_for(
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


def _session_diff(target: ClassSession, data: ClassSession, teacher_ids: set[UUID]) -> list[FieldDifference]:
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
    differences = _diff_fields(
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


def _generated_session_layout_issues(
    *,
    row: ParsedRow,
    term: Term,
    offering: CourseOffering,
    classroom: Classroom,
    teacher_ids: set[UUID],
    planned: calendar.PlannedSession,
    start_time: time | None,
    end_time: time | None,
) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    checks: list[tuple[str, Any, Any]] = [
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
                _issue_for(
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
        tzinfo = _term_timezone(term)
        if tzinfo is None:
            return issues
        expected_start = planned.start_at.astimezone(tzinfo).time()
        expected_end = planned.end_at.astimezone(tzinfo).time()
        if start_time is not None and start_time != expected_start:
            issues.append(
                _issue_for(
                    row,
                    "start_time",
                    "CHANGE_NOT_SUPPORTED_YET",
                    "显式开始时间与规则生成时间不同；B12a 不支持单次时间变化。",
                )
            )
        if end_time is not None and end_time != expected_end:
            issues.append(
                _issue_for(
                    row,
                    "end_time",
                    "CHANGE_NOT_SUPPORTED_YET",
                    "显式结束时间与规则生成时间不同；B12a 不支持单次时间变化。",
                )
            )
    if planned.offering_id != offering.pk:
        issues.append(
            _issue_for(
                row,
                "offering_code",
                "RULE_SESSION_NOT_FOUND",
                "规则生成课次与 sessions 行的 offering 不一致，不能模糊配对。",
            )
        )
    return issues


def _validate_completion_row(
    *,
    ctx: _BuildContext,
    row: ParsedRow,
    end_at: datetime | None,
    now: datetime,
    teacher_ids: set[UUID],
) -> None:
    if row.values.get("status") != SessionStatus.COMPLETED:
        return
    completed_by_code = row.values.get("completed_by_teacher_code")
    completed_teacher = _teacher_ref(ctx, completed_by_code) if completed_by_code else None
    row.values["_completed_by_teacher_id"] = completed_teacher.pk if completed_teacher is not None else None
    if completed_by_code and completed_teacher is None:
        ctx.add_issue(
            _issue_for(
                row,
                "completed_by_teacher_code",
                "FK_NOT_FOUND",
                f"完成确认教师 {completed_by_code} 不存在。",
            )
        )
    elif completed_teacher is not None and completed_teacher.pk not in teacher_ids:
        ctx.add_issue(
            _issue_for(
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
                _issue_for(
                    row,
                    "completed_at",
                    "COMPLETION_BEFORE_END",
                    f"完成确认时间不能早于课次结束时间 {end_at.isoformat()}。",
                )
            )
        if completed_at > now:
            ctx.add_issue(
                _issue_for(
                    row,
                    "completed_at",
                    "COMPLETION_IN_FUTURE",
                    "完成确认时间不能晚于导入时刻；不能把未来课次标记为已完成。",
                )
            )


def _build_sessions(ctx: _BuildContext, plan: _PlanData, now: datetime) -> None:
    bound_planned: dict[tuple[str, str, date, int], ParsedRow] = {}

    for row in ctx.parsed.rows_for("sessions"):
        external_session_id = row.values.get("external_session_id")
        term_code = row.values.get("term_code")
        offering_code = row.values.get("offering_code")
        if not external_session_id or not term_code or not offering_code:
            continue
        if external_session_id in ctx.graph.sessions:
            continue
        term = _term_ref(ctx, term_code)
        offering = _offering_ref(ctx, term_code, offering_code)
        if term is None:
            ctx.add_issue(_issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        if offering is None:
            ctx.add_issue(_issue_for(row, "offering_code", "FK_NOT_FOUND", f"课程开设 {offering_code} 不存在。"))
            continue
        if offering.term_id != term.pk:
            ctx.add_issue(_issue_for(row, "term_code", "TERM_MISMATCH", "课次学期必须与课程开设学期一致。"))
            continue
        session_date = row.values.get("session_date")
        if (
            isinstance(session_date, date)
            and term.start_date
            and term.end_date
            and not (term.start_date <= session_date <= term.end_date)
        ):
            ctx.add_issue(_issue_for(row, "session_date", "DATE_OUTSIDE_TERM", "课次日期必须在学期起止日期内。"))
        classroom_code = row.values.get("classroom_code")
        classroom = _classroom_ref(ctx, classroom_code or "")
        if classroom is None:
            ctx.add_issue(_issue_for(row, "classroom_code", "FK_NOT_FOUND", f"教室 {classroom_code} 不存在。"))
            continue

        teacher_codes = list(row.values.get("teacher_codes") or [])
        teachers = _session_teacher_links(ctx, row, teacher_codes)
        teacher_ids = {teacher.pk for teacher in teachers.values()}
        scope_ids, _ = _offering_scope(ctx, offering)
        for code, teacher in teachers.items():
            if teacher.pk not in scope_ids:
                ctx.add_issue(
                    _issue_for(
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
                # Parsing/validation already emitted ORIGIN_FIELDS_INCOMPLETE.
                continue
            rule = _rule_ref(ctx, term_code, origin_rule_code)
            if rule is None:
                ctx.add_issue(
                    _issue_for(
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
                    _issue_for(
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
                    _issue_for(
                        row,
                        "external_session_id",
                        "DUPLICATE_RULE_SESSION",
                        "本批次的另一行已经绑定同一个规则生成课次；不能重复绑定。",
                    )
                )
                continue
            layout_issues = _generated_session_layout_issues(
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
                    _issue_for(
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
            data = _session_desired_data(
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
            # Use the validated row's current layout; it is identical to planned.
            data.start_period = planned.start_period
            data.end_period = planned.end_period
            data.classroom_id = planned.classroom_id
            data.planned_hours = planned.planned_hours
            _validate_completion_row(
                ctx=ctx,
                row=row,
                end_at=planned.end_at,
                now=now,
                teacher_ids=teacher_ids,
            )
            data.completed_by_teacher_id = row.values.get("_completed_by_teacher_id")
            differences: list[FieldDifference] = []
            if isinstance(existing_target, ClassSession):
                differences = _session_diff(existing_target, data, teacher_ids)
            obj = _resolve_object(
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

        start_at, end_at = _session_bounds(
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
                _issue_for(
                    row,
                    "start_period",
                    "FK_NOT_FOUND",
                    "无法按节次派生课次时间；请确认节次存在，或同时填写 start_time/end_time。",
                )
            )
            continue
        if end_at <= start_at:
            ctx.add_issue(_issue_for(row, "end_time", "INVALID_TIME_RANGE", "课次结束时间必须晚于开始时间。"))
            continue
        data = _session_desired_data(
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
        _validate_completion_row(
            ctx=ctx,
            row=row,
            end_at=end_at,
            now=now,
            teacher_ids=teacher_ids,
        )
        data.completed_by_teacher_id = row.values.get("_completed_by_teacher_id")
        differences = []
        if isinstance(existing_target, ClassSession):
            differences = _session_diff(existing_target, data, teacher_ids)
        obj = _resolve_object(
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


def _validate_block_term_range(
    ctx: _BuildContext,
    row: ParsedRow,
    term: Term,
    start_at: datetime | None,
    end_at: datetime | None,
) -> None:
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return
    tzinfo = _term_timezone(term)
    if tzinfo is None or not (term.start_date and term.end_date):
        return
    local_start = start_at.astimezone(tzinfo).date()
    local_end = end_at.astimezone(tzinfo).date()
    if not (term.start_date <= local_start <= term.end_date and term.start_date <= local_end <= term.end_date):
        ctx.add_issue(
            _issue_for(
                row,
                "start_at",
                "BLOCK_OUTSIDE_TERM",
                "占用起止时间必须落在学期日期范围内。",
            )
        )


def _build_room_blocks(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("room_blocks"):
        code = row.values.get("block_code")
        term_code = row.values.get("term_code")
        classroom_code = row.values.get("classroom_code")
        if not code or not term_code or not classroom_code or code in ctx.graph.room_blocks:
            continue
        term = _term_ref(ctx, term_code)
        classroom = _classroom_ref(ctx, classroom_code)
        if term is None:
            ctx.add_issue(_issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        if classroom is None:
            ctx.add_issue(_issue_for(row, "classroom_code", "FK_NOT_FOUND", f"教室 {classroom_code} 不存在。"))
            continue
        _validate_block_term_range(
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
        differences: list[FieldDifference] = []
        if isinstance(target, RoomBlock):
            differences = _diff_fields(
                target,
                {
                    "classroom_id": data.classroom_id,
                    "start_at": data.start_at,
                    "end_at": data.end_at,
                    "reason": data.reason,
                    "block_code": data.block_code,
                },
                ["classroom_id", "start_at", "end_at", "reason", "block_code"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_ROOM_BLOCK,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.room_blocks[code] = obj


def _build_person_blocks(ctx: _BuildContext) -> None:
    for row in ctx.parsed.rows_for("resource_unavailability"):
        code = row.values.get("block_code")
        term_code = row.values.get("term_code")
        resource_type = row.values.get("resource_type")
        resource_code = row.values.get("resource_code")
        if not code or not term_code or not resource_type or not resource_code or code in ctx.graph.person_blocks:
            continue
        term = _term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(_issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        teacher: TeacherProfile | None = None
        student: Student | None = None
        if resource_type == "teacher":
            teacher = _teacher_ref(ctx, resource_code)
            if teacher is None:
                ctx.add_issue(_issue_for(row, "resource_code", "FK_NOT_FOUND", f"教师 {resource_code} 不存在。"))
                continue
        elif resource_type == "student":
            student = _student_ref(ctx, resource_code)
            if student is None:
                ctx.add_issue(_issue_for(row, "resource_code", "FK_NOT_FOUND", f"学生 {resource_code} 不存在。"))
                continue
        else:
            continue
        _validate_block_term_range(
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
        differences: list[FieldDifference] = []
        if isinstance(target, PersonBlock):
            differences = _diff_fields(
                target,
                {
                    "teacher_id": data.teacher_id,
                    "student_id": data.student_id,
                    "start_at": data.start_at,
                    "end_at": data.end_at,
                    "reason": data.reason,
                    "block_code": data.block_code,
                },
                ["teacher_id", "student_id", "start_at", "end_at", "reason", "block_code"],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_PERSON_BLOCK,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
        )
        obj.metadata["term_code"] = term_code
        ctx.graph.person_blocks[code] = obj


def _coverage_scope_issue(ctx: _BuildContext, row: ParsedRow, term_code: str, kind: str, scope_key: str) -> bool:
    if scope_key == "school":
        return False
    allowed = COVERAGE_SCOPES.get(kind, set())
    if ":" not in scope_key:
        ctx.add_issue(
            _issue_for(
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
            _issue_for(
                row,
                "scope_key",
                "SCOPE_NOT_FOUND",
                f"coverage.kind={kind} 不支持范围键 {scope_key}。",
            )
        )
        return False
    if prefix == "offering" and _offering_ref(ctx, term_code, value) is None:
        ctx.add_issue(_issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的开课编号 {value} 不存在。"))
        return False
    if prefix == "teacher" and _teacher_ref(ctx, value) is None:
        ctx.add_issue(_issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的教师编号 {value} 不存在。"))
        return False
    if prefix == "student" and _student_ref(ctx, value) is None:
        ctx.add_issue(_issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的学生编号 {value} 不存在。"))
        return False
    if prefix == "classroom" and _classroom_ref(ctx, value) is None:
        ctx.add_issue(_issue_for(row, "scope_key", "SCOPE_NOT_FOUND", f"范围键引用的教室编号 {value} 不存在。"))
        return False
    return True


def _build_coverages(ctx: _BuildContext) -> None:
    coverage_rows: list[ParsedRow] = []
    for row in ctx.parsed.rows_for("coverage"):
        code = row.values.get("coverage_code")
        term_code = row.values.get("term_code")
        kind = row.values.get("kind")
        scope_key = row.values.get("scope_key")
        if not code or not term_code or not kind or not scope_key or code in ctx.graph.coverages:
            continue
        term = _term_ref(ctx, term_code)
        if term is None:
            ctx.add_issue(_issue_for(row, "term_code", "FK_NOT_FOUND", f"学期 {term_code} 不存在。"))
            continue
        _coverage_scope_issue(ctx, row, term_code, kind, scope_key)
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
        differences: list[FieldDifference] = []
        if isinstance(target, DataCoverage):
            differences = _diff_fields(
                target,
                {
                    "term_id": data.term_id,
                    "kind": data.kind,
                    "scope_key": data.scope_key,
                    "from_date": data.from_date,
                    "until_date": data.until_date,
                    "completeness": data.completeness,
                    "source_updated_at": data.source_updated_at,
                    "confirmation_ref": data.confirmation_ref,
                },
                [
                    "term_id",
                    "kind",
                    "scope_key",
                    "from_date",
                    "until_date",
                    "completeness",
                    "source_updated_at",
                    "confirmation_ref",
                ],
            )
        obj = _resolve_object(
            ctx,
            entity_type=ENTITY_COVERAGE,
            external_id=code,
            data=data,
            rows=[row],
            natural=natural,
            differences=differences,
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
                        _issue_for(
                            left_row,
                            "from_date",
                            "COVERAGE_OVERLAP",
                            "同一学期、kind 与 scope_key 的覆盖声明有效期重叠；请明确替换或裁剪区间。",
                        )
                    )
                    ctx.add_issue(
                        _issue_for(
                            right_row,
                            "from_date",
                            "COVERAGE_OVERLAP",
                            "同一学期、kind 与 scope_key 的覆盖声明有效期重叠；请明确替换或裁剪区间。",
                        )
                    )


def _dedupe_issues(issues: list[ImportIssue]) -> list[ImportIssue]:
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


def _parse_batch_files(batch: ImportBatch) -> tuple[ParseResult, list[ImportIssue], str]:
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


def _summary_for(graph: ImportGraph, plan: _PlanData) -> dict[str, Any]:
    status_counts: dict[str, dict[str, int]] = {
        NEW: defaultdict(int),
        UNCHANGED: defaultdict(int),
        ERROR: defaultdict(int),
    }
    for obj in graph.objects():
        status_counts[obj.state][obj.entity_type] += 1
    summary: dict[str, Any] = {
        "new_counts": dict(sorted(status_counts[NEW].items())),
        "unchanged_counts": dict(sorted(status_counts[UNCHANGED].items())),
        "error_counts": dict(sorted(status_counts[ERROR].items())),
        "expected_new_sessions": plan.expected_new,
    }
    return summary


def _estimated_hours(graph: ImportGraph) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for obj in graph.sessions.values():
        session = obj.data
        if (
            obj.state == NEW
            and isinstance(session, ClassSession)
            and session.status == SessionStatus.COMPLETED
            and session.actual_hours is not None
        ):
            offering_code = obj.metadata.get("offering_code")
            if offering_code:
                totals[offering_code] += session.actual_hours
    return {code: str(value) for code, value in sorted(totals.items())}


def _row_counts(
    parsed: ParseResult,
    graph: ImportGraph,
    issues: list[ImportIssue],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for parsed_file in parsed.files:
        counts[parsed_file.display_key] = {NEW: 0, UNCHANGED: 0, ERROR: 0}
    for issue in issues:
        key = f"{issue.file} / {issue.sheet}" if issue.sheet else issue.file
        counts.setdefault(key, {NEW: 0, UNCHANGED: 0, ERROR: 0})

    error_rows: set[str] = {f"{issue.file}\x1f{issue.sheet}\x1f{issue.row}" for issue in issues if issue.row > 0}
    seen_rows: set[str] = set()
    for parsed_file in parsed.files:
        file_counts = counts.setdefault(parsed_file.display_key, {NEW: 0, UNCHANGED: 0, ERROR: 0})
        for row in parsed_file.rows:
            seen_rows.add(row.source_key)
            if row.source_key in error_rows:
                file_counts[ERROR] += 1
                continue
            primary = graph.row_primary.get(row.source_key)
            if primary is None:
                if any(
                    issue.file == row.file and issue.sheet == row.sheet and issue.row == row.row_number
                    for issue in issues
                ):
                    file_counts[ERROR] += 1
                continue
            file_counts[primary.state] = file_counts.get(primary.state, 0) + 1

    # Header/file-level issues still need a visible error count even when no
    # physical data row exists.
    for issue in issues:
        key = f"{issue.file} / {issue.sheet}" if issue.sheet else issue.file
        file_counts = counts.setdefault(key, {NEW: 0, UNCHANGED: 0, ERROR: 0})
        if issue.row == 0 or (issue.row == 1 and not parsed_file_has_rows(parsed, issue.file, issue.sheet)):
            if file_counts[ERROR] == 0:
                file_counts[ERROR] = 1
    return counts


def parsed_file_has_rows(parsed: ParseResult, file_name: str, sheet: str) -> bool:
    return any(
        parsed_file.file_name == file_name and parsed_file.sheet == sheet and parsed_file.rows
        for parsed_file in parsed.files
    )


def _batch_term(ctx: _BuildContext) -> Term | None:
    codes = _referenced_term_codes(ctx.graph, ctx.batch)
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


def _preview_base(ctx: _BuildContext) -> int | None:
    term = _batch_term(ctx)
    return term.schedule_version if term is not None else None


def build_preview(
    batch: ImportBatch,
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> PreviewResult:
    now = now or django_timezone.now()
    parsed, parse_issues, file_hash = _parse_batch_files(batch)
    issues = list(parse_issues)
    issues.extend(validate_business_rows(parsed))

    ctx = _BuildContext(
        batch=batch,
        parsed=parsed,
        existing=_ExistingLookup.load(batch),
        graph=ImportGraph(),
        issues=issues,
    )
    _build_terms(ctx)
    _build_periods(ctx)
    _build_teachers(ctx)
    _build_classrooms(ctx)
    _build_courses(ctx)
    _build_teaching_classes(ctx)
    _build_offerings(ctx)
    _build_students(ctx)
    _build_enrollments(ctx)
    _build_calendar_exceptions(ctx)
    _build_rules(ctx)
    plan = _plan_all_sessions(ctx)
    _build_sessions(ctx, plan, now)
    _build_room_blocks(ctx)
    _build_person_blocks(ctx)
    _build_coverages(ctx)

    final_issues = _dedupe_issues(ctx.issues)
    row_counts = _row_counts(parsed, ctx.graph, final_issues)
    summary = _summary_for(ctx.graph, plan)
    estimated_hours = _estimated_hours(ctx.graph)
    summary["estimated_hours_by_offering"] = estimated_hours
    base_version = _preview_base(ctx)
    batch_term = _batch_term(ctx)

    if persist:
        batch.status = ImportStatus.PREVIEWED
        batch.row_counts = row_counts
        batch.errors_json = [issue.as_dict() for issue in final_issues]
        batch.preview_base_schedule_version = base_version
        batch.term = batch_term
        if file_hash:
            batch.file_hash = file_hash
        batch.save(
            update_fields=[
                "status",
                "row_counts",
                "errors_json",
                "preview_base_schedule_version",
                "term",
                "file_hash",
                "updated_at",
            ]
        )

    return PreviewResult(
        batch=batch,
        issues=tuple(final_issues),
        row_counts=row_counts,
        summary=summary,
        expected_new_sessions=plan.expected_new,
        estimated_hours_by_offering=estimated_hours,
        preview_base_schedule_version=base_version,
        file_hash=file_hash,
        graph=ctx.graph,
        planned_sessions=tuple(plan.planned),
        now=now,
    )


def preview_batch(batch: ImportBatch, *, now: datetime | None = None) -> PreviewResult:
    """Parse, validate and persist the preview outcome on ``batch``."""
    if batch.status == ImportStatus.COMMITTED:
        raise ValueError("已提交的导入批次不能重新预览。")
    result = build_preview(batch, now=now)
    if result.has_errors:
        batch.status = ImportStatus.PREVIEWED
    return result


def _make_identity(batch: ImportBatch, obj: ImportObject, object_id: UUID) -> None:
    ImportIdentity.objects.create(
        school_id=batch.school_id,
        source_system=batch.source_system,
        entity_type=obj.entity_type,
        external_id=obj.external_id,
        object_id=object_id,
        batch=batch,
    )


def _persist_new(obj: ImportObject) -> models.Model:
    instance = obj.data
    instance.save(force_insert=True)
    return instance


def _create_teaching_assignments(
    obj: ImportObject,
    term: Term,
    teacher_lookup: dict[str, TeacherProfile],
) -> None:
    # Teacher resolution happens through the preview graph and is stored on the
    # offering metadata; this function only writes the full-term assignments.
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


def _apply_generated_session_values(
    *,
    session: ClassSession,
    desired: ClassSession,
    actor,
) -> bool:
    update_fields: list[str] = []
    for field_name in (
        "status",
        "status_reason",
        "actual_hours",
        "completed_by_teacher_id",
        "completed_at",
        "completion_ref",
    ):
        if getattr(session, field_name) != getattr(desired, field_name):
            setattr(session, field_name, getattr(desired, field_name))
            update_fields.append(field_name)
    if session.status == SessionStatus.COMPLETED and desired.status == SessionStatus.COMPLETED:
        if session.recorded_by_user_id != actor.pk:
            session.recorded_by_user = actor
            update_fields.append("recorded_by_user")
    if not update_fields:
        return False
    session.version += 1
    update_fields.extend(["version", "updated_at"])
    session.save(update_fields=update_fields)
    return True


def _commit_plan(batch: ImportBatch, actor, result: PreviewResult) -> CommitResult:
    graph = result.graph
    now = django_timezone.now()
    created_count = 0
    sessions_changed = 0
    sessions_matched = 0
    sessions_generated = 0
    change_entries: list[ChangeEntry] = []
    independent_terms: set[str] = set()

    # New terms are created before the remaining graph so every term row can be
    # locked in the project-wide order: SchoolSyncState -> Term.
    for term_code in sorted(graph.terms):
        term_obj = graph.terms[term_code]
        if term_obj.state == NEW:
            _persist_new(term_obj)
            _make_identity(batch, term_obj, term_obj.data.pk)
            created_count += 1
    existing_lookup = _ExistingLookup.load(batch)
    locked_terms: dict[str, Term] = {}
    for term_code in sorted(_referenced_term_codes(graph, batch)):
        term_obj = graph.terms.get(term_code)
        if term_obj is not None:
            term_id = term_obj.data.pk
        else:
            target = existing_lookup.target(ENTITY_TERM, term_code)
            if not isinstance(target, Term):
                target = batch.term if batch.term is not None and batch.term.code == term_code else None
            if target is None:
                continue
            term_id = target.pk
        locked_terms[term_code] = Term.objects.select_for_update().get(pk=term_id)

    teacher_lookup: dict[str, TeacherProfile] = {}
    for identity in existing_lookup.identities.values():
        if identity.entity_type == ENTITY_TEACHER:
            target = existing_lookup.target(ENTITY_TEACHER, identity.external_id)
            if isinstance(target, TeacherProfile):
                teacher_lookup[identity.external_id] = target
    for teacher_obj in graph.teachers.values():
        if teacher_obj.state == NEW:
            _persist_new(teacher_obj)
            _make_identity(batch, teacher_obj, teacher_obj.data.pk)
            created_count += 1
        teacher_lookup[teacher_obj.external_id] = teacher_obj.data  # type: ignore[assignment]

    for period_obj in graph.periods.values():
        if period_obj.state == NEW:
            _persist_new(period_obj)
            _make_identity(batch, period_obj, period_obj.data.pk)
            created_count += 1
    for classroom_obj in graph.classrooms.values():
        if classroom_obj.state == NEW:
            _persist_new(classroom_obj)
            _make_identity(batch, classroom_obj, classroom_obj.data.pk)
            created_count += 1
    for course_obj in graph.courses.values():
        if course_obj.state == NEW:
            _persist_new(course_obj)
            _make_identity(batch, course_obj, course_obj.data.pk)
            created_count += 1
    for class_obj in graph.teaching_classes.values():
        if class_obj.state == NEW:
            _persist_new(class_obj)
            _make_identity(batch, class_obj, class_obj.data.pk)
            created_count += 1
    for offering_obj in graph.offerings.values():
        if offering_obj.state == NEW:
            _persist_new(offering_obj)
            _make_identity(batch, offering_obj, offering_obj.data.pk)
            created_count += 1
            term_code = offering_obj.metadata.get("term_code")
            term = locked_terms.get(term_code)
            if term is not None:
                _create_teaching_assignments(offering_obj, term, teacher_lookup)
    for student_obj in graph.students.values():
        if student_obj.state == NEW:
            _persist_new(student_obj)
            _make_identity(batch, student_obj, student_obj.data.pk)
            created_count += 1
    for enrollment_obj in graph.enrollments.values():
        if enrollment_obj.state == NEW:
            _persist_new(enrollment_obj)
            _make_identity(batch, enrollment_obj, enrollment_obj.data.pk)
            created_count += 1
    for exception_obj in graph.calendar_exceptions.values():
        if exception_obj.state == NEW:
            _persist_new(exception_obj)
            _make_identity(batch, exception_obj, exception_obj.data.pk)
            created_count += 1
    for rule_obj in graph.rules.values():
        if rule_obj.state == NEW:
            _persist_new(rule_obj)
            teacher_ids = list(rule_obj.metadata.get("teacher_ids", ()))
            if teacher_ids:
                rule_obj.data.teachers.set(teacher_ids)
            _make_identity(batch, rule_obj, rule_obj.data.pk)
            created_count += 1

    terms_with_new_rules = {
        obj.metadata.get("term_code")
        for obj in graph.rules.values()
        if obj.state == NEW and obj.metadata.get("term_code")
    }
    for term_code in sorted(terms_with_new_rules):
        term = locked_terms.get(term_code)
        if term is None:
            continue
        generation = generate_sessions_for_term(term)
        sessions_generated += generation.created

    # Bind sessions.csv rows to generated sessions.  The generated service
    # creates the stable UUIDs; ImportIdentity must point at those IDs, not at
    # preview-only random identifiers.
    generated_sessions_by_key: dict[tuple[UUID, date, int], ClassSession] = {}
    term_ids = [term.pk for term in locked_terms.values()]
    if term_ids:
        for session in ClassSession.objects.filter(offering__term_id__in=term_ids, rule__isnull=False):
            generated_sessions_by_key[(session.rule_id, session.origin_teaching_date, session.origin_period)] = session

    for session_obj in graph.sessions.values():
        if session_obj.state != NEW:
            continue
        if session_obj.generated_session:
            planned_key = session_obj.planned_key
            if planned_key is None:
                raise RuntimeError("规则课次缺少生成键，无法绑定。")
            term_code, rule_code, origin_date, origin_period = planned_key
            rule_obj = graph.rules.get((term_code, rule_code))
            if rule_obj is None:
                raise RuntimeError("规则课次找不到对应规则。")
            generation_key = (rule_obj.data.pk, origin_date, origin_period)
            session = generated_sessions_by_key.get(generation_key)
            if session is None:
                # Query once more for the exact stable key; this also handles a
                # rule that pre-existed but was not part of this batch.
                session = (
                    ClassSession.objects.filter(
                        rule_id=rule_obj.data.pk,
                        origin_teaching_date=origin_date,
                        origin_period=origin_period,
                    )
                    .order_by("id")
                    .first()
                )
            if session is None:
                raise RuntimeError(f"规则课次 {planned_key} 生成后未找到对应数据库行。")
            _make_identity(batch, session_obj, session.pk)
            sessions_matched += 1
            if _apply_generated_session_values(session=session, desired=session_obj.data, actor=actor):
                sessions_changed += 1
                change_entries.append(
                    ChangeEntry(
                        entity_type=ChangeEntityType.SESSION,
                        entity_id=session.pk,
                        version=session.version,
                        offering_id=session.offering_id,
                    )
                )
            continue

        # Independent one-off session.
        _persist_new(session_obj)
        _make_identity(batch, session_obj, session_obj.data.pk)
        created_count += 1
        teacher_ids = list(session_obj.metadata.get("teacher_ids", ()))
        if teacher_ids:
            SessionTeacher.objects.bulk_create(
                [SessionTeacher(session_id=session_obj.data.pk, teacher_id=teacher_id) for teacher_id in teacher_ids]
            )
        change_entries.append(
            ChangeEntry(
                entity_type=ChangeEntityType.SESSION,
                entity_id=session_obj.data.pk,
                version=session_obj.data.version,
                offering_id=session_obj.data.offering_id,
            )
        )
        term_code = session_obj.metadata.get("term_code")
        if term_code:
            independent_terms.add(term_code)

    for term_code in sorted(independent_terms):
        term = locked_terms.get(term_code)
        if term is None:
            continue
        term.schedule_version += 1
        term.save(update_fields=["schedule_version", "updated_at"])
        change_entries.append(
            ChangeEntry(
                entity_type=ChangeEntityType.TERM,
                entity_id=term.pk,
                version=term.schedule_version,
            )
        )

    if change_entries:
        record_changes(batch.school_id, change_entries)

    for block_obj in graph.room_blocks.values():
        if block_obj.state == NEW:
            _persist_new(block_obj)
            _make_identity(batch, block_obj, block_obj.data.pk)
            created_count += 1
    for block_obj in graph.person_blocks.values():
        if block_obj.state == NEW:
            _persist_new(block_obj)
            _make_identity(batch, block_obj, block_obj.data.pk)
            created_count += 1
    for coverage_obj in graph.coverages.values():
        if coverage_obj.state == NEW:
            _persist_new(coverage_obj)
            _make_identity(batch, coverage_obj, coverage_obj.data.pk)
            created_count += 1

    if len(_referenced_term_codes(graph, batch)) == 1 and len(locked_terms) == 1:
        batch.term = next(iter(locked_terms.values()))
    batch.status = ImportStatus.COMMITTED
    batch.committed_by = actor
    batch.committed_at = now
    batch.save(update_fields=["term", "status", "committed_by", "committed_at", "updated_at"])

    unchanged = sum(1 for obj in graph.objects() if obj.state == UNCHANGED)
    return CommitResult(
        batch=batch,
        committed=True,
        created=created_count,
        sessions_generated=sessions_generated,
        sessions_matched=sessions_matched,
        sessions_changed=sessions_changed,
        unchanged=unchanged,
    )


def _dedupe_issue_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
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


def _stale_result(batch: ImportBatch, preview: PreviewResult, baseline_errors: list[dict[str, Any]]) -> CommitResult:
    stale = make_issue(
        file="",
        sheet="",
        row=0,
        field="",
        value="",
        code="PREVIEW_STALE",
        message="预览之后相关数据发生变化；请重新预览并确认，旧预览不能提交。",
    )
    batch.status = ImportStatus.UPLOADED
    batch.row_counts = preview.row_counts
    batch.errors_json = _dedupe_issue_dicts([*baseline_errors, *preview.errors, stale.as_dict()])
    batch.preview_base_schedule_version = None
    batch.save(
        update_fields=[
            "status",
            "row_counts",
            "errors_json",
            "preview_base_schedule_version",
            "updated_at",
        ]
    )
    return CommitResult(
        batch=batch,
        committed=False,
        code="PREVIEW_STALE",
        message=stale.message,
    )


def _mark_failed(batch: ImportBatch, exc: Exception) -> None:
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


def commit_batch(batch: ImportBatch, actor) -> CommitResult:
    """Commit one previewed batch in a single transaction.

    The preview is re-run while holding the project write locks.  A changed
    preview or schedule version returns ``PREVIEW_STALE`` and forces a new
    preview; it never reuses the old new/unchanged decision.
    """
    import copy

    batch.refresh_from_db()
    if batch.status != ImportStatus.PREVIEWED:
        raise ImportCommitError("INVALID_STATUS", "只有已预览的批次可以提交。")
    if batch.errors_json:
        raise ImportCommitError("PREVIEW_HAS_ERRORS", "预览存在错误，整批不能提交。")

    try:
        with transaction.atomic():
            SchoolSyncState.objects.select_for_update().get_or_create(school_id=batch.school_id)
            if batch.term_id:
                # Lock order: SchoolSyncState -> Term.  Other writers use the
                # same order, so the preview comparison happens under a stable
                # term row.
                Term.objects.select_for_update().get(pk=batch.term_id)

            # Serialise concurrent commits of the same batch and re-check the
            # status after taking the locks.  ImportBatch is not a domain write
            # entry point, so it is locked after State/Term.
            locked_batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
            if locked_batch.status != ImportStatus.PREVIEWED:
                raise ImportCommitError("INVALID_STATUS", "批次已被其他操作处理，只有已预览的批次可以提交。")
            if locked_batch.errors_json:
                raise ImportCommitError("PREVIEW_HAS_ERRORS", "预览存在错误，整批不能提交。")

            baseline_counts = copy.deepcopy(locked_batch.row_counts)
            baseline_errors = copy.deepcopy(locked_batch.errors_json)
            baseline_base = locked_batch.preview_base_schedule_version
            baseline_file_hash = locked_batch.file_hash

            preview = build_preview(locked_batch, persist=False)
            if (
                preview.row_counts != baseline_counts
                or preview.errors != baseline_errors
                or preview.preview_base_schedule_version != baseline_base
                or preview.file_hash != baseline_file_hash
            ):
                return _stale_result(locked_batch, preview, baseline_errors)
            if preview.has_errors:
                raise ImportCommitError("PREVIEW_HAS_ERRORS", "重新预览发现错误，整批不能提交。")

            return _commit_plan(locked_batch, actor, preview)
    except ImportCommitError:
        raise
    except Exception as exc:
        _mark_failed(batch, exc)
        raise ImportCommitError("COMMIT_FAILED", f"导入提交失败，已整体回滚：{exc}") from exc
