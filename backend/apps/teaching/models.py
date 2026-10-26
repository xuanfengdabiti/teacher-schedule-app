"""Private teaching-note drafts, published snapshots, handoffs and deduplication."""

from __future__ import annotations

from django.conf import settings
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
