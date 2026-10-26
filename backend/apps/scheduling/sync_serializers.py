"""OpenAPI serializers for the B14 sync contract."""

from __future__ import annotations

from rest_framework import serializers

from apps.scheduling.serializers import (
    OfferingSerializer,
    SessionSummarySerializer,
    TermCalendarSerializer,
)


class SyncBootstrapResponseSerializer(serializers.Serializer):
    cursor = serializers.CharField()
    assignment_scope_version = serializers.IntegerField()
    term = TermCalendarSerializer(allow_null=True)
    offerings = OfferingSerializer(many=True)
    sessions = SessionSummarySerializer(many=True)
    generated_at = serializers.DateTimeField(default_timezone=None)


class SyncChangesResponseSerializer(serializers.Serializer):
    next_cursor = serializers.CharField()
    has_more = serializers.BooleanField()
    assignment_scope_version = serializers.IntegerField()
    reset_required = serializers.BooleanField()
    # A session item is either a SessionSummary or {"id", "tombstone": true};
    # the concrete shape depends on the current database row, not the log entry.
    sessions = serializers.ListField(child=serializers.DictField())
    terms = TermCalendarSerializer(many=True)
