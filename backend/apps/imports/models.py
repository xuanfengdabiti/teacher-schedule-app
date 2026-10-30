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
    """Import batch mode.

    ``SNAPSHOT`` is persisted for the paused B12d work but is not offered in
    Admin and is rejected at preview time: the scoped-missing handling is not
    implemented yet, so a snapshot batch must never silently run as a merge.
    """

    MERGE = "merge", _("增量合并")
    SNAPSHOT = "snapshot", _("限定范围快照（未实现）")


class ImportStatus(models.TextChoices):
    UPLOADED = "uploaded", _("已上传，待预览")
    PREVIEWED = "previewed", _("已预览")
    COMMITTED = "committed", _("已提交")
    FAILED = "failed", _("失败")


class ImportDecisionKind(models.TextChoices):
    TOUCHED = "touched_change", _("有教学记录或已调整")
    CONFLICT = "conflict", _("目标时段有冲突")
    COMPLETED = "completed_locked", _("已完成课次锁定")


class ImportDecisionChoice(models.TextChoices):
    KEEP_EXISTING = "keep_existing", _("保留现状")
    APPLY_IMPORT = "apply_import", _("采用导入结果")


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
    scope_json = models.JSONField(_("快照范围"), default=dict, blank=True)
    enrollment_end_date = models.DateField(
        _("快照退课生效日期"),
        null=True,
        blank=True,
        help_text="snapshot 模式下，缺失选课的生效结束日期（不含当天）。不能按上传日猜测。",
    )
    missing_confirmed_at = models.DateTimeField(_("缺失清单确认时间"), null=True, blank=True)
    missing_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_import_missing",
        verbose_name=_("缺失清单确认人"),
        null=True,
        blank=True,
    )
    missing_signature = models.CharField(_("缺失清单指纹"), max_length=64, blank=True, default="")
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
            models.CheckConstraint(
                condition=models.Q(mode__in=[ImportMode.MERGE, ImportMode.SNAPSHOT]),
                name="imp_batch_mode_valid",
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


class ImportDecision(TimeStampedUUIDModel):
    """One session the preview could not change without academic approval.

    The row is preview data: a re-preview rebuilds the set, preserving a choice
    only while the proposed/current states remain identical.  It never writes
    ``ClassSession`` itself; commit applies an ``apply_import`` choice through
    the scheduling change service.
    """

    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name="decisions",
        verbose_name=_("导入批次"),
    )
    session = models.ForeignKey(
        "scheduling.ClassSession",
        on_delete=models.PROTECT,
        related_name="import_decisions",
        verbose_name=_("课次"),
    )
    kind = models.CharField(_("决定类型"), max_length=24, choices=ImportDecisionKind.choices)
    proposed_json = models.JSONField(_("导入希望的结果"), default=dict)
    current_json = models.JSONField(_("现状"), default=dict)
    reason = models.TextField(_("原因"))
    options = models.JSONField(_("可选动作"), default=list)
    choice = models.CharField(
        _("教务选择"),
        max_length=24,
        choices=ImportDecisionChoice.choices,
        blank=True,
        default="",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="import_decisions",
        verbose_name=_("决定人"),
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(_("决定时间"), null=True, blank=True)
    note = models.TextField(_("依据说明"), blank=True, default="")

    class Meta:
        verbose_name = _("导入人工决定项")
        verbose_name_plural = _("导入人工决定项")
        ordering = ("batch", "created_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["batch", "session"], name="imp_decision_batch_session_uniq"),
        ]
        indexes = [
            models.Index(fields=["batch", "choice"], name="imp_decision_batch_choice_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.batch_id} / {self.session_id} ({self.kind})"

    @property
    def is_decided(self) -> bool:
        return bool(self.choice)

    @property
    def proposed_display(self) -> str:
        return format_import_state(self.proposed_json)

    @property
    def current_display(self) -> str:
        return format_import_state(self.current_json)


def format_import_state(state: dict) -> str:
    """Readable one-line state for the Admin decision list."""
    if not state:
        return "—"
    parts = [
        f"{state.get('date', '—')} 第 {state.get('start_period', '—')}–{state.get('end_period', '—')} 节",
        f"{state.get('start_time', '—')}–{state.get('end_time', '—')}",
        f"教室 {state.get('classroom_code', '—')}",
        f"教师 {'|'.join(state.get('teacher_codes') or []) or '—'}",
        f"{state.get('status_label', state.get('status', '—'))}",
        f"计划 {state.get('planned_hours', '—')} 学时",
    ]
    if state.get("actual_hours") is not None:
        parts.append(f"实际 {state['actual_hours']} 学时")
    return "；".join(parts)
