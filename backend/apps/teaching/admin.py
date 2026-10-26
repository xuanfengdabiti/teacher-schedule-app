"""Read-only Admin listing for teaching handoffs.

Academic staff may inspect delivery and acknowledgement state but must not edit
handoffs directly: every transition has a service with its own notification and
locking rules.  This matches the role matrix's "查看状态" scope.
"""

from __future__ import annotations

from django.contrib import admin

from apps.teaching.models import Handoff


@admin.register(Handoff)
class HandoffAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "sender",
        "recipient",
        "status",
        "created_at",
        "read_at",
        "acknowledged_at",
        "withdrawn_at",
    )
    list_filter = ("status", "created_at", "read_at", "acknowledged_at")
    search_fields = (
        "offering__offering_code",
        "offering__course__name",
        "offering__teaching_class__name",
        "sender__teacher_no",
        "sender__formal_name",
        "recipient__teacher_no",
        "recipient__formal_name",
        "content_snapshot",
    )
    list_select_related = (
        "offering__course",
        "offering__teaching_class",
        "sender",
        "recipient",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "offering",
        "sender",
        "recipient",
        "source_session",
        "target_session",
        "note",
        "note_published_version",
        "content_snapshot",
        "status",
        "read_at",
        "acknowledged_at",
        "withdrawn_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
