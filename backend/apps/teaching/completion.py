"""Transactional lesson-completion service (B22).

One completed class contributes its single ``actual_hours`` value once, no
matter how many teachers confirm or write notes.  Topic allocations are replaced
as a whole and may be less than the session total; the remainder is reported as
unallocated by the progress service.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone as django_timezone

from apps.academics.models import TeacherProfile, Term
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import (
    ChangeEntityType,
    ClassSession,
    SchoolSyncState,
    SessionStatus,
    SessionTeacher,
)
from apps.teaching.completion_serializers import current_completion_payload
from apps.teaching.exceptions import (
    allocation_exceeds,
    completion_not_session_teacher,
    invalid_allocation,
    invalid_completion,
    invalid_completion_transition,
    session_version_conflict,
)
from apps.teaching.hours import decimal_hours, hours_string
from apps.teaching.models import (
    CompletionAction,
    CompletionRecord,
    PlanItem,
    SessionProgress,
    TeachingPlan,
)

MAX_REASON_LENGTH = 2000


def _now(value: datetime) -> datetime:
    if django_timezone.is_naive(value):
        raise ValueError("now must be a timezone-aware datetime")
    return value


def _clean_reason(reason: str | None, *, required: bool) -> str:
    cleaned = (reason or "").strip()
    if required and not cleaned:
        raise invalid_completion("更正或撤销完成必须填写原因。", field_errors={"reason": ["原因不能为空。"]})
    if len(cleaned) > MAX_REASON_LENGTH:
        raise invalid_completion(
            f"原因不能超过 {MAX_REASON_LENGTH} 个字符。",
            field_errors={"reason": [f"原因不能超过 {MAX_REASON_LENGTH} 个字符。"]},
        )
    return cleaned


def _allocation_rows(
    offering_id: UUID,
    allocations: list[dict[str, Any]] | None,
) -> tuple[list[tuple[PlanItem, Decimal]], list[dict[str, str]]]:
    rows = allocations or []
    if not isinstance(rows, list):
        raise invalid_allocation("allocations 必须是数组。")

    requested: list[tuple[UUID, Decimal]] = []
    seen: set[UUID] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise invalid_allocation("每个专题分配必须是对象。")
        try:
            item_id = UUID(str(raw.get("plan_item_id")))
        except (TypeError, ValueError):
            raise invalid_allocation("plan_item_id 格式不正确。") from None
        if item_id in seen:
            raise invalid_allocation("同一专题不能重复分配。")
        seen.add(item_id)
        try:
            actual_hours = decimal_hours(raw.get("actual_hours"), field="allocations.actual_hours")
        except ValueError as exc:
            raise invalid_allocation(str(exc)) from None
        requested.append((item_id, actual_hours))

    if not requested:
        return [], []

    plan = TeachingPlan.objects.filter(offering_id=offering_id).first()
    if plan is None:
        raise invalid_allocation("该课程尚未设置教学计划，不能分配专题学时。")
    items = {item.id: item for item in PlanItem.objects.filter(plan=plan, id__in=[item_id for item_id, _ in requested])}
    missing = {item_id for item_id, _ in requested} - set(items)
    if missing:
        raise invalid_allocation("专题不属于该课程的教学计划。")
    if any(items[item_id].archived for item_id, _ in requested):
        raise invalid_allocation("已归档专题不能接受新的学时分配。")

    result: list[tuple[PlanItem, Decimal]] = []
    snapshot: list[dict[str, str]] = []
    for item_id, actual_hours in requested:
        item = items[item_id]
        result.append((item, actual_hours))
        snapshot.append(
            {
                "plan_item_id": str(item.id),
                "title": item.title,
                "actual_hours": hours_string(actual_hours),
            }
        )
    return result, snapshot


def record_completion(
    teacher: TeacherProfile,
    session: ClassSession,
    *,
    action: str,
    actual_hours: Decimal | str | int | None,
    allocations: list[dict[str, Any]] | None,
    reason: str | None,
    base_version: int,
    now: datetime,
) -> ClassSession:
    """Complete, correct or revert one session and append its history row.

    Lock order is deliberately **SchoolSyncState -> Term -> ClassSession**,
    matching generation, imports and rescheduling.  Holding the session row lock
    is what makes two simultaneous confirmations resolve to exactly one winner:
    the second transaction reloads the new version and returns 409.
    """
    now = _now(now)
    try:
        action_value = CompletionAction(action)
    except ValueError:
        raise invalid_completion("action 必须是 complete、correct 或 revert。") from None

    reason_required = action_value in {CompletionAction.CORRECT, CompletionAction.REVERT}
    cleaned_reason = _clean_reason(reason, required=reason_required)

    # The passed instance came from a select_related read in the view/service
    # caller.  Locking starts with the school row, then the term, then the one
    # session, so this writer cannot deadlock with rescheduling or generation.
    with transaction.atomic():
        school_id = session.offering.term.school_id
        term_id = session.offering.term_id
        SchoolSyncState.objects.get_or_create(school_id=school_id)
        SchoolSyncState.objects.select_for_update().get(school_id=school_id)
        Term.objects.select_for_update().get(pk=term_id)
        locked = (
            ClassSession.objects.select_for_update(of=("self",))
            .select_related(
                "offering__term",
                "offering__course",
                "completed_by_teacher",
            )
            .get(pk=session.pk)
        )

        if not SessionTeacher.objects.filter(session=locked, teacher=teacher).exists():
            raise completion_not_session_teacher()
        if base_version != locked.version:
            raise session_version_conflict(current_completion_payload(locked))

        before_hours = locked.actual_hours
        after_hours: Decimal | None
        snapshot: list[dict[str, str]]

        if action_value == CompletionAction.COMPLETE:
            if locked.status == SessionStatus.CANCELLED:
                raise invalid_completion_transition("已取消课次不能确认完成。")
            if locked.status == SessionStatus.COMPLETED:
                raise invalid_completion_transition("该课次已完成，只能更正或撤销，不能再次确认完成。")
            if locked.start_at > now:
                raise invalid_completion_transition("课次尚未开始，不能提前确认完成。")
            if actual_hours is None:
                raise invalid_completion("必须填写实际学时。", field_errors={"actual_hours": ["必填。"]})
            try:
                after_hours = decimal_hours(actual_hours)
            except ValueError as exc:
                raise invalid_completion(str(exc), field_errors={"actual_hours": [str(exc)]}) from None
            allocation_rows, snapshot = _allocation_rows(locked.offering_id, allocations)
            allocation_total = sum((hours for _, hours in allocation_rows), Decimal("0.0"))
            if allocation_total > after_hours:
                raise allocation_exceeds(allocation_total, after_hours)
            locked.status = SessionStatus.COMPLETED

        elif action_value == CompletionAction.CORRECT:
            if locked.status != SessionStatus.COMPLETED:
                raise invalid_completion_transition("只有已完成课次可以更正。")
            if actual_hours is None:
                raise invalid_completion("更正时必须填写实际学时。", field_errors={"actual_hours": ["必填。"]})
            try:
                after_hours = decimal_hours(actual_hours)
            except ValueError as exc:
                raise invalid_completion(str(exc), field_errors={"actual_hours": [str(exc)]}) from None
            allocation_rows, snapshot = _allocation_rows(locked.offering_id, allocations)
            allocation_total = sum((hours for _, hours in allocation_rows), Decimal("0.0"))
            if allocation_total > after_hours:
                raise allocation_exceeds(allocation_total, after_hours)
            locked.status = SessionStatus.COMPLETED

        else:  # REVERT
            if locked.status != SessionStatus.COMPLETED:
                raise invalid_completion_transition("只有已完成课次可以撤销完成。")
            after_hours = None
            snapshot = []
            allocation_rows = []
            locked.status = SessionStatus.SCHEDULED

        record = CompletionRecord(
            session=locked,
            actor=teacher,
            action=action_value,
            actual_hours_before=before_hours,
            actual_hours_after=after_hours,
            allocations_json=snapshot,
            reason=cleaned_reason,
        )

        locked.actual_hours = after_hours
        locked.version += 1
        if action_value == CompletionAction.REVERT:
            locked.completed_by_teacher = None
            locked.completed_at = None
            locked.completion_ref = ""
            locked.recorded_by_user = None
        else:
            locked.completed_by_teacher = teacher
            locked.completed_at = now
            locked.completion_ref = f"app:{record.id}"
            locked.recorded_by_user_id = teacher.user_id

        locked.save(
            update_fields=[
                "status",
                "actual_hours",
                "completed_by_teacher",
                "completed_at",
                "completion_ref",
                "recorded_by_user",
                "version",
                "updated_at",
            ]
        )

        SessionProgress.objects.filter(session=locked).delete()
        if allocation_rows:
            SessionProgress.objects.bulk_create(
                [
                    SessionProgress(
                        session=locked,
                        plan_item=item,
                        actual_hours=hours,
                    )
                    for item, hours in allocation_rows
                ]
            )

        record.save()
        record_changes(
            school_id,
            [
                ChangeEntry(
                    entity_type=ChangeEntityType.SESSION,
                    entity_id=locked.id,
                    version=locked.version,
                    offering_id=locked.offering_id,
                )
            ],
        )

    return locked
