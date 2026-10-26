"""Academic reference data: school, terms, periods, people, courses, offerings.

The field names follow ``docs/import-spec.md``; business rules follow
``docs/domain-design.md`` sections 2 and 2.1.  Teaching records are never
cascade-deleted, so foreign keys use ``PROTECT`` unless the target is a pure
term-scoped configuration row (``Period.term``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import normalize_user_email
from apps.common.models import TimeStampedUUIDModel

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_HOUR_MINUTES = 45


def _ranges_overlap(
    start_a: date,
    end_a: date | None,
    start_b: date,
    end_b: date | None,
) -> bool:
    """Return whether two ``[start, end)`` date ranges overlap.

    A missing end date means "still effective", which is treated as infinity.
    """
    return start_a < (end_b or date.max) and start_b < (end_a or date.max)


class CalendarExceptionKind(models.TextChoices):
    CANCEL = "cancel", _("停课")
    REPLACE = "replace", _("替换教学日")
    CUSTOM = "custom", _("自定义")


class ContactVisibility(models.TextChoices):
    PRIVATE = "private", _("仅本人")
    COURSE_TEACHERS = "course_teachers", _("同课程教师")
    SCHOOL_TEACHERS = "school_teachers", _("全校教师")


class TeacherStatus(models.TextChoices):
    UNLINKED = "unlinked", _("未关联账号")
    PENDING = "pending", _("待教务确认")
    ACTIVE = "active", _("已关联")
    DISABLED = "disabled", _("已停用")


class AssignmentRole(models.TextChoices):
    LEAD = "lead", _("课程负责人")
    TEACHER = "teacher", _("任课教师")


class CoverageKind(models.TextChoices):
    CALENDAR = "calendar", _("校历与节次")
    TEACHER_ASSIGNMENTS = "teacher_assignments", _("任课范围与分工")
    TEACHER_SCHEDULE = "teacher_schedule", _("教师全校排课")
    STUDENT_ENROLLMENTS = "student_enrollments", _("实际选课名单")
    STUDENT_SCHEDULE = "student_schedule", _("学生全校排课")
    CLASSROOM_SCHEDULE = "classroom_schedule", _("教室课程占用")
    ROOM_BLOCKS = "room_blocks", _("教室非课程占用")
    PERSON_BLOCKS = "person_blocks", _("人员不可用")


class CoverageCompleteness(models.TextChoices):
    COMPLETE = "complete", _("完整")
    PARTIAL = "partial", _("部分")
    MISSING = "missing", _("缺失")


class School(TimeStampedUUIDModel):
    code = models.CharField(_("学校代码"), max_length=64, unique=True)
    name = models.CharField(_("学校名称"), max_length=200)

    class Meta:
        verbose_name = _("学校")
        verbose_name_plural = _("学校")
        ordering = ("code",)

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class Term(TimeStampedUUIDModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="terms", verbose_name=_("学校"))
    code = models.CharField(_("学期代码"), max_length=64)
    name = models.CharField(_("学期名称"), max_length=200)
    start_date = models.DateField(_("开始日期"))
    end_date = models.DateField(_("结束日期"))
    first_week_monday = models.DateField(_("教学第 1 周周一"))
    teaching_week_count = models.PositiveSmallIntegerField(
        _("教学周数"),
        validators=[MinValueValidator(1)],
    )
    timezone = models.CharField(_("学校时区"), max_length=64, default=DEFAULT_TIMEZONE)
    hour_minutes = models.PositiveSmallIntegerField(
        _("每学时分钟数"),
        default=DEFAULT_HOUR_MINUTES,
        validators=[MinValueValidator(1)],
    )
    schedule_version = models.PositiveIntegerField(_("课表版本"), default=1)

    class Meta:
        verbose_name = _("学期")
        verbose_name_plural = _("学期")
        ordering = ("school", "start_date", "code")
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="acad_term_school_code_uniq"),
            models.CheckConstraint(
                condition=Q(start_date__lte=F("end_date")),
                name="acad_term_dates_order",
            ),
            models.CheckConstraint(
                condition=Q(first_week_monday__lte=F("start_date")),
                name="acad_term_first_week_le_start",
            ),
            models.CheckConstraint(
                condition=Q(teaching_week_count__gt=0),
                name="acad_term_weeks_positive",
            ),
            models.CheckConstraint(
                condition=Q(hour_minutes__gt=0),
                name="acad_term_hour_minutes_pos",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.first_week_monday:
            if self.first_week_monday.weekday() != 0:
                errors["first_week_monday"] = "教学第 1 周周一必须是周一。"
            if self.start_date and self.first_week_monday > self.start_date:
                errors["first_week_monday"] = "教学第 1 周周一不能晚于学期开始日期。"
        if self.start_date and self.end_date and self.start_date > self.end_date:
            errors["end_date"] = "学期结束日期不能早于开始日期。"
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                errors["timezone"] = f"无效的 IANA 时区：{self.timezone}"
        if errors:
            raise ValidationError(errors)


class Period(TimeStampedUUIDModel):
    # The only CASCADE in this app: periods are pure term-scoped configuration.
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="periods", verbose_name=_("学期"))
    period_number = models.PositiveSmallIntegerField(_("节次序号"), validators=[MinValueValidator(1)])
    start_time = models.TimeField(_("开始时间"))
    end_time = models.TimeField(_("结束时间"))
    block_code = models.CharField(_("时段组"), max_length=64)

    class Meta:
        verbose_name = _("节次")
        verbose_name_plural = _("节次")
        ordering = ("term", "period_number")
        constraints = [
            models.UniqueConstraint(fields=["term", "period_number"], name="acad_period_term_no_uniq"),
            models.CheckConstraint(
                condition=Q(start_time__lt=F("end_time")),
                name="acad_period_time_order",
            ),
            models.CheckConstraint(
                condition=Q(period_number__gt=0),
                name="acad_period_number_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.term_id} 第 {self.period_number} 节"

    def clean(self) -> None:
        super().clean()
        if not (self.term_id and self.start_time and self.end_time):
            return
        if self.start_time >= self.end_time:
            raise ValidationError({"end_time": "节次结束时间必须晚于开始时间。"})
        others = Period.objects.filter(term_id=self.term_id).exclude(pk=self.pk)
        for other in others:
            if self.start_time < other.end_time and other.start_time < self.end_time:
                raise ValidationError(
                    {
                        "start_time": (
                            f"与第 {other.period_number} 节"
                            f"（{other.start_time:%H:%M}-{other.end_time:%H:%M}）时间重叠。"
                        )
                    }
                )


class CalendarException(TimeStampedUUIDModel):
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="calendar_exceptions", verbose_name=_("学期"))
    date = models.DateField(_("生效日期"))
    kind = models.CharField(_("例外类型"), max_length=16, choices=CalendarExceptionKind.choices)
    source_teaching_date = models.DateField(_("被替换的教学日期"), null=True, blank=True)
    reason = models.TextField(_("校历说明"))

    class Meta:
        verbose_name = _("校历例外")
        verbose_name_plural = _("校历例外")
        ordering = ("term", "date")
        constraints = [
            # A term has at most one exception per calendar date.
            models.UniqueConstraint(fields=["term", "date"], name="acad_exc_term_date_uniq"),
            # Only `replace` carries a source teaching date; cancel/custom never do.
            models.CheckConstraint(
                condition=(
                    Q(kind=CalendarExceptionKind.REPLACE, source_teaching_date__isnull=False)
                    | (~Q(kind=CalendarExceptionKind.REPLACE) & Q(source_teaching_date__isnull=True))
                ),
                name="acad_exc_source_matches_kind",
            ),
            # One source teaching day can be moved to at most one target date.
            models.UniqueConstraint(
                fields=["term", "source_teaching_date"],
                condition=Q(kind=CalendarExceptionKind.REPLACE),
                name="acad_exc_replace_src_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.get_kind_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.kind == CalendarExceptionKind.REPLACE and not self.source_teaching_date:
            errors["source_teaching_date"] = "替换教学日必须填写被替换的原教学日期。"
        if self.kind != CalendarExceptionKind.REPLACE and self.source_teaching_date:
            errors["source_teaching_date"] = "只有 replace 例外可以填写被替换的教学日期。"
        term = self.term if self.term_id else None
        if term is not None:
            for field_name in ("date", "source_teaching_date"):
                value = getattr(self, field_name)
                if value and not (term.start_date <= value <= term.end_date):
                    errors[field_name] = "日期必须落在学期起止日期（含两端）内。"
        if errors:
            raise ValidationError(errors)


class TeacherProfile(TimeStampedUUIDModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="teachers", verbose_name=_("学校"))
    teacher_no = models.CharField(_("教师工号"), max_length=64)
    formal_name = models.CharField(_("正式姓名"), max_length=100)
    display_name = models.CharField(_("显示名"), max_length=100, blank=True, default="")
    department = models.CharField(_("院系"), max_length=100, blank=True, default="")
    email = models.EmailField(_("邮箱"), blank=True, default="", db_index=True)
    phone = models.CharField(_("电话"), max_length=32, blank=True, default="")
    phone_visibility = models.CharField(
        _("电话可见范围"),
        max_length=32,
        choices=ContactVisibility.choices,
        default=ContactVisibility.PRIVATE,
    )
    wechat = models.CharField(_("微信"), max_length=64, blank=True, default="")
    wechat_visibility = models.CharField(
        _("微信可见范围"),
        max_length=32,
        choices=ContactVisibility.choices,
        default=ContactVisibility.PRIVATE,
    )
    is_active = models.BooleanField(_("名册启用"), default=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="teacher_profile",
        verbose_name=_("关联账号"),
        null=True,
        blank=True,
    )
    teacher_status = models.CharField(
        _("账号关联状态"),
        max_length=16,
        choices=TeacherStatus.choices,
        default=TeacherStatus.UNLINKED,
    )
    # Bumped in the same transaction whenever this teacher's visible assignment
    # scope changes; clients use it to drop cached courses (domain-design 2.1).
    assignment_scope_version = models.PositiveIntegerField(_("任课范围版本"), default=1)

    class Meta:
        verbose_name = _("教师")
        verbose_name_plural = _("教师")
        ordering = ("school", "teacher_no")
        constraints = [
            models.UniqueConstraint(fields=["school", "teacher_no"], name="acad_teacher_school_no_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.teacher_no} {self.formal_name}"

    def clean(self) -> None:
        super().clean()
        if self.email:
            self.email = normalize_user_email(self.email)

    def save(self, *args, **kwargs) -> None:
        # Roster imports bypass forms; store one canonical email form so roster
        # matching does not depend on casing.
        if self.email:
            self.email = normalize_user_email(self.email)
        super().save(*args, **kwargs)


class Student(TimeStampedUUIDModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="students", verbose_name=_("学校"))
    student_no = models.CharField(_("学号"), max_length=64)

    class Meta:
        verbose_name = _("学生")
        verbose_name_plural = _("学生")
        ordering = ("school", "student_no")
        constraints = [
            models.UniqueConstraint(fields=["school", "student_no"], name="acad_student_school_no_uniq"),
        ]

    def __str__(self) -> str:
        return self.student_no


class Course(TimeStampedUUIDModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="courses", verbose_name=_("学校"))
    course_code = models.CharField(_("课程代码"), max_length=64)
    name = models.CharField(_("课程名称"), max_length=200)

    class Meta:
        verbose_name = _("课程")
        verbose_name_plural = _("课程")
        ordering = ("school", "course_code")
        constraints = [
            models.UniqueConstraint(fields=["school", "course_code"], name="acad_course_school_code_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.course_code} {self.name}"


class TeachingClass(TimeStampedUUIDModel):
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="teaching_classes", verbose_name=_("学期"))
    class_code = models.CharField(_("教学班代码"), max_length=64)
    name = models.CharField(_("教学班名称"), max_length=200)

    class Meta:
        verbose_name = _("教学班")
        verbose_name_plural = _("教学班")
        ordering = ("term", "class_code")
        constraints = [
            models.UniqueConstraint(fields=["term", "class_code"], name="acad_class_term_code_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.class_code} {self.name}"


class CourseOffering(TimeStampedUUIDModel):
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="offerings", verbose_name=_("学期"))
    offering_code = models.CharField(_("开课代码"), max_length=64)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offerings", verbose_name=_("课程"))
    teaching_class = models.ForeignKey(
        TeachingClass,
        on_delete=models.PROTECT,
        related_name="offerings",
        verbose_name=_("教学班"),
    )
    target_hours = models.DecimalField(
        _("目标学时"),
        max_digits=6,
        decimal_places=1,
        validators=[MinValueValidator(Decimal("0"))],
    )
    lead_teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="led_offerings",
        verbose_name=_("课程负责人"),
    )
    minutes_per_hour = models.PositiveSmallIntegerField(
        _("每学时分钟数"),
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="留空时新建自动继承学期的 hour_minutes。",
    )
    required_equipment = ArrayField(
        models.CharField(max_length=64),
        default=list,
        blank=True,
        verbose_name=_("必需设备"),
    )

    class Meta:
        verbose_name = _("课程开设")
        verbose_name_plural = _("课程开设")
        ordering = ("term", "offering_code")
        constraints = [
            models.UniqueConstraint(fields=["term", "offering_code"], name="acad_offering_term_code_uniq"),
            models.CheckConstraint(
                condition=Q(target_hours__gte=0),
                name="acad_offering_hours_nonneg",
            ),
            models.CheckConstraint(
                condition=Q(minutes_per_hour__gt=0),
                name="acad_offering_minutes_pos",
            ),
        ]
        indexes = [
            models.Index(fields=["term", "teaching_class"], name="acad_offering_term_class_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.offering_code} {self.course.name}"

    def full_clean(self, *args, **kwargs) -> None:
        # A new offering may leave minutes_per_hour empty to inherit the term
        # default.  Fill it before field validation so Admin/DRF forms and
        # direct ``full_clean()`` calls accept the same input as ``save()``.
        if self.minutes_per_hour is None and self.term_id:
            self.minutes_per_hour = self.term.hour_minutes
        super().full_clean(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.term_id and self.teaching_class_id and self.teaching_class.term_id != self.term_id:
            errors["teaching_class"] = "教学班必须属于同一学期。"
        if self.term_id and self.course_id and self.course.school_id != self.term.school_id:
            errors["course"] = "课程目录必须与学期属于同一学校。"
        if self.term_id and self.lead_teacher_id and self.lead_teacher.school_id != self.term.school_id:
            errors["lead_teacher"] = "课程负责人必须属于同一学校。"
        if self.minutes_per_hour is None and self.term_id:
            self.minutes_per_hour = self.term.hour_minutes
        if self.required_equipment:
            self.required_equipment = sorted(set(self.required_equipment))
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        if self.minutes_per_hour is None and self.term_id:
            self.minutes_per_hour = self.term.hour_minutes
        if self.required_equipment:
            self.required_equipment = sorted(set(self.required_equipment))
        super().save(*args, **kwargs)


class TeachingAssignment(TimeStampedUUIDModel):
    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="teaching_assignments",
        verbose_name=_("课程开设"),
    )
    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name="teaching_assignments",
        verbose_name=_("教师"),
    )
    role = models.CharField(_("角色"), max_length=16, choices=AssignmentRole.choices, default=AssignmentRole.TEACHER)
    effective_from = models.DateField(_("生效日期"))
    effective_until = models.DateField(_("失效日期"), null=True, blank=True)

    class Meta:
        verbose_name = _("任课分工")
        verbose_name_plural = _("任课分工")
        ordering = ("offering", "teacher", "effective_from")
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "teacher", "effective_from"],
                name="acad_assignment_uniq",
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True) | Q(effective_until__gt=F("effective_from")),
                name="acad_assignment_range_chk",
            ),
        ]
        indexes = [
            models.Index(
                fields=["teacher", "effective_from", "effective_until"],
                name="acad_assign_teacher_idx",
            ),
            models.Index(fields=["offering", "effective_from"], name="acad_assign_offering_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.offering_id} / {self.teacher_id} / {self.role}"

    def clean(self) -> None:
        super().clean()
        if self.effective_from and self.effective_until and self.effective_until <= self.effective_from:
            raise ValidationError({"effective_until": "失效日期必须晚于生效日期。"})
        if not (self.offering_id and self.teacher_id and self.effective_from):
            return
        if self.offering.term.school_id != self.teacher.school_id:
            raise ValidationError({"teacher": "教师必须与课程开设属于同一学校。"})
        others = TeachingAssignment.objects.filter(
            offering_id=self.offering_id,
            teacher_id=self.teacher_id,
        ).exclude(pk=self.pk)
        for other in others:
            if _ranges_overlap(
                self.effective_from,
                self.effective_until,
                other.effective_from,
                other.effective_until,
            ):
                raise ValidationError({"effective_from": "同一课程开设与教师的任课有效期不能重叠。"})


class Enrollment(TimeStampedUUIDModel):
    offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="enrollments",
        verbose_name=_("课程开设"),
    )
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments", verbose_name=_("学生"))
    effective_from = models.DateField(_("生效日期"))
    effective_until = models.DateField(_("失效日期"), null=True, blank=True)

    class Meta:
        verbose_name = _("实际选课")
        verbose_name_plural = _("实际选课")
        ordering = ("offering", "student", "effective_from")
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "student", "effective_from"],
                name="acad_enrollment_uniq",
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True) | Q(effective_until__gt=F("effective_from")),
                name="acad_enrollment_range_chk",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "effective_from", "effective_until"],
                name="acad_enroll_student_idx",
            ),
            models.Index(fields=["offering", "effective_from"], name="acad_enroll_offering_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.offering_id} / {self.student_id}"

    def clean(self) -> None:
        super().clean()
        if self.effective_from and self.effective_until and self.effective_until <= self.effective_from:
            raise ValidationError({"effective_until": "失效日期必须晚于生效日期。"})
        if not (self.offering_id and self.student_id and self.effective_from):
            return
        others = Enrollment.objects.filter(
            offering_id=self.offering_id,
            student_id=self.student_id,
        ).exclude(pk=self.pk)
        for other in others:
            if _ranges_overlap(
                self.effective_from,
                self.effective_until,
                other.effective_from,
                other.effective_until,
            ):
                raise ValidationError({"effective_from": "同一课程开设与学生的选课有效期不能重叠。"})

    @classmethod
    def active_student_ids(cls, offering: CourseOffering, on_date: date) -> set[UUID]:
        """Return student IDs active on ``on_date`` using ``[from, until)``.

        The query lives in ``selectors.active_student_ids``; this thin wrapper
        keeps the selector discoverable from the model for service code.
        """
        from apps.academics.selectors import active_student_ids

        return active_student_ids(offering, on_date)


class DataCoverage(TimeStampedUUIDModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="data_coverages", verbose_name=_("学校"))
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="data_coverages", verbose_name=_("学期"))
    kind = models.CharField(_("数据类别"), max_length=32, choices=CoverageKind.choices)
    scope_key = models.CharField(_("范围键"), max_length=200)
    from_date = models.DateField(_("覆盖开始日期"))
    until_date = models.DateField(_("覆盖结束日期（不含）"))
    completeness = models.CharField(
        _("完整程度"),
        max_length=16,
        choices=CoverageCompleteness.choices,
    )
    source_updated_at = models.DateTimeField(_("来源更新时间"))
    confirmation_ref = models.CharField(_("确认依据"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("数据覆盖声明")
        verbose_name_plural = _("数据覆盖声明")
        ordering = ("term", "kind", "scope_key", "from_date")
        constraints = [
            models.CheckConstraint(
                condition=Q(until_date__gt=F("from_date")),
                name="acad_coverage_range_chk",
            ),
            # A `complete` claim must point at the school export / confirmation;
            # the importer is never allowed to fill this in on its own.
            models.CheckConstraint(
                condition=~Q(completeness=CoverageCompleteness.COMPLETE) | ~Q(confirmation_ref=""),
                name="acad_coverage_complete_ref",
            ),
        ]
        indexes = [
            models.Index(
                fields=["term", "kind", "scope_key", "from_date"],
                name="acad_coverage_term_kind_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.scope_key} {self.from_date}~{self.until_date}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.until_date and self.from_date and self.until_date <= self.from_date:
            errors["until_date"] = "覆盖结束日期（不含）必须晚于开始日期。"
        if self.completeness == CoverageCompleteness.COMPLETE and not self.confirmation_ref:
            errors["confirmation_ref"] = "声明 complete 时必须填写学校导出或教务确认依据。"
        if self.school_id and self.term_id and self.term.school_id != self.school_id:
            errors["term"] = "数据覆盖声明与学期必须属于同一学校。"
        if errors:
            raise ValidationError(errors)
