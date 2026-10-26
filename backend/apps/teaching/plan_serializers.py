"""Plan API schemas and JSON payload builders."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.teaching.hours import hours_string
from apps.teaching.models import PlanItem, TeachingPlan


def teacher_name(teacher) -> str:
    return teacher.display_name or teacher.formal_name


def teacher_brief(teacher) -> dict[str, str]:
    return {"id": str(teacher.id), "name": teacher_name(teacher)}


def plan_item_payload(item: PlanItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "position": item.position,
        "title": item.title,
        "planned_hours": hours_string(item.planned_hours),
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "due_session_id": str(item.due_session_id) if item.due_session_id else None,
        "archived": item.archived,
    }


def plan_payload(plan: TeachingPlan | None, *, items: list[PlanItem] | None = None) -> dict[str, Any] | None:
    if plan is None:
        return None
    rows = items if items is not None else list(plan.items.all())
    return {
        "id": str(plan.id),
        "version": plan.version,
        "owner": teacher_brief(plan.owner),
        "status": plan.status,
        "items": [plan_item_payload(item) for item in rows],
    }


class PlanItemWriteSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=200, allow_blank=False)
    planned_hours = serializers.DecimalField(
        max_digits=6,
        decimal_places=1,
        min_value=Decimal("0"),
    )
    due_date = serializers.DateField(required=False, allow_null=True)
    due_session_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs.get("due_date") is not None and attrs.get("due_session_id") is not None:
            raise serializers.ValidationError("due_date 与 due_session_id 至多填写一个。")
        return attrs


class PlanWriteSerializer(serializers.Serializer):
    items = serializers.ListField(child=PlanItemWriteSerializer(), allow_empty=True)
    base_version = serializers.IntegerField(min_value=0)


class PlanTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class PlanItemOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    position = serializers.IntegerField()
    title = serializers.CharField()
    planned_hours = serializers.CharField()
    due_date = serializers.DateField(allow_null=True)
    due_session_id = serializers.UUIDField(allow_null=True)
    archived = serializers.BooleanField()


class PlanOutputSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version = serializers.IntegerField()
    owner = PlanTeacherSerializer()
    status = serializers.CharField()
    items = PlanItemOutputSerializer(many=True)


class ProgressItemSerializer(serializers.Serializer):
    plan_item_id = serializers.UUIDField()
    title = serializers.CharField()
    planned_hours = serializers.CharField()
    actual_hours = serializers.CharField()
    due = serializers.DateField(allow_null=True)
    is_due = serializers.BooleanField()
    shortfall = serializers.CharField()
    archived = serializers.BooleanField()


class OfferingProgressSerializer(serializers.Serializer):
    target_hours = serializers.CharField()
    completed_hours = serializers.CharField()
    remaining_hours = serializers.CharField()
    over_hours = serializers.CharField()
    remaining_sessions = serializers.IntegerField()
    past_pending_sessions = serializers.IntegerField()
    future_sessions = serializers.IntegerField()
    unallocated_hours = serializers.CharField()
    plan_status = serializers.ChoiceField(choices=("not_set", "on_track", "behind"))
    behind_hours = serializers.CharField()
    behind_threshold_hours = serializers.CharField()
    items = ProgressItemSerializer(many=True)


class OfferingPlanResponseSerializer(serializers.Serializer):
    plan = PlanOutputSerializer(allow_null=True)
    progress = OfferingProgressSerializer()
    can_edit = serializers.BooleanField()


class ProgressErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    current = serializers.JSONField(required=False, allow_null=True)
    allocation_total = serializers.CharField(required=False)
    session_actual_hours = serializers.CharField(required=False)
