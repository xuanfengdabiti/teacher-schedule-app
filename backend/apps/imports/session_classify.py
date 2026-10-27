"""Classification and manual-decision construction for B12 imports.

The module owns the touched/untouched rules, completed locking, conflict
decision mapping and user-facing before/after payloads.  It only reads the
in-memory targets and the batch's preloaded conflict evaluations; no formal
table is written here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from apps.academics.models import Term
from apps.imports.projection.base import ImportObject
from apps.imports.session_targets import (
    _LAYOUT_FIELDS,
    ReconcileTarget,
    _state_payload,
    _target_diff,
)
from apps.scheduling.models import (
    ClassSession,
    RescheduleRequest,
    RescheduleStatus,
    SessionChange,
    SessionChangeSource,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.reschedule_options import ConflictDetail, TargetEvaluation
from apps.teaching.models import CompletionRecord, Handoff, TeachingNote

ACTION_NEW = "new"
ACTION_UNCHANGED = "unchanged"
ACTION_UPDATE = "update"
ACTION_CANCEL = "cancel"
ACTION_RESTORE = "restore"
ACTION_DECISION = "decision"
ACTION_KEPT_PAST = "kept_past"
ACTION_KEPT_COMPLETED = "kept_completed"

DECISION_TOUCHED = "touched_change"
DECISION_CONFLICT = "conflict"
DECISION_COMPLETED = "completed_locked"

OPTION_KEEP = "keep_existing"
OPTION_APPLY = "apply_import"

_CONFLICT_LABELS = {
    "teacher": "教师冲突",
    "student": "学生冲突",
    "classroom": "教室冲突",
}


@dataclass
class SessionAction:
    action: str
    target: ReconcileTarget
    kind: str = ""
    reasons: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    evaluation: TargetEvaluation | None = None
    current_state: dict = field(default_factory=dict)
    proposed_state: dict = field(default_factory=dict)

    @property
    def is_decision(self) -> bool:
        return self.action == ACTION_DECISION

    @property
    def needs_write(self) -> bool:
        return self.action in {ACTION_NEW, ACTION_UPDATE, ACTION_CANCEL, ACTION_RESTORE}


@dataclass
class DecisionDraft:
    session_id: UUID
    kind: str
    proposed: dict
    current: dict
    reason: str
    options: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "session_id": str(self.session_id),
            "kind": self.kind,
            "proposed": self.proposed,
            "current": self.current,
            "reason": self.reason,
            "options": list(self.options),
        }

    def signature(self) -> tuple:
        return (self.kind, self.proposed, self.current, self.options)


@dataclass
class ReconcileResult:
    actions: tuple[SessionAction, ...]
    decisions: tuple[DecisionDraft, ...]
    counts: dict[str, int]
    changes: tuple[dict, ...]
    estimated_hours: dict[str, str]

    def action_for_session(self, session_id: UUID) -> SessionAction | None:
        for action in self.actions:
            if action.target.existing is not None and action.target.existing.pk == session_id:
                return action
        return None

    def action_for_object(self, obj: ImportObject) -> SessionAction | None:
        for action in self.actions:
            if action.target.import_object is obj:
                return action
        return None


def _decision_options(kind: str) -> tuple[str, ...]:
    if kind == DECISION_COMPLETED:
        return (OPTION_KEEP,)
    return (OPTION_KEEP, OPTION_APPLY)


def _touch_sets(existing_sessions: list[ClassSession]) -> dict[str, set[UUID]]:
    ids = [session.id for session in existing_sessions]
    if not ids:
        return {
            "change": set(),
            "note": set(),
            "handoff": set(),
            "request": set(),
            "completion": set(),
        }
    handoffs = set(Handoff.objects.filter(target_session_id__in=ids).values_list("target_session_id", flat=True)) | set(
        Handoff.objects.filter(source_session_id__in=ids).values_list("source_session_id", flat=True)
    )
    return {
        # Import-originated changes are the same authority as this batch; they
        # must not permanently lock a session against a later calendar restore.
        # A B31/Admin change uses source=reschedule and still counts as touched.
        "change": set(
            SessionChange.objects.filter(session_id__in=ids)
            .exclude(source=SessionChangeSource.IMPORT)
            .values_list("session_id", flat=True)
        ),
        "note": set(TeachingNote.objects.filter(session_id__in=ids).values_list("session_id", flat=True)),
        "handoff": handoffs,
        "request": set(
            RescheduleRequest.objects.filter(
                session_id__in=ids,
                status__in=(RescheduleStatus.DRAFT, RescheduleStatus.SUBMITTED),
            ).values_list("session_id", flat=True)
        ),
        "completion": set(CompletionRecord.objects.filter(session_id__in=ids).values_list("session_id", flat=True)),
    }


def _teachers_by_session(existing_sessions: list[ClassSession]) -> dict[UUID, set[UUID]]:
    result: dict[UUID, set[UUID]] = defaultdict(set)
    rows = SessionTeacher.objects.filter(session_id__in=[session.id for session in existing_sessions]).values_list(
        "session_id", "teacher_id"
    )
    for session_id, teacher_id in rows:
        result[session_id].add(teacher_id)
    return dict(result)


def _touch_reasons(session: ClassSession, touches: dict[str, set[UUID]]) -> list[str]:
    reasons: list[str] = []
    if session.status == SessionStatus.COMPLETED:
        reasons.append("课次已完成")
    if session.id in touches["change"]:
        reasons.append("已有正式调课记录")
    if session.id in touches["note"]:
        reasons.append("已有教学备注")
    if session.id in touches["handoff"]:
        reasons.append("已有教学交接")
    if session.id in touches["request"]:
        reasons.append("有草稿或待审批调课申请")
    if session.id in touches["completion"]:
        reasons.append("已有完成记录")
    return reasons


def _conflict_reason_details(conflicts: tuple[ConflictDetail, ...]) -> list[str]:
    result: list[str] = []
    for conflict in conflicts:
        label = _CONFLICT_LABELS.get(conflict.category, conflict.category)
        detail = conflict.course_name or conflict.reason or ""
        if conflict.student_count:
            detail = f"{detail}（{conflict.student_count} 人）" if detail else f"{conflict.student_count} 人"
        result.append(f"{label}：{detail}" if detail else label)
    return result


def _decision(
    *,
    target: ReconcileTarget,
    kind: str,
    reasons: list[str],
    evaluation: TargetEvaluation | None,
    teacher_codes: dict[UUID, str],
    classroom_codes: dict[UUID, str],
    teachers_by_session: dict[UUID, set[UUID]],
    term: Term,
) -> SessionAction:
    current = _state_payload(
        session=target.existing,
        teacher_codes=teacher_codes,
        classroom_codes=classroom_codes,
        teachers_by_session=teachers_by_session,
        term=term,
        external_session_id=target.external_session_id,
    )
    proposed = _state_payload(
        target=target,
        teacher_codes=teacher_codes,
        classroom_codes=classroom_codes,
        teachers_by_session=teachers_by_session,
        term=term,
        external_session_id=target.external_session_id,
    )
    return SessionAction(
        action=ACTION_DECISION,
        target=target,
        kind=kind,
        reasons=tuple(dict.fromkeys(reasons)),
        options=_decision_options(kind),
        evaluation=evaluation,
        current_state=current,
        proposed_state=proposed,
    )


def _classify(
    *,
    target: ReconcileTarget,
    term: Term,
    touches: dict[str, set[UUID]],
    teacher_codes: dict[UUID, str],
    classroom_codes: dict[UUID, str],
    teachers_by_session: dict[UUID, set[UUID]],
    evaluation: TargetEvaluation | None,
    now: datetime,
) -> SessionAction:
    existing = target.existing
    if existing is None:
        return SessionAction(
            action=ACTION_NEW,
            target=target,
            current_state={},
            proposed_state=_state_payload(
                target=target,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
                external_session_id=target.external_session_id,
            ),
        )

    diff = _target_diff(existing, target, term, teachers_by_session)
    if not diff:
        return SessionAction(action=ACTION_UNCHANGED, target=target)

    touch_reasons = _touch_reasons(existing, touches)
    past = existing.start_at <= now
    conflict_reasons = _conflict_reason_details(evaluation.conflicts) if evaluation is not None else []

    if existing.status == SessionStatus.COMPLETED:
        if diff & (_LAYOUT_FIELDS | {"status", "actual_hours", "completed_by", "completed_at", "completion_ref"}):
            return _decision(
                target=target,
                kind=DECISION_COMPLETED,
                reasons=[
                    "课次已完成，普通增量导入不能移动、取消或覆盖完成量；只能保留现状。",
                    *touch_reasons,
                ],
                evaluation=None,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        return SessionAction(action=ACTION_KEPT_COMPLETED, target=target)

    if target.status == SessionStatus.CANCELLED:
        if existing.status == SessionStatus.CANCELLED:
            return SessionAction(action=ACTION_UNCHANGED, target=target)
        if touch_reasons:
            return _decision(
                target=target,
                kind=DECISION_TOUCHED,
                reasons=[*touch_reasons, "导入目标为取消课次"],
                evaluation=None,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        if past and not (target.explicit_change or target.from_row):
            return SessionAction(action=ACTION_KEPT_PAST, target=target)
        return SessionAction(action=ACTION_CANCEL, target=target)

    if existing.status == SessionStatus.CANCELLED and target.status == SessionStatus.COMPLETED:
        return _decision(
            target=target,
            kind=DECISION_COMPLETED,
            reasons=["已取消课次不能直接改为已完成；只能保留现状或先恢复课次再按教学流程确认完成。"],
            evaluation=None,
            teacher_codes=teacher_codes,
            classroom_codes=classroom_codes,
            teachers_by_session=teachers_by_session,
            term=term,
        )

    if existing.status == SessionStatus.CANCELLED and target.status == SessionStatus.SCHEDULED:
        if touch_reasons:
            return _decision(
                target=target,
                kind=DECISION_TOUCHED,
                reasons=[*touch_reasons, "导入要求恢复已取消课次"],
                evaluation=evaluation,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        if conflict_reasons:
            return _decision(
                target=target,
                kind=DECISION_CONFLICT,
                reasons=[f"导入目标时段存在已知冲突：{'；'.join(conflict_reasons)}"],
                evaluation=evaluation,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        return SessionAction(action=ACTION_RESTORE, target=target, evaluation=evaluation)

    if target.status == SessionStatus.COMPLETED and existing.status == SessionStatus.SCHEDULED:
        if touch_reasons:
            return _decision(
                target=target,
                kind=DECISION_TOUCHED,
                reasons=[*touch_reasons, "导入要求更新完成量"],
                evaluation=None,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        return SessionAction(action=ACTION_UPDATE, target=target)

    # scheduled -> scheduled: a rule/calendar move or a teacher/hours correction.
    if diff & _LAYOUT_FIELDS:
        if touch_reasons:
            if conflict_reasons:
                return _decision(
                    target=target,
                    kind=DECISION_CONFLICT,
                    reasons=[
                        f"导入目标时段存在已知冲突：{'；'.join(conflict_reasons)}",
                        *touch_reasons,
                    ],
                    evaluation=evaluation,
                    teacher_codes=teacher_codes,
                    classroom_codes=classroom_codes,
                    teachers_by_session=teachers_by_session,
                    term=term,
                )
            return _decision(
                target=target,
                kind=DECISION_TOUCHED,
                reasons=[*touch_reasons, "导入目标与原安排不同"],
                evaluation=evaluation,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        if past and not (target.explicit_change or target.from_row):
            return SessionAction(action=ACTION_KEPT_PAST, target=target)
        if conflict_reasons:
            return _decision(
                target=target,
                kind=DECISION_CONFLICT,
                reasons=[f"导入目标时段存在已知冲突：{'；'.join(conflict_reasons)}"],
                evaluation=evaluation,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                term=term,
            )
        return SessionAction(action=ACTION_UPDATE, target=target, evaluation=evaluation)

    if conflict_reasons:
        return _decision(
            target=target,
            kind=DECISION_CONFLICT,
            reasons=[
                f"导入目标时段存在已知冲突：{'；'.join(conflict_reasons)}",
                *touch_reasons,
            ],
            evaluation=evaluation,
            teacher_codes=teacher_codes,
            classroom_codes=classroom_codes,
            teachers_by_session=teachers_by_session,
            term=term,
        )

    if touch_reasons:
        return _decision(
            target=target,
            kind=DECISION_TOUCHED,
            reasons=[*touch_reasons, "导入目标与原安排不同"],
            evaluation=evaluation,
            teacher_codes=teacher_codes,
            classroom_codes=classroom_codes,
            teachers_by_session=teachers_by_session,
            term=term,
        )
    return SessionAction(action=ACTION_UPDATE, target=target, evaluation=evaluation)
