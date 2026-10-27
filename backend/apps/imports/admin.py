"""Django Admin entry points for upload -> preview -> commit.

No model field on ``ImportBatch`` is editable in Admin.  The upload view creates
the batch and private files, then immediately parses and previews.  Commit and
re-preview are explicit POST actions.  Both call the same services as tests or
a future API would.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from apps.imports.forms import ImportBatchUploadForm
from apps.imports.models import (
    ImportBatch,
    ImportDecision,
    ImportDecisionChoice,
    ImportDecisionKind,
    ImportIdentity,
    ImportStatus,
)
from apps.imports.services import ImportCommitError, commit_batch, preview_batch
from apps.imports.storage import save_uploaded_files


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "school",
        "source_system",
        "term",
        "status",
        "file_names",
        "source_updated_at",
        "committed_at",
        "created_at",
    )
    list_filter = ("status", "school", "source_system")
    search_fields = ("id", "school__code", "source_system", "term__code")
    list_select_related = ("school", "term", "uploaded_by", "committed_by")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    change_form_template = "admin/imports/importbatch/change_form.html"
    add_form_template = "admin/imports/importbatch/add_form.html"
    fields = (
        "id",
        "school",
        "source_system",
        "term",
        "mode",
        "status",
        "file_names",
        "file_hash",
        "source_updated_at",
        "preview_base_schedule_version",
        "preview_fingerprint",
        "row_counts",
        "errors_json",
        "change_summary",
        "uploaded_by",
        "committed_by",
        "committed_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return True

    def has_delete_permission(self, request, obj=None) -> bool:
        # The batch and its identity mapping are audit data.  A failed upload
        # is retained and a corrected import becomes a new batch.
        return False

    def get_urls(self):
        custom = [
            path(
                "<path:object_id>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="imports_importbatch_preview",
            ),
            path(
                "<path:object_id>/commit/",
                self.admin_site.admin_view(self.commit_view),
                name="imports_importbatch_commit",
            ),
            path(
                "<path:object_id>/decisions/",
                self.admin_site.admin_view(self.decisions_view),
                name="imports_importbatch_decisions",
            ),
        ]
        return custom + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        batch = ImportBatch.objects.filter(pk=object_id).first()
        extra_context["can_commit"] = self._can_commit(request, object_id)
        extra_context["undecided_count"] = batch.decisions.filter(choice="").count() if batch is not None else 0
        extra_context["decision_items"] = (
            list(batch.decisions.select_related("session", "decided_by").order_by("created_at", "id"))
            if batch is not None
            else []
        )
        extra_context["decision_choices"] = ImportDecisionChoice.choices
        extra_context["preview_errors"] = [
            item for item in (batch.errors_json if batch else []) if item.get("level", "error") == "error"
        ]
        extra_context["preview_warnings"] = [
            item for item in (batch.errors_json if batch else []) if item.get("level") == "warning"
        ]
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def _can_commit(self, request, object_id) -> bool:
        batch = ImportBatch.objects.filter(pk=object_id).first()
        if batch is None or batch.status != ImportStatus.PREVIEWED or batch.has_errors:
            return False
        if batch.decisions.filter(choice="").exists():
            return False
        return not batch.decisions.filter(choice=ImportDecisionChoice.APPLY_IMPORT, note="").exists()

    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = ImportBatchUploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            batch = ImportBatch.objects.create(
                school=form.cleaned_data["school"],
                source_system=form.cleaned_data["source_system"],
                source_updated_at=form.cleaned_data["source_updated_at"],
                uploaded_by=request.user,
                mode="merge",
            )
            try:
                names, file_hash = save_uploaded_files(batch, form.cleaned_data["files"])
                batch.file_names = names
                batch.file_hash = file_hash
                batch.save(update_fields=["file_names", "file_hash", "updated_at"])
                result = preview_batch(batch)
            except Exception as exc:  # file/storage or unexpected preview failure
                batch.status = ImportStatus.FAILED
                batch.errors_json = [
                    {
                        "file": "",
                        "sheet": "",
                        "row": 0,
                        "field": "",
                        "value": type(exc).__name__,
                        "code": "UPLOAD_FAILED",
                        "message": f"上传或预览失败：{str(exc)[:500]}",
                    }
                ]
                batch.save(update_fields=["status", "errors_json", "updated_at"])
                self.message_user(request, f"导入批次已保存但预览失败：{exc}", level=messages.ERROR)
            else:
                if result.has_errors:
                    self.message_user(
                        request,
                        f"预览完成，共 {len(result.errors)} 条错误、{len(result.warnings)} 条警告；"
                        "修复错误后重新上传。",
                        level=messages.WARNING,
                    )
                else:
                    self.message_user(
                        request,
                        f"预览完成：预计新建 {result.expected_new_sessions} 个规则课次，"
                        f"{len(result.warnings)} 条警告，{batch.decisions.count()} 条人工决定项，"
                        "未发现错误。",
                        level=messages.SUCCESS,
                    )
            return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[batch.pk]))

        context = {
            **self.admin_site.each_context(request),
            "title": "上传导入批次",
            "opts": self.model._meta,
            "form": form,
            "media": self.media + form.media,
            "has_view_permission": self.has_view_permission(request),
            "site_url": reverse("admin:index"),
        }
        return TemplateResponse(request, self.add_form_template, context)

    def preview_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[object_id]))
        batch = self.get_object(request, object_id)
        if batch is None:
            self.message_user(request, "导入批次不存在。", level=messages.ERROR)
        elif batch.status == ImportStatus.COMMITTED:
            self.message_user(request, "已提交的批次不能重新预览。", level=messages.ERROR)
        else:
            try:
                result = preview_batch(batch)
            except Exception as exc:
                self.message_user(request, f"重新预览失败：{exc}", level=messages.ERROR)
            else:
                level = messages.WARNING if result.has_errors else messages.SUCCESS
                self.message_user(
                    request,
                    (
                        f"预览完成：{len(result.errors)} 条错误、{len(result.warnings)} 条警告，"
                        f"预计新建 {result.expected_new_sessions} 个规则课次。"
                    ),
                    level=level,
                )
        return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[object_id]))

    def decisions_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[object_id]))
        batch = self.get_object(request, object_id)
        if batch is None:
            self.message_user(request, "导入批次不存在。", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:imports_importbatch_changelist"))

        keep_all = request.POST.get("action") == "keep_all"
        try:
            with transaction.atomic():
                locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
                if locked.status != ImportStatus.PREVIEWED:
                    raise ValueError("只有已预览且未提交的批次可以处理决定项。")
                decisions = list(
                    ImportDecision.objects.select_for_update().filter(batch=locked).order_by("created_at", "id")
                )
                if not decisions:
                    raise ValueError("该批次没有人工决定项。")
                now = timezone.now()
                values = []
                for decision in decisions:
                    if keep_all:
                        choice = ImportDecisionChoice.KEEP_EXISTING
                    else:
                        choice = request.POST.get(f"choice_{decision.pk}", "").strip()
                        if not choice:
                            raise ValueError("还有未选择的决定项；请逐条选择或点击“全部保留现状”。")
                    options = set(decision.options or ())
                    if choice not in options:
                        raise ValueError(f"课次 {decision.session_id} 不支持所选动作。")
                    note = (request.POST.get(f"note_{decision.pk}", "") or "").strip()
                    if not note:
                        note = decision.note
                    if choice == ImportDecisionChoice.APPLY_IMPORT:
                        if decision.kind == ImportDecisionKind.COMPLETED:
                            raise ValueError("已完成课次只能保留现状。")
                        if not note:
                            raise ValueError(f"课次 {decision.session_id} 采用导入结果时必须填写依据说明。")
                    values.append((decision, choice, note, now))
                for decision, choice, note, decided_at in values:
                    decision.choice = choice
                    decision.note = note
                    decision.decided_by = request.user
                    decision.decided_at = decided_at
                    decision.save(update_fields=["choice", "note", "decided_by", "decided_at", "updated_at"])
        except ValueError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                "人工决定项已保存；仍可在提交前重新预览或调整。",
                level=messages.SUCCESS,
            )
        return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[object_id]))

    def commit_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[object_id]))
        batch = self.get_object(request, object_id)
        if batch is None:
            self.message_user(request, "导入批次不存在。", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:imports_importbatch_changelist"))
        try:
            result = commit_batch(batch, request.user)
        except ImportCommitError as exc:
            self.message_user(request, f"提交被拒绝：{exc.detail}（{exc.code}）", level=messages.ERROR)
        else:
            if result.committed:
                self.message_user(
                    request,
                    (
                        f"导入已提交：基础对象新建 {result.created} 个、更新 {result.changed} 个；"
                        f"课次新增 {result.sessions_new}、更新 {result.sessions_changed}、"
                        f"取消 {result.sessions_cancelled}、恢复 {result.sessions_restored}；"
                        f"人工保留 {result.decisions_kept}、人工采用 {result.decisions_applied}。"
                    ),
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(request, f"{result.message}（{result.code}）", level=messages.WARNING)
        return HttpResponseRedirect(reverse("admin:imports_importbatch_change", args=[object_id]))


@admin.register(ImportIdentity)
class ImportIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "entity_type",
        "external_id",
        "object_id",
        "source_system",
        "school",
        "batch",
        "created_at",
    )
    list_filter = ("entity_type", "source_system", "school")
    search_fields = ("external_id", "object_id", "batch__id")
    list_select_related = ("school", "batch")
    ordering = ("school", "source_system", "entity_type", "external_id")
    readonly_fields = (
        "id",
        "school",
        "source_system",
        "entity_type",
        "external_id",
        "object_id",
        "batch",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_view_permission(self, request, obj=None) -> bool:
        return True


@admin.register(ImportDecision)
class ImportDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "batch",
        "session",
        "kind",
        "choice",
        "decided_by",
        "decided_at",
        "created_at",
    )
    list_filter = ("kind", "choice")
    search_fields = ("batch__id", "session__id")
    list_select_related = ("batch", "session", "decided_by")
    readonly_fields = (
        "id",
        "batch",
        "session",
        "kind",
        "proposed_json",
        "current_json",
        "reason",
        "options",
        "choice",
        "decided_by",
        "decided_at",
        "note",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_view_permission(self, request, obj=None) -> bool:
        return True
