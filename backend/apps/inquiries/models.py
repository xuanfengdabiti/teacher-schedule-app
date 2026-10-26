"""Lost-item inquiry requests, recipients and replies (B33).

An inquiry is a set of explicit recipient snapshots, not a broadcast and not a
chat room.  ``InquiryRecipient.related_session_ids`` records why each teacher
was relevant at creation time; later formal moves never silently replace it.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.academics.models import TeacherProfile
from apps.classrooms.models import Classroom
from apps.common.models import TimeStampedUUIDModel


class InquiryStatus(models.TextChoices):
    OPEN = "open", _("待回复")
    REPLIED = "replied", _("已回复")
    CLOSED = "closed", _("已结案")


class InquiryReplyResult(models.TextChoices):
    NOT_SEEN = "not_seen", _("未见到")
    IN_CUSTODY = "in_custody", _("已保管")
    OTHER = "other", _("其他说明")


class Inquiry(TimeStampedUUIDModel):
    initiator = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="initiated_inquiries",
        verbose_name=_("发起人"),
    )
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.PROTECT,
        related_name="inquiries",
        verbose_name=_("教室"),
    )
    from_at = models.DateTimeField(_("开始时间"))
    to_at = models.DateTimeField(_("结束时间"))
    item_description = models.TextField(_("物品描述"), max_length=1000)
    status = models.CharField(
        _("状态"),
        max_length=16,
        choices=InquiryStatus.choices,
        default=InquiryStatus.OPEN,
    )
    closed_at = models.DateTimeField(_("结案时间"), null=True, blank=True)
    close_note = models.TextField(_("结案说明"), max_length=1000, blank=True, default="")

    class Meta:
        verbose_name = _("失物协查")
        verbose_name_plural = _("失物协查")
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(condition=Q(to_at__gt=F("from_at")), name="inq_range_chk"),
        ]
        indexes = [
            models.Index(
                fields=["initiator", "status", "created_at"],
                name="inq_init_status_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.classroom_id} {self.from_at:%Y-%m-%d %H:%M} {self.status}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.from_at and self.to_at and self.to_at <= self.from_at:
            errors["to_at"] = "协查结束时间必须晚于开始时间。"
        if self.item_description is not None and not self.item_description.strip():
            errors["item_description"] = "物品描述不能为空。"
        if errors:
            raise ValidationError(errors)


class InquiryRecipient(TimeStampedUUIDModel):
    inquiry = models.ForeignKey(
        Inquiry,
        on_delete=models.CASCADE,
        related_name="recipients",
        verbose_name=_("协查"),
    )
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="inquiry_recipient_rows",
        verbose_name=_("接收教师"),
    )
    # Immutable-at-creation snapshot of the sessions that made this teacher a
    # candidate.  A later move changes the session, not this list.
    related_session_ids = models.JSONField(_("相关课次快照"), default=list, blank=True)
    first_viewed_at = models.DateTimeField(_("首次查看时间"), null=True, blank=True)

    class Meta:
        verbose_name = _("协查接收人")
        verbose_name_plural = _("协查接收人")
        ordering = ("inquiry", "teacher")
        constraints = [
            models.UniqueConstraint(fields=["inquiry", "teacher"], name="inq_recipient_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.inquiry_id} / {self.teacher_id}"


class InquiryReply(TimeStampedUUIDModel):
    inquiry = models.ForeignKey(
        Inquiry,
        on_delete=models.CASCADE,
        related_name="replies",
        verbose_name=_("协查"),
    )
    author = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="inquiry_replies",
        verbose_name=_("回复教师"),
    )
    result = models.CharField(_("回复结果"), max_length=16, choices=InquiryReplyResult.choices)
    body = models.TextField(_("文字说明"), max_length=1000, blank=True, default="")

    class Meta:
        verbose_name = _("协查回复")
        verbose_name_plural = _("协查回复")
        ordering = ("inquiry", "created_at", "id")
        indexes = [
            models.Index(fields=["inquiry", "created_at"], name="inq_reply_inquiry_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.inquiry_id} / {self.author_id} / {self.result}"
