"""Transactional service for editing one offering's teaching plan.

Every save increments the plan version and appends a full ``PlanRevision``.
Items removed from the request are deleted only when no session hours reference
them; otherwise they become archived so historical allocations stay auditable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone as django_timezone

from apps.academics.models import CourseOffering, TeacherProfile
from apps.scheduling.models import ClassSession
from apps.teaching.exceptions import (
    invalid_plan,
    not_plan_owner,
    plan_version_conflict,
)
from apps.teaching.hours import decimal_hours, hours_string
from apps.teaching.models import (
    PlanItem,
    PlanRevision,
    SessionProgress,
    TeachingPlan,
    TeachingPlanStatus,
)
from apps.teaching.plan_serializers import plan_payload

POSITION_OFFSET = 1_000_000


def _now(value: datetime) -> datetime:
    if django_timezone.is_naive(value):
        raise ValueError("now must be a timezone-aware datetime")
    return value


def _uuid(value: Any, *, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        raise invalid_plan(f"{field} 不是合法的 UUID。", field_errors={field: ["格式不正确。"]}) from None


def _clean_title(value: Any) -> str:
    title = str(value or "").strip()
    if not title:
        raise invalid_plan("专题标题不能为空。", field_errors={"items": ["专题标题不能为空。"]})
    if len(title) > 200:
        raise invalid_plan("专题标题不能超过 200 个字符。", field_errors={"items": ["专题标题过长。"]})
    return title


def _clean_due_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise invalid_plan("due_date 必须是 YYYY-MM-DD。", field_errors={"items": ["日期格式不正确。"]}) from None


def _normalise_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise invalid_plan("items 必须是数组。", field_errors={"items": ["必须是数组。"]})
    normalised: list[dict[str, Any]] = []
    seen_ids: set[UUID] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise invalid_plan("每个专题必须是对象。", field_errors={"items": ["条目格式不正确。"]})
        item_id = _uuid(raw["id"], field="id") if raw.get("id") else None
        if item_id is not None:
            if item_id in seen_ids:
                raise invalid_plan("同一专题不能在一个请求中出现两次。", field_errors={"items": ["专题 ID 重复。"]})
            seen_ids.add(item_id)
        due_date = _clean_due_date(raw.get("due_date"))
        due_session_id = (
            _uuid(raw.get("due_session_id"), field="due_session_id")
            if raw.get("due_session_id") not in (None, "")
            else None
        )
        if due_date is not None and due_session_id is not None:
            raise invalid_plan(
                "due_date 与 due_session_id 至多填写一个。",
                field_errors={"items": ["计划日期与计划课次只能填写一个。"]},
            )
        try:
            planned_hours = decimal_hours(raw.get("planned_hours"), field="planned_hours")
        except ValueError as exc:
            raise invalid_plan(str(exc), field_errors={"items": [str(exc)]}) from None
        normalised.append(
            {
                "id": item_id,
                "title": _clean_title(raw.get("title")),
                "planned_hours": planned_hours,
                "due_date": due_date,
                "due_session_id": due_session_id,
            }
        )
    return normalised


def _snapshot(items: list[PlanItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.id),
            "position": item.position,
            "title": item.title,
            "planned_hours": hours_string(item.planned_hours),
            "due_date": item.due_date.isoformat() if item.due_date else None,
            "due_session_id": str(item.due_session_id) if item.due_session_id else None,
            "archived": item.archived,
        }
        for item in items
    ]


def _validate_due_sessions(offering: CourseOffering, normalised: list[dict[str, Any]]) -> dict[UUID, UUID]:
    requested = {row["due_session_id"] for row in normalised if row["due_session_id"] is not None}
    if not requested:
        return {}
    found = set(ClassSession.objects.filter(offering=offering, id__in=requested).values_list("id", flat=True))
    missing = requested - found
    if missing:
        raise invalid_plan(
            "计划完成课次必须属于该课程开设。",
            field_errors={"items": ["存在不属于该 offering 的计划课次。"]},
        )
    return {session_id: session_id for session_id in found}


def save_plan(
    teacher: TeacherProfile,
    offering: CourseOffering,
    *,
    items: list[dict[str, Any]],
    base_version: int,
    now: datetime,
) -> TeachingPlan:
    """Create or replace a plan under one transaction and append its revision."""
    _now(now)
    normalised = _normalise_items(items)

    with transaction.atomic():
        # Plan creation can race two first saves.  Locking the offering row makes
        # base_version=0 resolve to exactly one creator; plan edits then use the
        # plan row's own version.  Plans are not part of the App ChangeLog.
        locked_offering = (
            CourseOffering.objects.select_for_update(of=("self",))
            .select_related("term", "course", "teaching_class", "lead_teacher")
            .get(pk=offering.pk)
        )
        if locked_offering.lead_teacher_id != teacher.id:
            raise not_plan_owner()

        due_sessions = _validate_due_sessions(locked_offering, normalised)
        plan = (
            TeachingPlan.objects.select_for_update(of=("self",))
            .select_related("owner")
            .filter(offering=locked_offering)
            .first()
        )
        if plan is None:
            if base_version != 0:
                raise plan_version_conflict(None)
            plan = TeachingPlan.objects.create(
                offering=locked_offering,
                owner=teacher,
                version=1,
                status=TeachingPlanStatus.ACTIVE,
            )
        else:
            if base_version != plan.version:
                raise plan_version_conflict(plan_payload(plan))
            plan.version += 1
            plan.owner = teacher

        existing_items = list(
            plan.items.select_for_update(of=("self",))
            .select_related("due_session")
            .annotate(
                has_progress=Exists(SessionProgress.objects.filter(plan_item=OuterRef("pk"))),
            )
            .order_by("position", "id")
        )
        existing_by_id = {item.id: item for item in existing_items}
        provided_ids = {row["id"] for row in normalised if row["id"] is not None}
        unknown = provided_ids - set(existing_by_id)
        if unknown:
            raise invalid_plan(
                "请求中的专题不属于该教学计划。",
                field_errors={"items": ["存在未知的专题 ID。"]},
            )

        # Move every existing position out of the final 1..N range first.  This
        # lets items swap order without transiently violating the unique
        # (plan, position) constraint.
        plan.items.update(position=F("position") + POSITION_OFFSET)

        kept_archived: list[PlanItem] = []
        for item in existing_items:
            if item.id in provided_ids:
                continue
            if item.has_progress:
                item.archived = True
                kept_archived.append(item)
            else:
                item.delete()

        active_items: list[PlanItem] = []
        for position, row in enumerate(normalised, start=1):
            item = existing_by_id.get(row["id"]) if row["id"] is not None else None
            if item is None:
                item = PlanItem(plan=plan)
            item.position = position
            item.title = row["title"]
            item.planned_hours = row["planned_hours"]
            item.due_date = row["due_date"]
            item.due_session_id = due_sessions.get(row["due_session_id"])
            item.archived = False
            item.save()
            active_items.append(item)

        archived_items = sorted(kept_archived, key=lambda item: (item.position, str(item.id)))
        for offset, item in enumerate(archived_items, start=len(active_items) + 1):
            item.position = offset
            item.archived = True
            item.save(update_fields=["position", "archived", "updated_at"])

        plan.save(update_fields=["owner", "version", "status", "updated_at"])
        all_items = list(plan.items.select_related("due_session").order_by("position", "id"))
        PlanRevision.objects.create(
            plan=plan,
            version=plan.version,
            items_json=_snapshot(all_items),
            saved_by=teacher,
            saved_at=now,
        )

    return plan
