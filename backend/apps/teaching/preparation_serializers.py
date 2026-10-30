"""HTTP/OpenAPI schemas for the AI lesson-preparation slice."""

from __future__ import annotations

from rest_framework import serializers


class PreparationPlanItemSerializer(serializers.Serializer):
    title = serializers.CharField()
    planned_hours = serializers.CharField()


class PreparationContextSerializer(serializers.Serializer):
    course_name = serializers.CharField()
    date = serializers.DateField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()
    teaching_minutes = serializers.IntegerField()
    previous_note = serializers.CharField(allow_blank=True)
    plan_items = PreparationPlanItemSerializer(many=True)


class PreparationDraftSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    topic = serializers.CharField()
    outline = serializers.CharField()
    case_study = serializers.CharField()
    discussion_questions = serializers.CharField()
    preparation = serializers.CharField()
    version = serializers.IntegerField()
    generated_model = serializers.CharField(allow_blank=True)
    generated_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField()


class PreparationResponseSerializer(serializers.Serializer):
    context = PreparationContextSerializer()
    draft = PreparationDraftSerializer(allow_null=True)
    ai_available = serializers.BooleanField()


class PreparationGenerateRequestSerializer(serializers.Serializer):
    topic = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)


class PreparationGenerateResponseSerializer(serializers.Serializer):
    topic = serializers.CharField()
    outline = serializers.CharField()
    case_study = serializers.CharField()
    discussion_questions = serializers.CharField()
    preparation = serializers.CharField()
    generated_model = serializers.CharField(allow_blank=True)
    generated_at = serializers.DateTimeField()


class PreparationSaveRequestSerializer(serializers.Serializer):
    base_version = serializers.IntegerField(min_value=0)
    topic = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)
    outline = serializers.CharField(max_length=5000, allow_blank=True)
    case_study = serializers.CharField(max_length=5000, allow_blank=True)
    discussion_questions = serializers.CharField(max_length=5000, allow_blank=True)
    preparation = serializers.CharField(max_length=5000, allow_blank=True)
    generated_model = serializers.CharField(
        max_length=100,
        allow_blank=True,
        required=False,
        default="",
    )
    generated_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class PreparationErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    current = serializers.JSONField(required=False, allow_null=True)
