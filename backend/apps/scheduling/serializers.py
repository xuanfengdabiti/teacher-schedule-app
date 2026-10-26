"""Serializers for the schedule read APIs (B13) and reschedule options (B30)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import CoverageKind
from apps.scheduling.calendar import teaching_week
from apps.scheduling.reschedule_options import (
    CONFLICT_CLASSROOM,
    CONFLICT_STUDENT,
    CONFLICT_TEACHER,
    ELIGIBILITY_AVAILABLE,
    ELIGIBILITY_CONFLICT,
    ELIGIBILITY_UNVERIFIED,
)
from apps.scheduling.selectors import session_local_date, term_is_current

_CONFLICT_CATEGORIES = (CONFLICT_TEACHER, CONFLICT_STUDENT, CONFLICT_CLASSROOM)
_ELIGIBILITIES = (ELIGIBILITY_AVAILABLE, ELIGIBILITY_CONFLICT, ELIGIBILITY_UNVERIFIED)


class RescheduleOptionsQuerySerializer(serializers.Serializer):
    """Input contract; the date defaults are filled by the view."""

    date_from = serializers.DateField(required=False)
    date_until = serializers.DateField(required=False)
    allowed_block_codes = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
    )
    classroom_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
    )
    allow_weekend = serializers.BooleanField(required=False, default=False)


class RescheduleOptionsErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class ConflictDetailSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=_CONFLICT_CATEGORIES)
    session_id = serializers.UUIDField(allow_null=True)
    course_name = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_null=True)
    student_count = serializers.IntegerField(allow_null=True)


class RescheduleOptionSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()
    classroom_id = serializers.UUIDField()
    classroom_code = serializers.CharField()
    eligibility = serializers.ChoiceField(choices=_ELIGIBILITIES)
    student_conflict_count = serializers.IntegerField()
    conflicts = ConflictDetailSerializer(many=True)
    missing_data = serializers.ListField(child=serializers.ChoiceField(choices=CoverageKind.choices))
    reasons = serializers.ListField(child=serializers.CharField())


class RescheduleOptionsResponseSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    schedule_version = serializers.IntegerField()
    checked_at = serializers.DateTimeField(default_timezone=None)
    data_completeness = serializers.ChoiceField(choices=("complete", "partial"))
    options = RescheduleOptionSerializer(many=True)


# ---------------------------------------------------------------------------
# B13 schedule read APIs
# ---------------------------------------------------------------------------


def _teacher_name(teacher) -> str:
    return teacher.display_name or teacher.formal_name


def _iso_datetime(value, term) -> str:
    return value.astimezone(term.tzinfo).isoformat()


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.1f}"


class ScheduleErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class TeacherBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        return _teacher_name(obj)


class OfferingTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()
    role = serializers.CharField()

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        return _teacher_name(obj)


class SessionOfferingBriefSerializer(serializers.Serializer):
    target_hours = serializers.CharField()
    lead_teacher = TeacherBriefSerializer()


class TeachingClassBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    class_code = serializers.CharField()
    name = serializers.CharField()


class ClassroomBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    classroom_code = serializers.CharField()
    campus = serializers.CharField()
    building = serializers.CharField()
    room_no = serializers.CharField()


class TermBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    schedule_version = serializers.IntegerField()


class TermSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    first_week_monday = serializers.DateField()
    teaching_week_count = serializers.IntegerField()
    timezone = serializers.CharField()
    schedule_version = serializers.IntegerField()
    is_current = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_is_current(self, obj) -> bool:
        return term_is_current(obj)


class PeriodSerializer(serializers.Serializer):
    period_number = serializers.IntegerField()
    start_time = serializers.TimeField(format="%H:%M")
    end_time = serializers.TimeField(format="%H:%M")
    block_code = serializers.CharField()


class CalendarExceptionSerializer(serializers.Serializer):
    date = serializers.DateField()
    kind = serializers.CharField()
    source_teaching_date = serializers.DateField(allow_null=True)
    reason = serializers.CharField()


class TeachingWeekSerializer(serializers.Serializer):
    week = serializers.IntegerField()
    monday = serializers.DateField()
    sunday = serializers.DateField()


class TermCalendarSerializer(TermSummarySerializer):
    periods = PeriodSerializer(many=True)
    exceptions = CalendarExceptionSerializer(many=True, source="calendar_exceptions")
    weeks = serializers.SerializerMethodField()

    @extend_schema_field(TeachingWeekSerializer(many=True))
    def get_weeks(self, obj) -> list[dict]:
        first_monday = obj.first_week_monday
        return [
            {
                "week": week,
                "monday": (first_monday + timedelta(days=7 * (week - 1))).isoformat(),
                "sunday": (first_monday + timedelta(days=7 * (week - 1) + 6)).isoformat(),
            }
            for week in range(1, obj.teaching_week_count + 1)
        ]


class SessionQuerySerializer(serializers.Serializer):
    """Validate the half-open, school-local date range and scope selectors."""

    scope = serializers.ChoiceField(choices=("mine", "offering", "teaching_class"), default="mine")
    offering_id = serializers.UUIDField(required=False)
    teaching_class_id = serializers.UUIDField(required=False)
    include_cancelled = serializers.BooleanField(required=False, default=True)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = serializers.DateField(required=True)
        fields["to"] = serializers.DateField(required=True)
        return fields

    def validate(self, attrs):
        date_from = attrs["from"]
        date_to = attrs["to"]
        span = (date_to - date_from).days
        if span < 1:
            raise serializers.ValidationError({"to": ["结束日期必须晚于开始日期。"]})
        if span > 42:
            raise serializers.ValidationError({"to": ["日期跨度不能超过 42 天。"]})

        scope = attrs["scope"]
        if scope == "offering" and not attrs.get("offering_id"):
            raise serializers.ValidationError({"offering_id": ["scope=offering 时 offering_id 必填。"]})
        if scope == "teaching_class" and not attrs.get("teaching_class_id"):
            raise serializers.ValidationError(
                {"teaching_class_id": ["scope=teaching_class 时 teaching_class_id 必填。"]}
            )
        return attrs


class SessionSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    offering_id = serializers.UUIDField()
    offering_code = serializers.CharField(source="offering.offering_code")
    course_code = serializers.CharField(source="offering.course.course_code")
    course_name = serializers.CharField(source="offering.course.name")
    teaching_class = serializers.SerializerMethodField()
    date = serializers.SerializerMethodField()
    weekday = serializers.SerializerMethodField()
    teaching_week = serializers.SerializerMethodField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()
    start_at = serializers.SerializerMethodField()
    end_at = serializers.SerializerMethodField()
    classroom = serializers.SerializerMethodField()
    teachers = serializers.SerializerMethodField()
    status = serializers.CharField()
    status_reason = serializers.CharField()
    is_mine = serializers.SerializerMethodField()
    is_moved = serializers.SerializerMethodField()
    origin_teaching_date = serializers.DateField(allow_null=True)
    version = serializers.IntegerField()

    @extend_schema_field(TeachingClassBriefSerializer)
    def get_teaching_class(self, obj) -> dict:
        return {
            "id": str(obj.offering.teaching_class_id),
            "class_code": obj.offering.teaching_class.class_code,
            "name": obj.offering.teaching_class.name,
        }

    @extend_schema_field(serializers.DateField())
    def get_date(self, obj) -> str:
        return session_local_date(obj).isoformat()

    @extend_schema_field(serializers.IntegerField())
    def get_weekday(self, obj) -> int:
        return session_local_date(obj).isoweekday()

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_teaching_week(self, obj) -> int | None:
        return teaching_week(obj.offering.term, session_local_date(obj))

    @extend_schema_field(serializers.DateTimeField())
    def get_start_at(self, obj) -> str:
        return _iso_datetime(obj.start_at, obj.offering.term)

    @extend_schema_field(serializers.DateTimeField())
    def get_end_at(self, obj) -> str:
        return _iso_datetime(obj.end_at, obj.offering.term)

    @extend_schema_field(ClassroomBriefSerializer)
    def get_classroom(self, obj) -> dict:
        return {
            "id": str(obj.classroom_id),
            "classroom_code": obj.classroom.classroom_code,
            "campus": obj.classroom.campus,
            "building": obj.classroom.building,
            "room_no": obj.classroom.room_no,
        }

    @extend_schema_field(TeacherBriefSerializer(many=True))
    def get_teachers(self, obj) -> list[dict]:
        links = sorted(
            obj.session_teachers.all(),
            key=lambda link: (link.teacher.teacher_no, str(link.teacher_id)),
        )
        return [{"id": str(link.teacher_id), "name": _teacher_name(link.teacher)} for link in links]

    @extend_schema_field(serializers.BooleanField())
    def get_is_mine(self, obj) -> bool:
        annotated = getattr(obj, "is_mine", None)
        if annotated is not None:
            return bool(annotated)
        teacher = self.context.get("teacher")
        if teacher is None:
            return False
        return any(link.teacher_id == teacher.id for link in obj.session_teachers.all())

    @extend_schema_field(serializers.BooleanField())
    def get_is_moved(self, obj) -> bool:
        if obj.origin_teaching_date is None:
            return False
        return session_local_date(obj) != obj.origin_teaching_date or (
            obj.origin_period is not None and obj.start_period != obj.origin_period
        )


class SessionListResponseSerializer(serializers.Serializer):
    term = TermBriefSerializer(allow_null=True)
    results = SessionSummarySerializer(many=True)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = serializers.DateField()
        fields["to"] = serializers.DateField()
        return fields


class SessionDetailSerializer(SessionSummarySerializer):
    planned_hours = serializers.SerializerMethodField()
    actual_hours = serializers.SerializerMethodField()
    completed_at = serializers.SerializerMethodField()
    enrolled_count = serializers.SerializerMethodField()
    offering = serializers.SerializerMethodField()
    term = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_planned_hours(self, obj) -> str:
        return _decimal_string(obj.planned_hours)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_actual_hours(self, obj) -> str | None:
        return _decimal_string(obj.actual_hours)

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_completed_at(self, obj) -> str | None:
        if obj.completed_at is None:
            return None
        return _iso_datetime(obj.completed_at, obj.offering.term)

    @extend_schema_field(serializers.IntegerField())
    def get_enrolled_count(self, obj) -> int:
        return self.context["enrolled_count"]

    @extend_schema_field(SessionOfferingBriefSerializer)
    def get_offering(self, obj) -> dict:
        return {
            "target_hours": _decimal_string(obj.offering.target_hours),
            "lead_teacher": {
                "id": str(obj.offering.lead_teacher_id),
                "name": _teacher_name(obj.offering.lead_teacher),
            },
        }

    @extend_schema_field(TermBriefSerializer)
    def get_term(self, obj) -> dict:
        return {
            "id": str(obj.offering.term_id),
            "code": obj.offering.term.code,
            "schedule_version": obj.offering.term.schedule_version,
        }


class OfferingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    offering_code = serializers.CharField()
    course_code = serializers.CharField(source="course.course_code")
    course_name = serializers.CharField(source="course.name")
    teaching_class = serializers.SerializerMethodField()
    target_hours = serializers.SerializerMethodField()
    lead_teacher = serializers.SerializerMethodField()
    teachers = serializers.SerializerMethodField()
    is_assigned = serializers.BooleanField(read_only=True)

    @extend_schema_field(TeachingClassBriefSerializer)
    def get_teaching_class(self, obj) -> dict:
        return {
            "id": str(obj.teaching_class_id),
            "class_code": obj.teaching_class.class_code,
            "name": obj.teaching_class.name,
        }

    @extend_schema_field(serializers.CharField())
    def get_target_hours(self, obj) -> str:
        return _decimal_string(obj.target_hours)

    @extend_schema_field(TeacherBriefSerializer)
    def get_lead_teacher(self, obj) -> dict:
        return {"id": str(obj.lead_teacher_id), "name": _teacher_name(obj.lead_teacher)}

    @extend_schema_field(OfferingTeacherSerializer(many=True))
    def get_teachers(self, obj) -> list[dict]:
        rows: list[dict] = []
        seen: set[tuple] = set()
        for assignment in obj.teaching_assignments.all():
            key = (assignment.teacher_id, assignment.role)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "id": str(assignment.teacher_id),
                    "name": _teacher_name(assignment.teacher),
                    "role": assignment.role,
                }
            )
        return rows
