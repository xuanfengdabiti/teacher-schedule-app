"""Admin registration for academic reference data."""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.academics.models import (
    CalendarException,
    Course,
    CourseOffering,
    DataCoverage,
    Enrollment,
    Period,
    School,
    Student,
    TeacherProfile,
    TeachingAssignment,
    TeachingClass,
    Term,
)
from apps.scheduling.services import generate_sessions_for_term


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_at")
    search_fields = ("code", "name")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "school",
        "start_date",
        "end_date",
        "teaching_week_count",
        "schedule_version",
    )
    list_filter = ("school", "timezone")
    search_fields = ("code", "name")
    date_hierarchy = "start_date"
    actions = ("generate_sessions",)

    @admin.action(description="按规则生成课次")
    def generate_sessions(self, request, queryset):
        """Run the shared scheduling service for every selected term.

        Admin must not expand rules on its own: the service owns locking,
        idempotency, mismatch reporting and the schedule version bump.
        """
        for term in queryset:
            try:
                result = generate_sessions_for_term(term)
            except ValidationError as exc:
                self.message_user(
                    request,
                    f"{term.code}：生成失败，未写入任何课次。{' '.join(exc.messages)}",
                    level=messages.ERROR,
                )
                continue
            self.message_user(
                request,
                (
                    f"{term.code}：新建 {result.created}，未变化 {result.unchanged}，"
                    f"需人工核对 {result.mismatched_count}，疑似重复 {len(result.suspected_duplicates)}。"
                ),
                level=messages.WARNING if result.mismatched_count or result.suspected_duplicates else messages.SUCCESS,
            )


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("term", "period_number", "start_time", "end_time", "block_code")
    list_filter = ("term", "block_code")
    search_fields = ("term__code", "block_code")
    ordering = ("term", "period_number")


@admin.register(CalendarException)
class CalendarExceptionAdmin(admin.ModelAdmin):
    list_display = ("term", "date", "kind", "source_teaching_date", "reason")
    list_filter = ("term", "kind")
    search_fields = ("term__code", "reason")
    date_hierarchy = "date"


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = (
        "teacher_no",
        "formal_name",
        "display_name",
        "department",
        "teacher_status",
        "is_active",
        "user",
    )
    list_filter = ("school", "teacher_status", "is_active", "phone_visibility", "wechat_visibility")
    search_fields = ("teacher_no", "formal_name", "display_name", "email")
    list_select_related = ("school", "user")
    autocomplete_fields = ("user",)
    readonly_fields = ("assignment_scope_version",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_no", "school")
    list_filter = ("school",)
    search_fields = ("student_no",)
    list_select_related = ("school",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("course_code", "name", "school")
    list_filter = ("school",)
    search_fields = ("course_code", "name")
    list_select_related = ("school",)


@admin.register(TeachingClass)
class TeachingClassAdmin(admin.ModelAdmin):
    list_display = ("class_code", "name", "term")
    list_filter = ("term",)
    search_fields = ("class_code", "name", "term__code")
    list_select_related = ("term",)


@admin.register(CourseOffering)
class CourseOfferingAdmin(admin.ModelAdmin):
    list_display = (
        "offering_code",
        "course",
        "teaching_class",
        "term",
        "target_hours",
        "lead_teacher",
        "minutes_per_hour",
    )
    list_filter = ("term", "course")
    search_fields = ("offering_code", "course__course_code", "course__name", "teaching_class__class_code")
    list_select_related = ("term", "course", "teaching_class", "lead_teacher")
    autocomplete_fields = ("lead_teacher",)


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("offering", "teacher", "role", "effective_from", "effective_until")
    list_filter = ("role", "offering__term")
    search_fields = ("offering__offering_code", "teacher__teacher_no", "teacher__formal_name")
    list_select_related = ("offering", "teacher")
    autocomplete_fields = ("offering", "teacher")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("offering", "student", "effective_from", "effective_until")
    list_filter = ("offering__term",)
    search_fields = ("offering__offering_code", "student__student_no")
    list_select_related = ("offering", "student")
    autocomplete_fields = ("offering", "student")


@admin.register(DataCoverage)
class DataCoverageAdmin(admin.ModelAdmin):
    list_display = (
        "term",
        "kind",
        "scope_key",
        "from_date",
        "until_date",
        "completeness",
        "source_updated_at",
    )
    list_filter = ("term", "kind", "completeness")
    search_fields = ("scope_key", "confirmation_ref", "term__code")
    list_select_related = ("school", "term")
    date_hierarchy = "from_date"
