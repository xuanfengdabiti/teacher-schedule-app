"""B12d limited-scope snapshot planning (PAUSED — NOT WIRED).

This module is kept isolated on purpose.  It is not imported by the preview,
commit or Admin path, and ``ImportMode.SNAPSHOT`` batches are rejected at
preview time with ``SNAPSHOT_NOT_IMPLEMENTED``.  Do not wire it in or advertise
snapshot as supported until the paused B12d work is resumed.

Intended semantics once resumed: a snapshot batch declares one existing term,
one source system and explicit offering/resource codes.  Only objects that
already have an ``ImportIdentity`` for the same school + source and fall inside
that scope may be reported as missing.  Missing objects are never hard-deleted.

* rules are deactivated and their future untouched sessions go through the
  normal T14 cancellation/decision path;
* sessions are only recognised through their explicit source identity mapping
  (``sessions.csv`` remains a supplement for rule-generated sessions);
* enrollments end at the batch's explicit withdrawal date using the half-open
  ``[effective_from, effective_until)`` interval;
* teachers / classrooms / offerings only produce deactivation suggestions.

Snapshot types that would imply a whole-school replacement and have no
implemented missing semantics are a hard error, never a silent "full snapshot
supported".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from apps.academics.models import CourseOffering, Enrollment, TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.imports.models import ImportBatch, ImportIdentity, ImportMode
from apps.imports.parsing import ImportIssue, ParseResult, make_issue
from apps.scheduling.models import ClassSession, ScheduleRule

SUPPORTED_TYPES = ("rules", "sessions", "enrollments")
SUGGESTION_TYPES = ("teachers", "classrooms", "offerings")
MERGE_ONLY_TYPES = (
    "terms",
    "periods",
    "calendar_exceptions",
    "room_blocks",
    "resource_unavailability",
    "coverage",
)
TYPE_LABELS = {
    "rules": "排课规则",
    "sessions": "具体课次",
    "enrollments": "实际选课",
    "teachers": "教师名册",
    "classrooms": "教室名册",
    "offerings": "课程开设",
}
TYPE_FILE = {
    "rules": "rules.csv",
    "sessions": "sessions.csv",
    "enrollments": "enrollments.csv",
    "teachers": "teachers.csv",
    "classrooms": "classrooms.csv",
    "offerings": "offerings.csv",
}
ACTION_LABELS = {
    "deactivate_rule": "停用规则并取消未来未触碰课次",
    "cancel_session": "取消课次（保留 UUID）",
    "decision": "人工决定",
    "kept_past": "过去课次保持现状",
    "kept_completed": "已完成课次保持现状",
    "unchanged": "无变化",
    "end_enrollment": "设置退课生效日期",
    "suggest_deactivate": "建议停用（不自动修改）",
}
STATUS_LABELS = {
    "scheduled": "待上课",
    "completed": "已完成",
    "cancelled": "已取消",
}


@dataclass(frozen=True)
class SnapshotScope:
    term_code: str
    types: tuple[str, ...]
    offering_codes: tuple[str, ...]
    resource_codes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "term_code": self.term_code,
            "types": list(self.types),
            "offering_codes": list(self.offering_codes),
            "resource_codes": list(self.resource_codes),
        }


@dataclass(frozen=True)
class SnapshotMissing:
    """One missing object with its current value and proposed action."""

    entity_type: str
    external_id: str
    action: str
    detail: str
    before: dict

    def as_dict(self) -> dict:
        return {
            "entity_type": self.entity_type,
            "external_id": self.external_id,
            "action": self.action,
            "action_label": ACTION_LABELS.get(self.action, self.action),
            "detail": self.detail,
            "before": self.before,
        }


@dataclass
class SnapshotPlan:
    scope: SnapshotScope | None
    term: Term | None
    missing: list[SnapshotMissing] = field(default_factory=list)
    deactivated_rule_ids: set[UUID] = field(default_factory=set)
    excluded_session_ids: set[UUID] = field(default_factory=set)
    enrollment_until: dict[UUID, date] = field(default_factory=dict)
    issues: list[ImportIssue] = field(default_factory=list)
    signature: str = ""

    @property
    def has_errors(self) -> bool:
        return any(issue.level == "error" for issue in self.issues)

    def fingerprint_payload(self) -> dict:
        return {
            "scope": self.scope.as_dict() if self.scope is not None else {},
            "missing": [entry.as_dict() for entry in self.missing],
        }

    def compute_signature(self) -> str:
        encoded = json.dumps(self.fingerprint_payload(), sort_keys=True, default=str, ensure_ascii=False)
        self.signature = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return self.signature


def _scope_issue(field_name: str, code: str, message: str) -> ImportIssue:
    return make_issue(file="", sheet="", row=0, field=field_name, value="", code=code, message=message)


def _string_list(raw, field_name: str, *, required: bool) -> tuple[list[str], ImportIssue | None]:
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        return [], _scope_issue(
            field_name,
            "SNAPSHOT_SCOPE_INVALID",
            f'scope.{field_name} 必须是字符串数组，例如 ["OFF-CRIME-2026A"]。',
        )
    values = sorted({item.strip() for item in raw if item.strip()})
    if required and not values:
        return [], _scope_issue(
            field_name,
            "SNAPSHOT_SCOPE_REQUIRED",
            f"snapshot 必须显式给出 scope.{field_name}；空范围不会暗示全校或全来源快照。",
        )
    return values, None


def resolve_snapshot_scope(batch: ImportBatch) -> tuple[SnapshotScope | None, list[ImportIssue]]:
    """Validate the plain-JSON scope on the batch and resolve its existing term."""
    issues: list[ImportIssue] = []
    scope_json = batch.scope_json or {}
    if not isinstance(scope_json, dict):
        return None, [
            _scope_issue(
                "scope_json",
                "SNAPSHOT_SCOPE_INVALID",
                'scope 必须是 JSON 对象，例如 {"term_code": "2026-autumn", "types": ["rules"], '
                '"offering_codes": ["OFF-CRIME-2026A"]}。',
            )
        ]
    term_code = scope_json.get("term_code")
    if not isinstance(term_code, str) or not term_code.strip():
        issues.append(
            _scope_issue(
                "scope_json",
                "SNAPSHOT_SCOPE_REQUIRED",
                "snapshot 必须填写 scope.term_code，且只能覆盖一个现有学期。",
            )
        )
        term_code = ""
    types, types_issue = _string_list(scope_json.get("types"), "types", required=True)
    offering_codes, offering_issue = _string_list(scope_json.get("offering_codes"), "offering_codes", required=True)
    resource_codes, resource_issue = _string_list(scope_json.get("resource_codes"), "resource_codes", required=False)
    for issue in (types_issue, offering_issue, resource_issue):
        if issue is not None:
            issues.append(issue)

    for type_name in types:
        if type_name in SUGGESTION_TYPES or type_name in SUPPORTED_TYPES:
            continue
        if type_name in MERGE_ONLY_TYPES:
            issues.append(
                _scope_issue(
                    "types",
                    "SNAPSHOT_TYPE_NOT_SUPPORTED",
                    f"类型 {type_name}（{TYPE_LABELS.get(type_name, type_name)}）不支持 snapshot 缺失处理；"
                    "它只能随同一批次以 merge 方式更新，不能声明为快照类型。",
                )
            )
        else:
            issues.append(
                _scope_issue(
                    "types",
                    "SNAPSHOT_TYPE_NOT_SUPPORTED",
                    f"未知快照类型 {type_name}；当前只支持 rules、sessions、enrollments，"
                    "teachers/classrooms/offerings 只列停用建议。",
                )
            )
    if types_issue is None and not types:
        issues.append(_scope_issue("types", "SNAPSHOT_SCOPE_REQUIRED", "snapshot 必须至少声明一个数据类型。"))
    if batch.enrollment_end_date is None and "enrollments" in types:
        issues.append(
            _scope_issue(
                "enrollment_end_date",
                "SNAPSHOT_ENROLLMENT_DATE_REQUIRED",
                "快照包含 enrollments 缺失处理时必须填写明确退课生效日期（不含当天），不能按上传日猜测。",
            )
        )

    term = None
    if term_code:
        term = Term.objects.filter(school_id=batch.school_id, code=term_code).first()
        if term is None:
            issues.append(
                _scope_issue(
                    "term_code",
                    "SNAPSHOT_TERM_NOT_FOUND",
                    f"学期 {term_code} 不存在于本学校；snapshot 只能固定现有学期，不会自动建学期。",
                )
            )
        elif batch.term_id and batch.term_id != term.pk:
            issues.append(
                _scope_issue(
                    "term_code",
                    "SNAPSHOT_TERM_MISMATCH",
                    "scope.term_code 与批次已固定的学期不一致；请重新上传或使用正确学期。",
                )
            )
    scope = None
    if term is not None and not issues:
        scope = SnapshotScope(
            term_code=term.code,
            types=tuple(types),
            offering_codes=tuple(offering_codes),
            resource_codes=tuple(resource_codes),
        )
    return scope, issues


def _in_scope_offerings(scope: SnapshotScope, term: Term) -> list[CourseOffering]:
    return list(CourseOffering.objects.filter(term=term, offering_code__in=scope.offering_codes))


def _identity_map(batch: ImportBatch, entity_type: str) -> dict[UUID, ImportIdentity]:
    return {
        identity.object_id: identity
        for identity in ImportIdentity.objects.filter(
            school_id=batch.school_id,
            source_system=batch.source_system,
            entity_type=entity_type,
        )
    }


def _require_file(scope_type: str, present_types: set[str], issues: list[ImportIssue]) -> None:
    if scope_type not in present_types:
        issues.append(
            _scope_issue(
                "mode",
                "SNAPSHOT_FILE_REQUIRED",
                f"snapshot 声明了 {scope_type}（{TYPE_LABELS.get(scope_type, scope_type)}），"
                f"必须上传 {TYPE_FILE[scope_type]} 或同名工作表；保留表头、数据行为空才表示明确的空集合。"
                "未上传不等于空集合。",
            )
        )


def _rule_before(rule: ScheduleRule) -> dict:
    return {
        "is_active": rule.is_active,
        "is_active_label": "启用" if rule.is_active else "已停用",
        "offering_code": rule.offering.offering_code,
        "rule_code": rule.rule_code,
        "weekday": rule.weekday,
        "start_period": rule.start_period,
        "end_period": rule.end_period,
        "weeks": list(rule.weeks or []),
    }


def _session_before(session: ClassSession, term: Term) -> dict:
    local_start = session.start_at.astimezone(term.tzinfo)
    return {
        "session_id": str(session.id),
        "date": local_start.date().isoformat(),
        "start_period": session.start_period,
        "end_period": session.end_period,
        "status": session.status,
        "status_label": STATUS_LABELS.get(session.status, session.status),
        "offering_code": session.offering.offering_code,
        "classroom_code": session.classroom.classroom_code,
    }


def _enrollment_before(enrollment: Enrollment) -> dict:
    return {
        "effective_from": enrollment.effective_from.isoformat(),
        "effective_until": enrollment.effective_until.isoformat() if enrollment.effective_until else "",
        "offering_code": enrollment.offering.offering_code,
        "student_code": enrollment.student.student_no,
    }


def build_snapshot_plan(batch: ImportBatch, parsed: ParseResult) -> SnapshotPlan:
    """Collect missing objects for one snapshot batch.

    A logical file that is absent is an error for a declared snapshot type,
    while a header-only file is an explicit empty set.
    """
    if batch.mode != ImportMode.SNAPSHOT:
        return SnapshotPlan(scope=None, term=None)
    scope, issues = resolve_snapshot_scope(batch)
    if scope is None:
        return SnapshotPlan(scope=None, term=None, issues=issues)

    term = Term.objects.get(school_id=batch.school_id, code=scope.term_code)
    plan = SnapshotPlan(scope=scope, term=term, issues=issues)
    present_types = {parsed_file.logical_name for parsed_file in parsed.files}
    rows_by_type = {type_name: parsed.rows_for(type_name) for type_name in TYPE_FILE}
    for scope_type in scope.types:
        _require_file(scope_type, present_types, plan.issues)
    if "rules" in scope.types:
        _missing_rules(batch, scope, term, rows_by_type["rules"], plan)
    if "sessions" in scope.types:
        _missing_sessions(batch, scope, term, rows_by_type["sessions"], plan)
    if "enrollments" in scope.types:
        _missing_enrollments(batch, scope, term, rows_by_type["enrollments"], plan)
    if "teachers" in scope.types:
        _missing_teachers(batch, scope, term, rows_by_type["teachers"], plan)
    if "classrooms" in scope.types:
        _missing_classrooms(batch, scope, term, rows_by_type["classrooms"], plan)
    if "offerings" in scope.types:
        _missing_offerings(batch, term, rows_by_type["offerings"], plan)
    plan.missing.sort(key=lambda entry: (entry.entity_type, entry.external_id))
    plan.compute_signature()
    return plan


def _missing_rules(batch, scope, term, rows, plan: SnapshotPlan) -> None:
    present_codes = {row.values.get("rule_code") for row in rows if row.values.get("rule_code")}
    offerings = _in_scope_offerings(scope, term)
    identities = _identity_map(batch, "rule")
    rules = (
        ScheduleRule.objects.filter(offering__in=offerings, is_active=True)
        .select_related("offering")
        .order_by("rule_code")
    )
    session_identities = _identity_map(batch, "session")
    for rule in rules:
        identity = identities.get(rule.pk)
        if identity is None or identity.external_id in present_codes:
            continue
        plan.deactivated_rule_ids.add(rule.pk)
        plan.missing.append(
            SnapshotMissing(
                entity_type="rule",
                external_id=identity.external_id,
                action="deactivate_rule",
                detail="快照未包含该规则：停用规则并停止后续生成；历史课次与 UUID 保留。",
                before=_rule_before(rule),
            )
        )
        sessions = (
            ClassSession.objects.filter(rule=rule).select_related("offering", "classroom").order_by("start_at", "id")
        )
        for session in sessions:
            if session.status == "cancelled":
                continue
            external_id = session_identities.get(session.id)
            plan.missing.append(
                SnapshotMissing(
                    entity_type="session",
                    external_id=external_id.external_id if external_id else "",
                    action="cancel_session",
                    detail=f"来自缺失规则 {rule.rule_code} 的课次；按课次状态取消或进入人工决定。",
                    before=_session_before(session, term),
                )
            )


def _missing_sessions(batch, scope, term, rows, plan: SnapshotPlan) -> None:
    present_ids = {row.values.get("external_session_id") for row in rows}
    offerings = _in_scope_offerings(scope, term)
    identities = _identity_map(batch, "session")
    sessions = (
        ClassSession.objects.filter(offering__in=offerings)
        .select_related("offering", "classroom")
        .order_by("start_at", "id")
    )
    for session in sessions:
        identity = identities.get(session.id)
        if identity is None or identity.external_id in present_ids:
            continue
        if session.status == "cancelled":
            continue
        if scope.resource_codes and session.classroom.classroom_code not in scope.resource_codes:
            continue
        plan.excluded_session_ids.add(session.id)
        plan.missing.append(
            SnapshotMissing(
                entity_type="session",
                external_id=identity.external_id,
                action="cancel_session",
                detail=(
                    "仅按同来源显式 external_session_id 映射识别；保留 UUID，未来未触碰课次取消，"
                    "有记录转人工决定，显式重新包含后恢复。"
                ),
                before=_session_before(session, term),
            )
        )


def _missing_enrollments(batch, scope, term, rows, plan: SnapshotPlan) -> None:
    present_codes = {row.values.get("enrollment_code") for row in rows}
    offerings = _in_scope_offerings(scope, term)
    identities = _identity_map(batch, "enrollment")
    end_date: date = batch.enrollment_end_date  # validated above
    enrollments = (
        Enrollment.objects.filter(offering__in=offerings)
        .select_related("offering", "student")
        .order_by("offering__offering_code", "student__student_no", "effective_from")
    )
    for enrollment in enrollments:
        identity = identities.get(enrollment.pk)
        if identity is None or identity.external_id in present_codes:
            continue
        if enrollment.effective_until is not None and enrollment.effective_until <= end_date:
            continue
        if end_date <= enrollment.effective_from:
            plan.issues.append(
                _scope_issue(
                    "enrollment_end_date",
                    "SNAPSHOT_ENROLLMENT_DATE_INVALID",
                    f"选课 {identity.external_id} 的生效日期为 {enrollment.effective_from.isoformat()}，"
                    f"退课生效日期 {end_date.isoformat()} 必须晚于生效日期；请修正后重新预览。",
                )
            )
            continue
        plan.enrollment_until[enrollment.pk] = end_date
        plan.missing.append(
            SnapshotMissing(
                entity_type="enrollment",
                external_id=identity.external_id,
                action="end_enrollment",
                detail=f"快照未包含该选课：设置 effective_until={end_date.isoformat()}（不含当天），保留历史。",
                before=_enrollment_before(enrollment),
            )
        )


def _missing_teachers(batch, scope, term, rows, plan: SnapshotPlan) -> None:
    present_codes = {row.values.get("teacher_code") for row in rows}
    offerings = _in_scope_offerings(scope, term)
    identities = _identity_map(batch, "teacher")
    teacher_ids = set(
        TeacherProfile.objects.filter(schedule_rules__offering__in=offerings).values_list("id", flat=True)
    )
    for teacher in TeacherProfile.objects.filter(pk__in=teacher_ids, is_active=True).order_by("teacher_no"):
        identity = identities.get(teacher.pk)
        if identity is None or identity.external_id in present_codes:
            continue
        plan.missing.append(
            SnapshotMissing(
                entity_type="teacher",
                external_id=identity.external_id,
                action="suggest_deactivate",
                detail="快照未包含该教师：只列停用建议，不自动修改账号、任课或历史课次。",
                before={"teacher_no": teacher.teacher_no, "is_active": teacher.is_active},
            )
        )


def _missing_classrooms(batch, scope, term, rows, plan: SnapshotPlan) -> None:
    present_codes = {row.values.get("classroom_code") for row in rows}
    offerings = _in_scope_offerings(scope, term)
    identities = _identity_map(batch, "classroom")
    classroom_ids = set(Classroom.objects.filter(schedule_rules__offering__in=offerings).values_list("id", flat=True))
    for classroom in Classroom.objects.filter(pk__in=classroom_ids, is_active=True).order_by("classroom_code"):
        identity = identities.get(classroom.pk)
        if identity is None or identity.external_id in present_codes:
            continue
        plan.missing.append(
            SnapshotMissing(
                entity_type="classroom",
                external_id=identity.external_id,
                action="suggest_deactivate",
                detail="快照未包含该教室：只列停用建议，不自动改历史课次或占用。",
                before={"classroom_code": classroom.classroom_code, "is_active": classroom.is_active},
            )
        )


def _missing_offerings(batch, term, rows, plan: SnapshotPlan) -> None:
    present_codes = {row.values.get("offering_code") for row in rows}
    identities = _identity_map(batch, "offering")
    offerings = CourseOffering.objects.filter(term=term).select_related("course").order_by("offering_code")
    for offering in offerings:
        identity = identities.get(offering.pk)
        if identity is None or identity.external_id in present_codes:
            continue
        plan.missing.append(
            SnapshotMissing(
                entity_type="offering",
                external_id=identity.external_id,
                action="suggest_deactivate",
                detail="快照未包含该开课：只列停用/归档建议，不自动删除课程、课次或选课历史。",
                before={
                    "offering_code": offering.offering_code,
                    "course_code": offering.course.course_code,
                    "target_hours": str(offering.target_hours),
                },
            )
        )


def snapshot_missing_summary(plan: SnapshotPlan) -> dict:
    counts: dict[str, int] = {}
    for entry in plan.missing:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    return {
        "missing_counts": counts,
        "missing_total": len(plan.missing),
        "signature": plan.signature,
        "scope": plan.scope.as_dict() if plan.scope is not None else {},
        "entries": [entry.as_dict() for entry in plan.missing],
    }
