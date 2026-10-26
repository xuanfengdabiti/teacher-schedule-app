"""OpenAPI contracts for classroom catalogue and usage history (B32)."""

from __future__ import annotations

from rest_framework import serializers

from apps.classrooms.selectors import (
    DATA_COMPLETE,
    DATA_INCOMPLETE,
    ENTRY_CANCELLED_SESSION,
    ENTRY_MOVED_OUT,
    ENTRY_ROOM_BLOCK,
    ENTRY_SESSION,
    USAGE_NOTICE,
)
from apps.scheduling.models import SessionStatus


class ClassroomSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    classroom_code = serializers.CharField()
    campus = serializers.CharField()
    building = serializers.CharField()
    room_no = serializers.CharField()
    capacity = serializers.IntegerField()
    is_active = serializers.BooleanField()


class ClassroomUsageTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    has_visible_contact = serializers.BooleanField()


class ClassroomUsageEntrySerializer(serializers.Serializer):
    """Heterogeneous entry; fields irrelevant to ``kind`` are absent.

    ``moved_out`` uses ``start_at``/``end_at`` for the original slot in this
    room and ``current_*`` for the current slot and room.  ``session`` and
    ``cancelled_session`` use ``start_at``/``end_at`` directly.
    """

    kind = serializers.ChoiceField(
        choices=(
            (ENTRY_SESSION, "课次"),
            (ENTRY_CANCELLED_SESSION, "停课课次"),
            (ENTRY_MOVED_OUT, "已调出课次"),
            (ENTRY_ROOM_BLOCK, "教室占用"),
        )
    )
    session_id = serializers.UUIDField(required=False)
    room_block_id = serializers.UUIDField(required=False)
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    start_period = serializers.IntegerField(required=False)
    end_period = serializers.IntegerField(required=False)
    course_name = serializers.CharField(required=False)
    teaching_class = serializers.CharField(required=False)
    status = serializers.ChoiceField(choices=SessionStatus.choices, required=False)
    status_reason = serializers.CharField(required=False, allow_blank=True)
    is_moved = serializers.BooleanField(required=False)
    teachers = ClassroomUsageTeacherSerializer(many=True, required=False)
    current_start_at = serializers.DateTimeField(required=False)
    current_end_at = serializers.DateTimeField(required=False)
    current_start_period = serializers.IntegerField(required=False)
    current_end_period = serializers.IntegerField(required=False)
    current_classroom_id = serializers.UUIDField(required=False)
    current_classroom_code = serializers.CharField(required=False)
    reason = serializers.CharField(required=False)
    block_code = serializers.CharField(required=False)


class PaginatedClassroomSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ClassroomSerializer(many=True)


class ClassroomUsageSerializer(serializers.Serializer):
    classroom = ClassroomSerializer()
    date = serializers.DateField()
    notice = serializers.CharField(default=USAGE_NOTICE)
    data_completeness = serializers.ChoiceField(choices=(DATA_COMPLETE, DATA_INCOMPLETE))
    entries = ClassroomUsageEntrySerializer(many=True)


class ClassroomQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=200)
    campus = serializers.CharField(required=False, allow_blank=True, max_length=100)
    building = serializers.CharField(required=False, allow_blank=True, max_length=100)


class ClassroomUsageQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=True)


class ClassroomUsageErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
