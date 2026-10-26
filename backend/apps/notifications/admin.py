"""Read-only Admin listings for notification operations and failure triage."""

from __future__ import annotations

from django.contrib import admin

from apps.notifications.models import DeliveryAttempt, DeviceRegistration, Notification


class ReadOnlyAdminMixin:
    """Staff can inspect rows but cannot bypass the notification service."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("recipient", "type", "title", "created_at", "read_at")
    list_filter = ("type", "created_at", "read_at")
    search_fields = ("recipient__teacher_no", "recipient__formal_name", "title", "event_key")
    list_select_related = ("recipient",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(DeviceRegistration)
class DeviceRegistrationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("installation_id", "user", "provider", "platform", "enabled", "last_seen_at")
    list_filter = ("provider", "platform", "enabled")
    search_fields = ("installation_id", "user__email", "provider_client_id")
    list_select_related = ("user",)
    ordering = ("-last_seen_at",)


@admin.register(DeliveryAttempt)
class DeliveryAttemptAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "notification",
        "device",
        "channel",
        "attempt_no",
        "status",
        "next_attempt_at",
        "provider_message_id",
        "error_code",
    )
    list_filter = ("status", "channel", "next_attempt_at")
    search_fields = (
        "notification__title",
        "notification__recipient__teacher_no",
        "device__installation_id",
        "provider_message_id",
        "error_code",
    )
    list_select_related = ("notification", "device")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
