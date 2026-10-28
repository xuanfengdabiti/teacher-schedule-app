"""Private teaching-note drafts, published snapshots, handoffs and deduplication."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.academics.models import CourseOffering, TeacherProfile
from apps.common.models import TimeStampedUUIDModel
from apps.scheduling.models import ClassSession


class TeachingNote(TimeStampedUUIDModel):
    """One private draft and one published snapshot per session teacher.

    A draft save never changes the published snapshot (A25).  The author is the
    only reader of draft columns; sharing is based on the published snapshot
    and the session's offering, never on the broader teaching-class scope.
    """

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="teaching_notes",
        verbose_name=_("课次"),
    )
    author = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="teaching_notes",
        verbose_name=_("作者"),
    )
    draft_covered = models.TextField(_("已讲内容草稿"), max_length=5000, null=True, blank=True)
    draft_unfinished = models.TextField(_("未完成内容草稿"), max_length=5000, null=True, blank=True)
    draft_next_plan = models.TextField(_("下次计划草稿"), max_length=5000, null=True, blank=True)
    published_covered = models.TextField(_("已发布已讲内容"), max_length=5000, null=True, blank=True)
    published_unfinished = models.TextField(_("已发布未完成内容"), max_length=5000, null=True, blank=True)
    published_next_plan = models.TextField(_("已发布下次计划"), max_length=5000, null=True, blank=True)
    published_at = models.DateTimeField(_("发布时间"), null=True, blank=True)
    published_version = models.PositiveIntegerField(_("发布版本"), null=True, blank=True)
    version = models.PositiveIntegerField(_("版本"), default=1)

    class Meta:
        verbose_name = _("教学备注")
        verbose_name_plural = _("教学备注")
        ordering = ("session", "author")
        constraints = [
            models.UniqueConstraint(fields=["session", "author"], name="teach_note_session_author_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="teach_note_version_positive"),
            models.CheckConstraint(
                condition=(
                    models.Q(published_at__isnull=True, published_version__isnull=True)
                    | models.Q(published_at__isnull=False, published_version__isnull=False)
                ),
                name="teach_note_published_pair_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["author", "session"], name="teach_note_author_session_idx"),
            models.Index(fields=["session", "published_at"], name="teach_note_session_pub_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.author_id} v{self.version}"


class LessonPreparation(TimeStampedUUIDModel):
    """One editable AI-assisted preparation draft per session teacher.

    The row is private to its author, independent of ``TeachingNote``: saving a
    preparation never publishes, never changes hours and never appears on the
    timetable cards.  ``generated_model`` stays internal for support; teachers
    only see the four editable sections.
    """

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="lesson_preparations",
        verbose_name=_("课次"),
    )
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="lesson_preparations",
        verbose_name=_("教师"),
    )
    topic = models.CharField(_("本节主题"), max_length=200)
    outline = models.TextField(_("授课提纲"), blank=True, default="")
    case_study = models.TextField(_("课堂案例"), blank=True, default="")
    discussion_questions = models.TextField(_("讨论问题"), blank=True, default="")
    preparation = models.TextField(_("课前准备"), blank=True, default="")
    generated_model = models.CharField(_("生成模型"), max_length=100, blank=True, default="")
    generated_at = models.DateTimeField(_("生成时间"), null=True, blank=True)
    version = models.PositiveIntegerField(_("版本"), default=1)

    class Meta:
        verbose_name = _("备课草稿")
        verbose_name_plural = _("备课草稿")
        ordering = ("session", "teacher")
        constraints = [
            models.UniqueConstraint(
                fields=["session", "teacher"],
                name="teach_prep_session_teacher_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="teach_prep_version_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.teacher_id} v{self.version}"


class HandoffStatus(models.TextChoices):
    PENDING = "pending", _("待确认")
    ACKNOWLEDGED = "acknowledged", _("已确认")
    WITHDRAWN = "withdrawn", _("已撤回")


class Handoff(TimeStampedUUIDModel):
    """One explicit teaching handoff from one sender to one recipient.

    The content is a snapshot: editing or republishing the linked note later
    never changes an existing handoff (A14).  A multi-recipient send creates one
    row per recipient so each one has its own read/acknowledge/withdraw state.
    ``source_session`` is where the sender was when writing; ``target_session``
    is the class being handed over.  Both may be empty, but must belong to the
    offering when present.
    """

    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="handoffs",
        verbose_name=_("课程开设"),
    )
    sender = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="sent_handoffs",
        verbose_name=_("发送教师"),
    )
    recipient = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="received_handoffs",
        verbose_name=_("接收教师"),
    )
    source_session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="source_handoffs",
        verbose_name=_("来源课次"),
        null=True,
        blank=True,
    )
    target_session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="target_handoffs",
        verbose_name=_("目标课次"),
        null=True,
        blank=True,
    )
    note = models.ForeignKey(
        TeachingNote,
        on_delete=models.PROTECT,
        related_name="handoffs",
        verbose_name=_("关联备注"),
        null=True,
        blank=True,
    )
    note_published_version = models.PositiveIntegerField(_("备注发布版本"), null=True, blank=True)
    content_snapshot = models.TextField(_("交接内容"), max_length=5000)
    status = models.CharField(
        _("状态"),
        max_length=16,
        choices=HandoffStatus.choices,
        default=HandoffStatus.PENDING,
    )
    read_at = models.DateTimeField(_("已读时间"), null=True, blank=True)
    acknowledged_at = models.DateTimeField(_("确认时间"), null=True, blank=True)
    withdrawn_at = models.DateTimeField(_("撤回时间"), null=True, blank=True)

    class Meta:
        verbose_name = _("教学交接")
        verbose_name_plural = _("教学交接")
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(sender=models.F("recipient")),
                name="teach_handoff_sender_recipient_chk",
            ),
            models.CheckConstraint(
                condition=(~models.Q(status=HandoffStatus.ACKNOWLEDGED) | models.Q(acknowledged_at__isnull=False)),
                name="teach_handoff_ack_time_chk",
            ),
            models.CheckConstraint(
                condition=(~models.Q(status=HandoffStatus.WITHDRAWN) | models.Q(withdrawn_at__isnull=False)),
                name="teach_handoff_withdraw_time_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(note__isnull=True, note_published_version__isnull=True)
                    | models.Q(note__isnull=False, note_published_version__isnull=False)
                ),
                name="teach_handoff_note_pair_chk",
            ),
        ]
        indexes = [
            models.Index(
                fields=["recipient", "status", "created_at"],
                name="teach_handoff_inbox_idx",
            ),
            models.Index(
                fields=["sender", "created_at"],
                name="teach_handoff_sent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sender_id} -> {self.recipient_id} ({self.status})"


class TeachingPlanStatus(models.TextChoices):
    ACTIVE = "active", _("启用")


class TeachingPlan(TimeStampedUUIDModel):
    """One plan per offering; only its lead teacher may edit it.

    The current row is the editable plan.  Every save also appends a
    ``PlanRevision`` snapshot so changing a due date or removing an item cannot
    silently rewrite the baseline a reminder was based on (B23).
    """

    offering = models.OneToOneField(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="teaching_plan",
        verbose_name=_("课程开设"),
    )
    owner = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="owned_teaching_plans",
        verbose_name=_("负责人"),
    )
    version = models.PositiveIntegerField(_("版本"), default=1)
    status = models.CharField(
        _("计划状态"),
        max_length=16,
        choices=TeachingPlanStatus.choices,
        default=TeachingPlanStatus.ACTIVE,
    )

    class Meta:
        verbose_name = _("教学计划")
        verbose_name_plural = _("教学计划")
        ordering = ("offering",)
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gte=1), name="teach_plan_version_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.offering_id} v{self.version}"


class PlanItem(TimeStampedUUIDModel):
    """A chapter/topic in a plan; positions are contiguous within the plan."""

    plan = models.ForeignKey(
        TeachingPlan,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name=_("教学计划"),
    )
    position = models.PositiveIntegerField(_("排序"), validators=[MinValueValidator(1)])
    title = models.CharField(_("专题标题"), max_length=200)
    planned_hours = models.DecimalField(
        _("计划学时"),
        max_digits=6,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0"))],
    )
    due_date = models.DateField(_("计划完成日期"), null=True, blank=True)
    due_session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="plan_items_due_here",
        verbose_name=_("计划完成课次"),
        null=True,
        blank=True,
    )
    archived = models.BooleanField(_("已归档"), default=False)

    class Meta:
        verbose_name = _("计划专题")
        verbose_name_plural = _("计划专题")
        ordering = ("plan", "position")
        constraints = [
            models.UniqueConstraint(fields=["plan", "position"], name="teach_planitem_position_uniq"),
            models.CheckConstraint(condition=models.Q(position__gte=1), name="teach_planitem_position_positive"),
            models.CheckConstraint(condition=models.Q(planned_hours__gte=0), name="teach_planitem_hours_nonneg"),
            models.CheckConstraint(
                condition=~(models.Q(due_date__isnull=False) & models.Q(due_session__isnull=False)),
                name="teach_planitem_one_due",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plan_id} #{self.position} {self.title}"


class PlanRevision(TimeStampedUUIDModel):
    """Immutable full snapshot appended on every successful plan save."""

    plan = models.ForeignKey(
        TeachingPlan,
        on_delete=models.PROTECT,
        related_name="revisions",
        verbose_name=_("教学计划"),
    )
    version = models.PositiveIntegerField(_("版本"))
    items_json = models.JSONField(_("专题快照"), default=list, blank=True)
    saved_by = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="plan_revisions",
        verbose_name=_("保存教师"),
    )
    saved_at = models.DateTimeField(_("保存时间"))

    class Meta:
        verbose_name = _("计划修订")
        verbose_name_plural = _("计划修订")
        ordering = ("plan", "-version")
        constraints = [
            models.UniqueConstraint(fields=["plan", "version"], name="teach_planrevision_version_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="teach_planrevision_version_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.plan_id} v{self.version}"


class SessionProgress(TimeStampedUUIDModel):
    """Hours from one completed session allocated to one plan item.

    The completion service replaces all rows for a session in one transaction
    and enforces that their sum never exceeds the session's actual hours.
    """

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="topic_progress",
        verbose_name=_("课次"),
    )
    plan_item = models.ForeignKey(
        PlanItem,
        on_delete=models.PROTECT,
        related_name="session_progresses",
        verbose_name=_("计划专题"),
    )
    actual_hours = models.DecimalField(
        _("本次分配学时"),
        max_digits=6,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = _("课次专题学时")
        verbose_name_plural = _("课次专题学时")
        ordering = ("session", "plan_item")
        constraints = [
            models.UniqueConstraint(fields=["session", "plan_item"], name="teach_progress_session_item_uniq"),
            models.CheckConstraint(condition=models.Q(actual_hours__gte=0), name="teach_progress_hours_nonneg"),
        ]
        indexes = [
            models.Index(fields=["plan_item"], name="teach_progress_planitem_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.plan_item_id} = {self.actual_hours}"


class CompletionAction(models.TextChoices):
    COMPLETE = "complete", _("完成")
    CORRECT = "correct", _("更正")
    REVERT = "revert", _("撤销完成")


class CompletionRecord(TimeStampedUUIDModel):
    """Append-only audit row for every completion, correction and reversion."""

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="completion_records",
        verbose_name=_("课次"),
    )
    actor = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="completion_records",
        verbose_name=_("操作教师"),
    )
    action = models.CharField(_("操作"), max_length=16, choices=CompletionAction.choices)
    actual_hours_before = models.DecimalField(
        _("操作前实际学时"),
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
    )
    actual_hours_after = models.DecimalField(
        _("操作后实际学时"),
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
    )
    allocations_json = models.JSONField(_("专题分配快照"), default=list, blank=True)
    reason = models.TextField(_("更正或撤销原因"), blank=True, default="")

    class Meta:
        verbose_name = _("授课完成记录")
        verbose_name_plural = _("授课完成记录")
        ordering = ("session", "-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(action__in=[CompletionAction.CORRECT, CompletionAction.REVERT]) | ~models.Q(reason="")
                ),
                name="teach_completion_reason_required",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "created_at"], name="teach_completion_session_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} {self.action}"


class ClientMutationOperation(models.TextChoices):
    SAVE_DRAFT = "save_draft", _("保存草稿")
    PUBLISH = "publish", _("发布")


class ClientMutation(TimeStampedUUIDModel):
    """Idempotency ledger for one account and client-generated mutation key.

    It lives in the teaching app for the B20 slice.  The fields are generic
    enough to serve later offline writes, but moving it to ``common`` without a
    second caller would add indirection before a shared use exists.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="client_mutations",
        verbose_name=_("账号"),
    )
    client_mutation_id = models.CharField(_("客户端变更 ID"), max_length=64)
    operation = models.CharField(_("操作"), max_length=32, choices=ClientMutationOperation.choices)
    request_hash = models.CharField(_("请求摘要"), max_length=64)
    result_object_id = models.UUIDField(_("结果对象 ID"), null=True, blank=True)
    result_version = models.PositiveIntegerField(_("结果版本"), null=True, blank=True)
    response_json = models.JSONField(_("保存的响应"), default=dict, blank=True)
    completed_at = models.DateTimeField(_("完成时间"))

    class Meta:
        verbose_name = _("客户端变更去重")
        verbose_name_plural = _("客户端变更去重")
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "client_mutation_id"],
                name="teach_mutation_user_key_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} / {self.client_mutation_id} / {self.operation}"
