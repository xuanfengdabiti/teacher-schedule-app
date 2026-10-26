"""Admin registration for scheduling data.

``ClassSession`` is view-only in Admin: formal schedule changes go through B31
and completion corrections (with a reason) through B22.  B11 only exposes the
rule-expansion entry point on ``Term``.
"""

from django import forms
from django.contrib import admin

from apps.academics.selectors import assigned_teacher_ids
from apps.scheduling.models import (
    ClassSession,
    PersonBlock,
    ScheduleRule,
    SchoolSyncState,
    SessionTeacher,
)


class ScheduleRuleAdminForm(forms.ModelForm):
    """Validate the M2M teacher scope before the inline rows are saved.

    ``Model.clean()`` cannot see newly selected M2M rows on an unsaved instance,
    so the Admin form checks the same offering-scope rule the generation service
    enforces.
    """

    class Meta:
        model = ScheduleRule
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        offering = cleaned.get("offering")
        teachers = cleaned.get("teachers")
        if offering and teachers is not None:
            allowed = assigned_teacher_ids(offering)
            extra = [teacher.teacher_no for teacher in teachers if teacher.pk not in allowed]
            if extra:
                self.add_error("teachers", f"以下教师不在该课程开设的任课范围内：{'、'.join(extra)}。")
        return cleaned


class SessionTeacherInline(admin.TabularInline):
    """Display the explicit session/teacher links.

    Read-only on purpose: changing a session's teachers is a formal schedule
    change and must go through the scheduling service, not the Admin.
    """

    model = SessionTeacher
    fk_name = "session"
    extra = 0
    can_delete = False
    readonly_fields = ("teacher",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ScheduleRule)
class ScheduleRuleAdmin(admin.ModelAdmin):
    form = ScheduleRuleAdminForm
    list_display = (
        "rule_code",
        "offering",
        "weekday",
        "start_period",
        "end_period",
        "classroom",
        "planned_hours",
    )
    list_filter = ("offering__term", "weekday", "classroom")
    search_fields = ("rule_code", "offering__offering_code", "offering__course__name")
    list_select_related = ("offering", "classroom")
    filter_horizontal = ("teachers",)
    autocomplete_fields = ("classroom",)


@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "start_at",
        "end_at",
        "classroom",
        "status",
        "planned_hours",
        "actual_hours",
        "version",
    )
    list_filter = ("status", "offering__term", "classroom")
    search_fields = (
        "offering__offering_code",
        "offering__course__name",
        "rule__rule_code",
        "classroom__classroom_code",
    )
    list_select_related = ("offering", "classroom", "rule")
    date_hierarchy = "start_at"
    ordering = ("-start_at",)
    inlines = (SessionTeacherInline,)

    # ClassSession is view-only in Admin.  Completion corrections need a reason
    # and B22's teaching service; formal schedule changes belong to B31.  Keeping
    # a second editable Admin entry point would bypass both services.
    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_add_permission(self, request) -> bool:
        # Sessions are created from rules by the scheduling service; hand-made
        # rows would bypass generation keys, locking and version bumps.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Session IDs are stable teaching history; cancellation and formal
        # changes belong to the scheduling service, not Admin deletion.
        return False


@admin.register(PersonBlock)
class PersonBlockAdmin(admin.ModelAdmin):
    list_display = ("teacher", "student", "start_at", "end_at", "reason", "block_code")
    list_filter = ("teacher__school",)
    search_fields = (
        "block_code",
        "reason",
        "teacher__teacher_no",
        "student__student_no",
    )
    list_select_related = ("teacher", "student")
    date_hierarchy = "start_at"


@admin.register(SchoolSyncState)
class SchoolSyncStateAdmin(admin.ModelAdmin):
    list_display = ("school", "last_seq", "updated_at")
    search_fields = ("school__code", "school__name")
    list_select_related = ("school",)
    readonly_fields = ("last_seq",)
