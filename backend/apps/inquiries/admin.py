"""Read-only inquiry admin for academic staff."""

from __future__ import annotations

from django.contrib import admin

from apps.inquiries.models import Inquiry


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "classroom",
        "initiator",
        "from_at",
        "to_at",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = (
        "classroom__classroom_code",
        "initiator__teacher_no",
        "initiator__formal_name",
        "item_description",
    )
    ordering = ("-created_at",)
    readonly_fields = (
        "initiator",
        "classroom",
        "from_at",
        "to_at",
        "item_description",
        "status",
        "closed_at",
        "close_note",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
