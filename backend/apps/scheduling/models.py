"""Scheduling rules, generated class sessions and availability data."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.models import CourseOffering, Period, School, Student, TeacherProfile
from apps.academics.selectors import assigned_teacher_ids
from apps.classrooms.models import Classroom
from apps.common.models import TimeStampedUUIDModel


class SessionStatus(models.TextChoices):
    SCHEDULED = "scheduled", _("待上课")
    COMPLETED = "completed", _("已完成")
    CANCELLED = "cancelled", _("已取消")


class ScheduleRule(TimeStampedUUIDModel):
    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="schedule_rules",
        verbose_name=_("课程开设"),
    )
    rule_code = models.CharField(_("规则代码"), max_length=64)
    weekday = models.PositiveSmallIntegerField(
        _("星期"),
        validators=[MinValueValidator(1), MaxValueValidator(7)],
        help_text="1=周一 … 7=周日",
    )
    start_period = models.PositiveSmallIntegerField(_("起始节"), validators=[MinValueValidator(1)])
    end_period = models.PositiveSmallIntegerField(_("结束节"), validators=[MinValueValidator(1)])
    weeks = ArrayField(
        models.PositiveSmallIntegerField(),
        default=list,
        verbose_name=_("教学周集合"),
        help_text='例如 [1, 3, 5, 7]；"单双周"只是录入捷径，必须落成明确集合。',
    )
    teachers = models.ManyToManyField(
        TeacherProfile,
        related_name="schedule_rules",
        verbose_name=_("实际授课教师"),
    )
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.PROTECT,
        related_name="schedule_rules",
        verbose_name=_("排定教室"),
    )
    planned_hours = models.DecimalField(
        _("每次课计划学时"),
        max_digits=6,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = _("排课规则")
        verbose_name_plural = _("排课规则")
        ordering = ("offering", "rule_code")
        constraints = [
            models.CheckConstraint(condition=Q(weekday__gte=1) & Q(weekday__lte=7), name="sch_rule_weekday_chk"),
            models.CheckConstraint(
                condition=Q(start_period__gte=1) & Q(end_period__gte=F("start_period")),
                name="sch_rule_period_order_chk",
            ),
            models.CheckConstraint(condition=Q(planned_hours__gte=0), name="sch_rule_hours_nonneg"),
        ]

    def __str__(self) -> str:
        return f"{self.rule_code} {self.offering_id}"

    def save(self, *args, **kwargs) -> None:
        # The database stores an explicit, sorted week set; "odd/even weeks"
        # is only an input shortcut (domain-design 3.1 item 3).
        if self.weeks:
            self.weeks = sorted({int(week) for week in self.weeks})
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if not self.offering_id:
            return
        term = self.offering.term
        if self.start_period and self.end_period and self.start_period > self.end_period:
            errors["end_period"] = "结束节不能早于起始节。"
        if not self.weeks:
            errors["weeks"] = "至少选择一个教学周。"
        else:
            invalid = [week for week in self.weeks if not 1 <= week <= term.teaching_week_count]
            if invalid:
                errors["weeks"] = f"教学周必须落在 1…{term.teaching_week_count}，无效值：{sorted(set(invalid))}。"
        if self.start_period and self.end_period and self.start_period <= self.end_period:
            existing = set(Period.objects.filter(term=term).values_list("period_number", flat=True))
            missing = [number for number in range(self.start_period, self.end_period + 1) if number not in existing]
            if missing:
                errors["start_period"] = f"下列节次在学期 {term.code} 中不存在：{missing}。"
        if self.rule_code:
            duplicate = (
                ScheduleRule.objects.filter(offering__term_id=term.pk, rule_code=self.rule_code)
                .exclude(pk=self.pk)
                .exists()
            )
            if duplicate:
                errors["rule_code"] = f"规则代码 {self.rule_code} 在同一学期内已存在。"
        if self.classroom_id and self.classroom.school_id != term.school_id:
            errors["classroom"] = "教室必须与学期属于同一学校。"
        # M2M rows can only be inspected after the rule has a primary key; the
        # Admin form and the generation service also validate the scope.
        if self.pk and self.offering_id:
            allowed = assigned_teacher_ids(self.offering)
            selected = set(self.teachers.values_list("id", flat=True))
            extra = selected - allowed
            if extra:
                names = ", ".join(TeacherProfile.objects.filter(pk__in=extra).values_list("teacher_no", flat=True))
                errors["teachers"] = f"以下教师不在该课程开设的任课范围内：{names}。"
        if errors:
            raise ValidationError(errors)


class ClassSession(TimeStampedUUIDModel):
    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="sessions",
        verbose_name=_("课程开设"),
    )
    rule = models.ForeignKey(
        ScheduleRule,
        on_delete=models.PROTECT,
        related_name="sessions",
        verbose_name=_("生成规则"),
        null=True,
        blank=True,
    )
    origin_teaching_date = models.DateField(_("原教学日期"), null=True, blank=True)
    origin_period = models.PositiveSmallIntegerField(
        _("原起始节"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    start_period = models.PositiveSmallIntegerField(_("开始节"), validators=[MinValueValidator(1)])
    end_period = models.PositiveSmallIntegerField(_("结束节"), validators=[MinValueValidator(1)])
    start_at = models.DateTimeField(_("开始时间"))
    end_at = models.DateTimeField(_("结束时间"))
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.PROTECT,
        related_name="sessions",
        verbose_name=_("教室"),
    )
    status = models.CharField(
        _("状态"),
        max_length=16,
        choices=SessionStatus.choices,
        default=SessionStatus.SCHEDULED,
    )
    status_reason = models.TextField(_("状态说明"), blank=True, default="")
    planned_hours = models.DecimalField(
        _("计划学时"),
        max_digits=6,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0"))],
    )
    actual_hours = models.DecimalField(
        _("实际学时"),
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    completed_by_teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="completed_sessions",
        verbose_name=_("完成确认教师"),
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(_("完成确认时间"), null=True, blank=True)
    completion_ref = models.CharField(_("完成确认依据"), max_length=200, blank=True, default="")
    recorded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_sessions",
        verbose_name=_("记录操作人"),
        null=True,
        blank=True,
    )
    version = models.PositiveIntegerField(_("版本"), default=1)
    teachers = models.ManyToManyField(
        TeacherProfile,
        through="SessionTeacher",
        related_name="class_sessions",
        blank=True,
        verbose_name=_("实际授课教师"),
    )

    class Meta:
        verbose_name = _("课次")
        verbose_name_plural = _("课次")
        ordering = ("offering", "start_at")
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")),
                name="sch_session_range_chk",
            ),
            models.CheckConstraint(
                condition=Q(start_period__gte=1) & Q(end_period__gte=F("start_period")),
                name="sch_session_period_order_chk",
            ),
            models.CheckConstraint(
                condition=Q(planned_hours__gte=0),
                name="sch_session_planned_nonneg",
            ),
            models.CheckConstraint(
                condition=Q(actual_hours__isnull=True) | Q(actual_hours__gte=0),
                name="sch_session_actual_nonneg",
            ),
            # scheduled: no actual hours yet.
            models.CheckConstraint(
                condition=~Q(status=SessionStatus.SCHEDULED) | Q(actual_hours__isnull=True),
                name="sch_session_sched_hours_chk",
            ),
            # cancelled: actual hours are exactly zero and count towards nothing.
            models.CheckConstraint(
                condition=(~Q(status=SessionStatus.CANCELLED) | (Q(actual_hours__isnull=False) & Q(actual_hours=0))),
                name="sch_session_cancel_hours_chk",
            ),
            # completed: explicit non-negative amount plus the confirming teacher
            # and timestamp (a past date alone never completes a session).
            models.CheckConstraint(
                condition=(
                    ~Q(status=SessionStatus.COMPLETED)
                    | (
                        Q(actual_hours__isnull=False)
                        & Q(actual_hours__gte=0)
                        & Q(completed_at__isnull=False)
                        & Q(completed_by_teacher__isnull=False)
                    )
                ),
                name="sch_session_complete_chk",
            ),
            # Rule/origin triple is either fully present or fully absent.
            models.CheckConstraint(
                condition=(
                    (Q(rule__isnull=True) & Q(origin_teaching_date__isnull=True) & Q(origin_period__isnull=True))
                    | (Q(rule__isnull=False) & Q(origin_teaching_date__isnull=False) & Q(origin_period__isnull=False))
                ),
                name="sch_session_origin_chk",
            ),
            # Stable generation key: one session per rule + original teaching
            # date + original start period; it survives later moves.
            models.UniqueConstraint(
                fields=["rule", "origin_teaching_date", "origin_period"],
                condition=Q(rule__isnull=False),
                name="sch_session_rulekey_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["offering", "start_at"], name="sch_session_offering_idx"),
            models.Index(fields=["classroom", "start_at"], name="sch_session_classroom_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.offering_id} {self.start_at:%Y-%m-%d %H:%M}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.start_at and self.end_at:
            if not django_timezone.is_aware(self.start_at) or not django_timezone.is_aware(self.end_at):
                errors["start_at"] = "课次开始/结束时间必须是带时区的 datetime。"
            elif self.end_at <= self.start_at:
                errors["end_at"] = "课次结束时间必须晚于开始时间。"
        if self.actual_hours is not None and self.actual_hours < 0:
            errors["actual_hours"] = "实际学时不能为负数。"
        if errors:
            raise ValidationError(errors)


class SessionTeacher(TimeStampedUUIDModel):
    """Explicit through table so session teachers carry their own row/index."""

    # Link row only exists for its session; CASCADE keeps the join table clean
    # without ever deleting a session implicitly.
    session = models.ForeignKey(
        ClassSession,
        on_delete=models.CASCADE,
        related_name="session_teachers",
        verbose_name=_("课次"),
    )
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="session_links",
        verbose_name=_("教师"),
    )

    class Meta:
        verbose_name = _("课次授课教师")
        verbose_name_plural = _("课次授课教师")
        ordering = ("session", "teacher")
        constraints = [
            models.UniqueConstraint(fields=["session", "teacher"], name="sch_sessteacher_uniq"),
        ]
        indexes = [
            models.Index(fields=["teacher", "session"], name="sch_sessteacher_teacher_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} / {self.teacher_id}"


class PersonBlock(TimeStampedUUIDModel):
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="person_blocks",
        verbose_name=_("教师"),
        null=True,
        blank=True,
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="person_blocks",
        verbose_name=_("学生"),
        null=True,
        blank=True,
    )
    start_at = models.DateTimeField(_("开始时间"))
    end_at = models.DateTimeField(_("结束时间"))
    reason = models.TextField(_("原因"))
    block_code = models.CharField(_("占用编号"), max_length=64)

    class Meta:
        verbose_name = _("人员不可用")
        verbose_name_plural = _("人员不可用")
        ordering = ("start_at",)
        constraints = [
            # Exactly one of teacher/student must be set.
            models.CheckConstraint(
                condition=(
                    Q(teacher__isnull=False, student__isnull=True) | Q(teacher__isnull=True, student__isnull=False)
                ),
                name="sch_personblock_one_person",
            ),
            models.CheckConstraint(condition=Q(end_at__gt=F("start_at")), name="sch_personblock_range_chk"),
        ]
        indexes = [
            models.Index(fields=["teacher", "start_at"], name="sch_personblock_teacher_idx"),
            models.Index(fields=["student", "start_at"], name="sch_personblock_student_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.teacher_id or self.student_id} {self.start_at:%Y-%m-%d %H:%M}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if bool(self.teacher_id) == bool(self.student_id):
            errors["teacher"] = "教师和学生必须且只能填写一个。"
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            errors["end_at"] = "不可用结束时间必须晚于开始时间。"
        if errors:
            raise ValidationError(errors)


class SchoolSyncState(TimeStampedUUIDModel):
    """Sync counter used as the project-wide first write lock.

    B11 only creates and locks this row; ChangeLog allocation belongs to B14.
    """

    school = models.OneToOneField(
        School,
        on_delete=models.PROTECT,
        related_name="sync_state",
        verbose_name=_("学校"),
    )
    last_seq = models.BigIntegerField(_("已提交序号"), default=0)

    class Meta:
        verbose_name = _("学校同步状态")
        verbose_name_plural = _("学校同步状态")

    def __str__(self) -> str:
        return f"{self.school_id} seq={self.last_seq}"
