"""Serializers for the read-only reschedule-options endpoint (B30)."""

from __future__ import annotations

from rest_framework import serializers

from apps.academics.models import CoverageKind
from apps.scheduling.reschedule_options import (
    CONFLICT_CLASSROOM,
    CONFLICT_STUDENT,
    CONFLICT_TEACHER,
    ELIGIBILITY_AVAILABLE,
    ELIGIBILITY_CONFLICT,
    ELIGIBILITY_UNVERIFIED,
)

_CONFLICT_CATEGORIES = (CONFLICT_TEACHER, CONFLICT_STUDENT, CONFLICT_CLASSROOM)
_ELIGIBILITIES = (ELIGIBILITY_AVAILABLE, ELIGIBILITY_CONFLICT, ELIGIBILITY_UNVERIFIED)


class RescheduleOptionsQuerySerializer(serializers.Serializer):
    """Input contract; the date defaults are filled by the view."""

    date_from = serializers.DateField(required=False)
    date_until = serializers.DateField(required=False)
    allowed_block_codes = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
    )
    classroom_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
    )
    allow_weekend = serializers.BooleanField(required=False, default=False)


class RescheduleOptionsErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class ConflictDetailSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=_CONFLICT_CATEGORIES)
    session_id = serializers.UUIDField(allow_null=True)
    course_name = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_null=True)
    student_count = serializers.IntegerField(allow_null=True)


class RescheduleOptionSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()
    classroom_id = serializers.UUIDField()
    classroom_code = serializers.CharField()
    eligibility = serializers.ChoiceField(choices=_ELIGIBILITIES)
    student_conflict_count = serializers.IntegerField()
    conflicts = ConflictDetailSerializer(many=True)
    missing_data = serializers.ListField(child=serializers.ChoiceField(choices=CoverageKind.choices))
    reasons = serializers.ListField(child=serializers.CharField())


class RescheduleOptionsResponseSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    schedule_version = serializers.IntegerField()
    checked_at = serializers.DateTimeField(default_timezone=None)
    data_completeness = serializers.ChoiceField(choices=("complete", "partial"))
    options = RescheduleOptionSerializer(many=True)
