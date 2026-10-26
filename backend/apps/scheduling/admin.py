"""Admin registration for scheduling data.

``ClassSession`` is view-only in Admin: formal schedule changes go through B31
and completion corrections (with a reason) through B22.  B11 only exposes the
rule-expansion entry point on ``Term``.
"""

from __future__ import annotations

import json

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.academics.selectors import assigned_teacher_ids
from apps.scheduling.changes import ScheduleChangeError
from apps.scheduling.models import (
    ClassSession,
    PersonBlock,
    RescheduleRequest,
    ScheduleRule,
    SchoolSyncState,
    SessionChange,
    SessionTeacher,
)
from apps.scheduling.requests import (
    RescheduleRequestError,
    approve_request,
    force_approve_request,
    reject_request,
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


APPROVE_ACTION = "approve_requests"
REJECT_ACTION = "reject_requests"
FORCE_APPROVE_ACTION = "force_approve_requests"


class RescheduleDecisionForm(forms.Form):
    """Fields collected by the Admin action confirmation page."""

    decision_ref = forms.CharField(
        label="学校审批编号/依据",
        required=False,
        max_length=200,
        help_text="普通批准可空；登记学校强制安排时必填。",
    )
    decision_note = forms.CharField(
        label="审批说明",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    reschedule_confirm = forms.CharField(widget=forms.HiddenInput(), initial="1")

    def __init__(self, *args, action: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if action == FORCE_APPROVE_ACTION:
            self.fields["decision_ref"].required = True
            self.fields["decision_ref"].help_text = "必填：学校审批编号或书面审批依据。"
        if action == REJECT_ACTION:
            self.fields["decision_note"].required = True
            self.fields["decision_note"].help_text = "必填：向申请人说明驳回原因。"


@admin.register(RescheduleRequest)
class RescheduleRequestAdmin(admin.ModelAdmin):
    """Inspect requests and register school decisions.

    Every field is read-only: the only writes available here are the three
    actions, and each calls the same service used by the API.  The action opens
    an intermediate page to collect the approval reference/note, so academic
    staff cannot bypass the validation by editing a model field.
    """

    list_display = (
        "id",
        "session",
        "applicant",
        "target_date",
        "target_start_period",
        "target_end_period",
        "status",
        "needs_verification",
        "forced_override",
        "created_at",
    )
    list_filter = ("status", "needs_verification", "forced_override")
    search_fields = (
        "id",
        "session__offering__offering_code",
        "session__offering__course__name",
        "applicant__teacher_no",
        "applicant__formal_name",
        "reason",
    )
    list_select_related = ("session__offering__course", "applicant", "target_classroom")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    actions = (APPROVE_ACTION, REJECT_ACTION, FORCE_APPROVE_ACTION)
    # Every source/target field is read-only.  The change page is for inspection
    # and links to the decision actions; no model field can be edited to bypass
    # a service.
    fields = (
        "id",
        "session_link",
        "applicant",
        "target_display",
        "reason",
        "status",
        "base_session_version",
        "checked_schedule_version",
        "check_summary_display",
        "needs_verification",
        "decision_by",
        "decided_at",
        "decision_ref",
        "decision_note",
        "forced_override",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "id",
        "session",
        "session_link",
        "applicant",
        "target_date",
        "target_start_period",
        "target_end_period",
        "target_start_at",
        "target_end_at",
        "target_classroom",
        "target_display",
        "reason",
        "status",
        "base_session_version",
        "checked_schedule_version",
        "check_summary",
        "check_summary_display",
        "needs_verification",
        "decision_by",
        "decided_at",
        "decision_ref",
        "decision_note",
        "forced_override",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "session__offering__course",
                "session__offering__term",
                "session__classroom",
                "applicant",
                "target_classroom",
                "decision_by",
            )
        )

    @admin.display(description="源课次")
    def session_link(self, obj: RescheduleRequest):
        url = reverse("admin:scheduling_classsession_change", args=[obj.session_id])
        return format_html(
            '<a href="{}">{} · {} 第 {}–{} 节 · {}</a>',
            url,
            obj.session.offering.course.name,
            timezone.localtime(obj.session.start_at, obj.session.offering.term.tzinfo).date(),
            obj.session.start_period,
            obj.session.end_period,
            obj.session.classroom.classroom_code,
        )

    @admin.display(description="目标安排")
    def target_display(self, obj: RescheduleRequest) -> str:
        tzinfo = obj.session.offering.term.tzinfo
        start = obj.target_start_at.astimezone(tzinfo)
        end = obj.target_end_at.astimezone(tzinfo)
        return (
            f"{obj.target_date} {start:%H:%M}–{end:%H:%M}"
            f"（第 {obj.target_start_period}–{obj.target_end_period} 节），"
            f"教室 {obj.target_classroom.classroom_code}"
        )

    @admin.display(description="检查摘要")
    def check_summary_display(self, obj: RescheduleRequest):
        return format_html(
            "<pre style='white-space: pre-wrap; max-width: 900px'>{}</pre>",
            json.dumps(obj.check_summary or {}, ensure_ascii=False, indent=2),
        )

    def _decision_response(self, request, queryset, *, action: str):
        form = RescheduleDecisionForm(request.POST if request.POST.get("reschedule_confirm") else None, action=action)
        if request.POST.get("reschedule_confirm") and form.is_valid():
            decision_ref = form.cleaned_data["decision_ref"]
            note = form.cleaned_data["decision_note"]
            now = timezone.now()
            selected = list(queryset.select_related("session__offering__term", "applicant", "target_classroom"))
            for request_obj in selected:
                try:
                    if action == APPROVE_ACTION:
                        approve_request(
                            request_obj,
                            request.user,
                            decision_ref=decision_ref,
                            note=note,
                            now=now,
                        )
                    elif action == REJECT_ACTION:
                        reject_request(request_obj, request.user, note=note, now=now)
                    else:
                        force_approve_request(
                            request_obj,
                            request.user,
                            decision_ref=decision_ref,
                            note=note,
                            now=now,
                        )
                except (ScheduleChangeError, RescheduleRequestError) as exc:
                    self.message_user(
                        request,
                        f"{request_obj.id} 未处理：{exc.detail}（{exc.code}）",
                        level=messages.ERROR,
                    )
                else:
                    self.message_user(
                        request,
                        f"{request_obj.id} 已处理。",
                        level=messages.SUCCESS,
                    )
            return HttpResponseRedirect(request.get_full_path())

        context = {
            **self.admin_site.each_context(request),
            "title": "登记调课审批结果",
            "opts": self.model._meta,
            "form": form,
            "action": action,
            "queryset": queryset,
        }
        return TemplateResponse(
            request,
            "admin/scheduling/reschedulerequest/decision_confirm.html",
            context,
        )

    @admin.action(description="批准并生效（普通，目标必须 available）")
    def approve_requests(self, request, queryset):
        return self._decision_response(request, queryset, action=APPROVE_ACTION)

    @admin.action(description="驳回（需填写审批说明）")
    def reject_requests(self, request, queryset):
        return self._decision_response(request, queryset, action=REJECT_ACTION)

    @admin.action(description="登记学校强制安排（需审批编号）")
    def force_approve_requests(self, request, queryset):
        return self._decision_response(request, queryset, action=FORCE_APPROVE_ACTION)


@admin.register(SessionChange)
class SessionChangeAdmin(admin.ModelAdmin):
    """Read-only audit list; changes are created only by the schedule service."""

    list_display = (
        "session",
        "session_version",
        "actor",
        "forced_override",
        "approval_ref",
        "recorded_at",
    )
    list_filter = ("forced_override", "recorded_at")
    search_fields = (
        "session__offering__offering_code",
        "session__offering__course__name",
        "actor__email",
        "approval_ref",
        "reason",
    )
    list_select_related = ("session__offering__course", "actor")
    date_hierarchy = "recorded_at"
    ordering = ("-recorded_at",)
    readonly_fields = (
        "id",
        "session",
        "request",
        "session_version",
        "before_json",
        "after_json",
        "actor",
        "reason",
        "approval_ref",
        "forced_override",
        "recorded_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
