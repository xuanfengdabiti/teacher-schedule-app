"""Django Admin entry points for upload -> preview -> commit.

No model field on ``ImportBatch`` is editable in Admin.  The upload view creates
the batch and private files, then immediately parses and previews.  Commit and
re-preview are explicit POST actions.  Both call the same services as tests or
a future API would.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.imports.forms import ImportBatchUploadForm
from apps.imports.models import ImportBatch, ImportIdentity, ImportStatus
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
    list_filter = ("status", "mode", "school", "source_system")
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
        "row_counts",
        "errors_json",
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
        ]
        return custom + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["can_commit"] = self._can_commit(request, object_id)
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def _can_commit(self, request, object_id) -> bool:
        batch = ImportBatch.objects.filter(pk=object_id).first()
        return bool(batch and batch.status == ImportStatus.PREVIEWED and not batch.errors_json)

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
                        f"预览完成，共 {len(result.issues)} 条错误；修复文件后重新上传。",
                        level=messages.WARNING,
                    )
                else:
                    self.message_user(
                        request,
                        f"预览完成：预计新建 {result.expected_new_sessions} 个规则课次，未发现错误。",
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
                    (f"预览完成：{len(result.issues)} 条错误，预计新建 {result.expected_new_sessions} 个规则课次。"),
                    level=level,
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
                        f"导入已提交：新建 {result.created} 个对象，生成 {result.sessions_generated} 个课次，"
                        f"匹配 {result.sessions_matched} 条 sessions 行，更新完成量 "
                        f"{result.sessions_changed} 条。"
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
