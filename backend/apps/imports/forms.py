"""Admin upload form for a batch of fixed-template files."""

from __future__ import annotations

from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError

from apps.academics.models import School

ALLOWED_SUFFIXES = {".csv", ".xlsx"}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return [single_file_clean(data, initial)]


class ImportBatchUploadForm(forms.Form):
    school = forms.ModelChoiceField(label="学校", queryset=School.objects.order_by("code"))
    source_system = forms.CharField(
        label="来源系统",
        max_length=64,
        help_text="稳定来源标识，例如 demo_csv_v1；后续不能随意改名。",
    )
    source_updated_at = forms.DateTimeField(
        label="来源更新时间",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="学校导出数据的时间，必须带明确时间点。",
    )
    files = MultipleFileField(
        label="CSV / XLSX 文件",
        widget=MultipleFileInput(attrs={"accept": ".csv,.xlsx"}),
        help_text="可多选。CSV 使用固定文件名；XLSX 使用同名工作表。",
    )

    def clean_files(self):
        files = self.cleaned_data.get("files") or []
        if not files:
            raise ValidationError("请至少选择一个 CSV 或 XLSX 文件。")
        names: set[str] = set()
        for uploaded in files:
            name = Path(uploaded.name or "").name
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise ValidationError(f"{name} 不是受支持的 CSV/XLSX 文件。")
            if name in names:
                raise ValidationError(f"同一批次不能重复上传同名文件：{name}。")
            names.add(name)
        return files
