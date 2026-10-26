"""Scheduling rules, generated class sessions and availability data."""

from __future__ import annotations

import uuid
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

    Every sync-relevant write locks this row before updating its objects and
    appending ``ChangeLog`` rows.  The same lock order is shared by generation
    and every future importer/rescheduling/completion entry point.
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


class ChangeEntityType(models.TextChoices):
    SESSION = "session", _("课次")
    TERM = "term", _("学期")


class RescheduleStatus(models.TextChoices):
    DRAFT = "draft", _("草稿")
    SUBMITTED = "submitted", _("待审批")
    APPROVED = "approved", _("已批准")
    REJECTED = "rejected", _("已驳回")
    WITHDRAWN = "withdrawn", _("已撤回")


class RescheduleRequest(TimeStampedUUIDModel):
    """A teacher's intent to move one session; it never reserves a room.

    The request keeps a stable reference to the source session and the target
    values.  Approval goes through ``scheduling.changes.apply_session_change``,
    which rechecks the target under row locks.  ``check_summary`` is a point-in-
    time explanation, not an authorisation to bypass a later recheck.
    """

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="reschedule_requests",
        verbose_name=_("源课次"),
    )
    applicant = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="reschedule_requests",
        verbose_name=_("申请人"),
    )
    target_date = models.DateField(_("目标日期"))
    target_start_period = models.PositiveSmallIntegerField(
        _("目标开始节"),
        validators=[MinValueValidator(1)],
    )
    target_end_period = models.PositiveSmallIntegerField(
        _("目标结束节"),
        validators=[MinValueValidator(1)],
    )
    target_start_at = models.DateTimeField(_("目标开始时间"))
    target_end_at = models.DateTimeField(_("目标结束时间"))
    target_classroom = models.ForeignKey(
        Classroom,
        on_delete=models.PROTECT,
        related_name="reschedule_requests",
        verbose_name=_("目标教室"),
    )
    reason = models.TextField(_("调课说明"))
    status = models.CharField(
        _("状态"),
        max_length=16,
        choices=RescheduleStatus.choices,
        default=RescheduleStatus.DRAFT,
    )
    base_session_version = models.PositiveIntegerField(_("提交时源课次版本"))
    checked_schedule_version = models.PositiveIntegerField(_("检查时课表版本"))
    check_summary = models.JSONField(_("检查摘要"), default=dict, blank=True)
    needs_verification = models.BooleanField(_("待核验"), default=False)
    decision_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reschedule_decisions",
        verbose_name=_("审批人"),
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(_("审批时间"), null=True, blank=True)
    decision_ref = models.CharField(_("学校审批编号"), max_length=200, blank=True, default="")
    decision_note = models.TextField(_("审批说明"), blank=True, default="")
    forced_override = models.BooleanField(_("学校强制安排"), default=False)

    class Meta:
        verbose_name = _("调课申请")
        verbose_name_plural = _("调课申请")
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(target_start_period__gte=1) & Q(target_end_period__gte=F("target_start_period")),
                name="sch_resreq_period_order_chk",
            ),
            models.CheckConstraint(
                condition=Q(target_end_at__gt=F("target_start_at")),
                name="sch_resreq_range_chk",
            ),
            # At most one submitted request per session/applicant.  Drafts are
            # intentionally unlimited.  The service checks first for a readable
            # 409; this constraint closes the concurrent-creation race.
            models.UniqueConstraint(
                fields=["session", "applicant"],
                condition=Q(status=RescheduleStatus.SUBMITTED),
                name="sch_resreq_pending_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["applicant", "created_at"], name="sch_resreq_app_created_idx"),
            models.Index(fields=["status", "created_at"], name="sch_resreq_status_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} -> {self.target_date} {self.get_status_display()}"


class SessionChange(TimeStampedUUIDModel):
    """Immutable before/after snapshot of one formal session move.

    The unique ``(session, session_version)`` pair makes a retry of the same
    change idempotent at the database level and preserves a complete history.
    """

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.PROTECT,
        related_name="changes",
        verbose_name=_("课次"),
    )
    request = models.ForeignKey(
        RescheduleRequest,
        on_delete=models.SET_NULL,
        related_name="changes",
        verbose_name=_("调课申请"),
        null=True,
        blank=True,
    )
    session_version = models.PositiveIntegerField(_("变更后课次版本"))
    before_json = models.JSONField(_("变更前快照"))
    after_json = models.JSONField(_("变更后快照"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="session_changes",
        verbose_name=_("操作人"),
    )
    reason = models.TextField(_("变更原因"))
    approval_ref = models.CharField(_("审批依据"), max_length=200, blank=True, default="")
    forced_override = models.BooleanField(_("学校强制安排"), default=False)
    recorded_at = models.DateTimeField(_("记录时间"), default=django_timezone.now)

    class Meta:
        verbose_name = _("课次变更")
        verbose_name_plural = _("课次变更")
        ordering = ("session", "session_version")
        constraints = [
            models.UniqueConstraint(
                fields=["session", "session_version"],
                name="sch_sesschange_version_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.session_id} v{self.session_version}"


class ChangeLog(models.Model):
    """Append-only per-school sequence for incremental App synchronisation.

    The row is written in the same transaction as the object/version change.
    ``change_seq`` is allocated while holding the ``SchoolSyncState`` row lock,
    so commit order and cursor order cannot run backwards.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="change_logs",
        verbose_name=_("学校"),
    )
    change_seq = models.BigIntegerField(_("变更序号"))
    entity_type = models.CharField(_("对象类型"), max_length=16, choices=ChangeEntityType.choices)
    entity_id = models.UUIDField(_("对象 ID"))
    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="change_logs",
        verbose_name=_("课程开设"),
        null=True,
        blank=True,
    )
    version = models.PositiveIntegerField(_("对象版本"))
    tombstone = models.BooleanField(_("已删除标记"), default=False)
    changed_at = models.DateTimeField(_("变更时间"), auto_now_add=True)

    class Meta:
        verbose_name = _("同步变更")
        verbose_name_plural = _("同步变更")
        ordering = ("school", "change_seq")
        constraints = [
            models.UniqueConstraint(fields=["school", "change_seq"], name="sch_changelog_school_seq_uniq"),
        ]
        indexes = [
            models.Index(fields=["school", "change_seq"], name="sch_changelog_school_seq_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.school_id} #{self.change_seq} {self.entity_type}:{self.entity_id}"
