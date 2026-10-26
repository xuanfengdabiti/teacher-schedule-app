"""Classroom catalogue and non-course room occupancy."""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.academics.models import School
from apps.common.models import TimeStampedUUIDModel


class Classroom(TimeStampedUUIDModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="classrooms", verbose_name=_("学校"))
    classroom_code = models.CharField(_("教室代码"), max_length=64)
    campus = models.CharField(_("校区"), max_length=100)
    building = models.CharField(_("楼宇"), max_length=100)
    room_no = models.CharField(_("房间号"), max_length=64)
    capacity = models.PositiveIntegerField(_("容量"), validators=[MinValueValidator(0)])
    equipment = ArrayField(
        models.CharField(max_length=64),
        default=list,
        blank=True,
        verbose_name=_("设备"),
    )
    is_active = models.BooleanField(_("可用"), default=True)

    class Meta:
        verbose_name = _("教室")
        verbose_name_plural = _("教室")
        ordering = ("school", "classroom_code")
        constraints = [
            models.UniqueConstraint(fields=["school", "classroom_code"], name="cr_classroom_school_code_uniq"),
            models.UniqueConstraint(
                fields=["school", "campus", "building", "room_no"],
                name="cr_classroom_room_uniq",
            ),
            models.CheckConstraint(condition=Q(capacity__gte=0), name="cr_classroom_capacity_nonneg"),
        ]

    def __str__(self) -> str:
        return f"{self.classroom_code} {self.campus}-{self.building}-{self.room_no}"

    def clean(self) -> None:
        super().clean()
        if self.equipment:
            self.equipment = sorted(set(self.equipment))

    def save(self, *args, **kwargs) -> None:
        if self.equipment:
            self.equipment = sorted(set(self.equipment))
        super().save(*args, **kwargs)


class RoomBlock(TimeStampedUUIDModel):
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.PROTECT,
        related_name="room_blocks",
        verbose_name=_("教室"),
    )
    start_at = models.DateTimeField(_("开始时间"))
    end_at = models.DateTimeField(_("结束时间"))
    reason = models.TextField(_("原因"))
    block_code = models.CharField(_("占用编号"), max_length=64)

    class Meta:
        verbose_name = _("教室占用")
        verbose_name_plural = _("教室占用")
        ordering = ("classroom", "start_at")
        constraints = [
            models.CheckConstraint(condition=Q(end_at__gt=F("start_at")), name="cr_roomblock_range_chk"),
        ]
        indexes = [
            models.Index(fields=["classroom", "start_at"], name="cr_roomblock_room_start_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.classroom_id} {self.start_at:%Y-%m-%d %H:%M}"

    def clean(self) -> None:
        super().clean()
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "占用结束时间必须晚于开始时间。"})
