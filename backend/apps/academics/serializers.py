"""Contact endpoint schemas (B32)."""

from __future__ import annotations

from rest_framework import serializers

from apps.academics.contacts import CONTACT_STATUS_CHOICES


class ContactTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    department = serializers.CharField(allow_blank=True)


class TeacherContactSerializer(serializers.Serializer):
    teacher = ContactTeacherSerializer()
    phone = serializers.CharField(allow_null=True)
    phone_status = serializers.ChoiceField(choices=CONTACT_STATUS_CHOICES)
    wechat = serializers.CharField(allow_null=True)
    wechat_status = serializers.ChoiceField(choices=CONTACT_STATUS_CHOICES)


class ContactErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
