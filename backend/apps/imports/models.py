"""Import batches and source identity mappings.

An import batch keeps the private source files, the preview counts/errors and
the commit audit. ``ImportIdentity`` is the durable link between an external
source key and one local object; repeated files must resolve through it instead
of matching names or dates.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import School, Term
from apps.common.models import TimeStampedUUIDModel


class ImportMode(models.TextChoices):
    MERGE = "merge", _("增量合并")


class ImportStatus(models.TextChoices):
    UPLOADED = "uploaded", _("已上传，待预览")
    PREVIEWED = "previewed", _("已预览")
    COMMITTED = "committed", _("已提交")
    FAILED = "failed", _("失败")


class ImportBatch(TimeStampedUUIDModel):
    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="import_batches",
        verbose_name=_("学校"),
    )
    source_system = models.CharField(_("来源系统"), max_length=64)
    term = models.ForeignKey(
        Term,
        on_delete=models.PROTECT,
        related_name="import_batches",
        verbose_name=_("学期"),
        null=True,
        blank=True,
    )
    mode = models.CharField(
        _("模式"),
        max_length=16,
        choices=ImportMode.choices,
        default=ImportMode.MERGE,
    )
    status = models.CharField(
        _("状态"),
        max_length=16,
        choices=ImportStatus.choices,
        default=ImportStatus.UPLOADED,
    )
    file_names = models.JSONField(_("原始文件名"), default=list, blank=True)
    file_hash = models.CharField(_("文件 SHA-256"), max_length=64, blank=True, default="")
    source_updated_at = models.DateTimeField(_("来源更新时间"))
    preview_base_schedule_version = models.PositiveIntegerField(
        _("预览时学期课表版本"),
        null=True,
        blank=True,
    )
    preview_fingerprint = models.CharField(_("预览状态指纹"), max_length=64, blank=True, default="")
    row_counts = models.JSONField(_("逐文件行统计"), default=dict, blank=True)
    errors_json = models.JSONField(_("预览问题"), default=list, blank=True)
    change_summary = models.JSONField(_("预览变更明细"), default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_import_batches",
        verbose_name=_("上传人"),
        null=True,
        blank=True,
    )
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="committed_import_batches",
        verbose_name=_("提交人"),
        null=True,
        blank=True,
    )
    committed_at = models.DateTimeField(_("提交时间"), null=True, blank=True)

    class Meta:
        verbose_name = _("导入批次")
        verbose_name_plural = _("导入批次")
        ordering = ("-created_at",)
        constraints = [
            # B12b still commits merge batches only. Snapshot scope and
            # missing-object decisions belong to B12c.
            models.CheckConstraint(
                condition=models.Q(mode=ImportMode.MERGE),
                name="imp_batch_mode_merge_only",
            ),
        ]
        indexes = [
            models.Index(fields=["term", "created_at"], name="imp_batch_term_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source_system} {self.created_at:%Y-%m-%d %H:%M} ({self.get_status_display()})"

    @property
    def has_errors(self) -> bool:
        return any(item.get("level", "error") == "error" for item in (self.errors_json or []))

    @property
    def warnings(self) -> list[dict]:
        return [item for item in (self.errors_json or []) if item.get("level") == "warning"]


class ImportIdentity(TimeStampedUUIDModel):
    """One source object mapped to one local UUID.

    ``object_id`` intentionally has no foreign key: the same table maps many
    entity types, and deleting one local object must not silently delete its
    source identity. Services resolve and validate the concrete object.
    """

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="import_identities",
        verbose_name=_("学校"),
    )
    source_system = models.CharField(_("来源系统"), max_length=64)
    entity_type = models.CharField(_("对象类型"), max_length=32)
    external_id = models.CharField(_("外部编号"), max_length=200)
    object_id = models.UUIDField(_("本地对象 ID"))
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        related_name="identities",
        verbose_name=_("首次建立的批次"),
    )

    class Meta:
        verbose_name = _("导入身份映射")
        verbose_name_plural = _("导入身份映射")
        ordering = ("school", "source_system", "entity_type", "external_id")
        constraints = [
            models.UniqueConstraint(
                fields=["school", "source_system", "entity_type", "external_id"],
                name="imp_identity_source_key_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["school", "source_system", "entity_type", "object_id"],
                name="imp_identity_object_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.external_id} -> {self.object_id}"
