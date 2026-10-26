"""Completion API schemas and current/history payload builders."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching.hours import hours_string
from apps.teaching.models import (
    CompletionAction,
    CompletionRecord,
    PlanItem,
    SessionProgress,
)
from apps.teaching.plan_serializers import teacher_brief


def _iso(value, session: ClassSession) -> str | None:
    if value is None:
        return None
    return value.astimezone(session.offering.term.tzinfo).isoformat()


def allocation_payload(progress: SessionProgress) -> dict[str, Any]:
    return {
        "plan_item_id": str(progress.plan_item_id),
        "title": progress.plan_item.title,
        "actual_hours": hours_string(progress.actual_hours),
    }


def completion_allocations(session: ClassSession) -> list[dict[str, Any]]:
    rows = (
        SessionProgress.objects.filter(session=session)
        .select_related("plan_item")
        .order_by("plan_item__position", "plan_item_id")
    )
    return [allocation_payload(row) for row in rows]


def current_completion_payload(session: ClassSession) -> dict[str, Any]:
    """The current state included with a 409 version conflict."""
    return {
        "session_id": str(session.id),
        "status": session.status,
        "actual_hours": hours_string(session.actual_hours),
        "completed_by": teacher_brief(session.completed_by_teacher) if session.completed_by_teacher_id else None,
        "completed_at": _iso(session.completed_at, session),
        "completion_ref": session.completion_ref,
        "version": session.version,
        "allocations": completion_allocations(session),
    }


def history_payload(record: CompletionRecord) -> dict[str, Any]:
    term = record.session.offering.term
    return {
        "id": str(record.id),
        "action": record.action,
        "actor": teacher_brief(record.actor),
        "actual_hours_before": hours_string(record.actual_hours_before),
        "actual_hours_after": hours_string(record.actual_hours_after),
        "allocations": record.allocations_json,
        "reason": record.reason,
        "created_at": record.created_at.astimezone(term.tzinfo).isoformat(),
    }


def available_item_payload(item: PlanItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "title": item.title,
        "planned_hours": hours_string(item.planned_hours),
    }


def completion_detail_payload(
    session: ClassSession,
    *,
    available_items: list[PlanItem],
    history: list[CompletionRecord],
) -> dict[str, Any]:
    payload = current_completion_payload(session)
    payload["available_items"] = [available_item_payload(item) for item in available_items]
    payload["history"] = [history_payload(record) for record in history]
    return payload


class AllocationWriteSerializer(serializers.Serializer):
    plan_item_id = serializers.UUIDField()
    actual_hours = serializers.DecimalField(
        max_digits=6,
        decimal_places=1,
        min_value=Decimal("0"),
    )


class CompletionWriteSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=CompletionAction.choices)
    actual_hours = serializers.DecimalField(
        max_digits=6,
        decimal_places=1,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )
    allocations = AllocationWriteSerializer(many=True, required=False)
    reason = serializers.CharField(max_length=2000, allow_blank=True, default="")
    base_version = serializers.IntegerField(min_value=1)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        action = attrs["action"]
        if action in {CompletionAction.COMPLETE, CompletionAction.CORRECT} and attrs.get("actual_hours") is None:
            raise serializers.ValidationError({"actual_hours": ["完成或更正授课时必须填写实际学时。"]})
        if action in {CompletionAction.CORRECT, CompletionAction.REVERT} and not attrs.get("reason", "").strip():
            raise serializers.ValidationError({"reason": ["更正或撤销完成必须填写原因。"]})
        attrs.setdefault("allocations", [])
        return attrs


class CompletionTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class CompletionAllocationSerializer(serializers.Serializer):
    plan_item_id = serializers.UUIDField()
    title = serializers.CharField()
    actual_hours = serializers.CharField()


class CompletionAvailableItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    planned_hours = serializers.CharField()


class AllocationSnapshotSerializer(serializers.Serializer):
    plan_item_id = serializers.UUIDField()
    title = serializers.CharField()
    actual_hours = serializers.CharField()


class CompletionHistorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=CompletionAction.choices)
    actor = CompletionTeacherSerializer()
    actual_hours_before = serializers.CharField(allow_null=True)
    actual_hours_after = serializers.CharField(allow_null=True)
    allocations = AllocationSnapshotSerializer(many=True)
    reason = serializers.CharField()
    created_at = serializers.DateTimeField()


class CompletionDetailSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=SessionStatus.choices)
    actual_hours = serializers.CharField(allow_null=True)
    completed_by = CompletionTeacherSerializer(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    completion_ref = serializers.CharField()
    version = serializers.IntegerField()
    allocations = CompletionAllocationSerializer(many=True)
    available_items = CompletionAvailableItemSerializer(many=True)
    history = CompletionHistorySerializer(many=True)


class CompletionErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    current = serializers.JSONField(required=False, allow_null=True)
    allocation_total = serializers.CharField(required=False)
    session_actual_hours = serializers.CharField(required=False)
