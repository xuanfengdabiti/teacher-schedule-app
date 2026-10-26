"""Serializers for the notification centre and device registration APIs."""

from __future__ import annotations

from rest_framework import serializers

from apps.notifications.models import DevicePlatform, DeviceProvider


class NotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.CharField()
    object_type = serializers.CharField()
    object_id = serializers.UUIDField(allow_null=True)
    title = serializers.CharField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()
    read_at = serializers.DateTimeField(allow_null=True)
    is_read = serializers.SerializerMethodField()

    def get_is_read(self, obj) -> bool:
        return obj.read_at is not None


class NotificationListResponseSerializer(serializers.Serializer):
    results = NotificationSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    unread_count = serializers.IntegerField()


class NotificationQuerySerializer(serializers.Serializer):
    cursor = serializers.CharField(required=False, allow_blank=False)
    limit = serializers.IntegerField(required=False, default=20, min_value=1, max_value=50)


class DeviceInputSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=DeviceProvider.choices)
    provider_client_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=255,
    )
    platform = serializers.ChoiceField(choices=DevicePlatform.choices)
    enabled = serializers.BooleanField(required=False, default=True)

    def validate_provider_client_id(self, value: str | None) -> str | None:
        # An empty string means "no provider token yet", which is distinct from
        # a real client id and should be stored as NULL.
        return value or None


class DeviceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    installation_id = serializers.CharField()
    provider = serializers.CharField()
    provider_client_id = serializers.CharField(allow_null=True)
    platform = serializers.CharField()
    enabled = serializers.BooleanField()
    last_seen_at = serializers.DateTimeField()
