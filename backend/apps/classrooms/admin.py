"""Admin registration for classrooms."""

from django.contrib import admin

from apps.classrooms.models import Classroom, RoomBlock


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ("classroom_code", "campus", "building", "room_no", "capacity", "is_active", "school")
    list_filter = ("school", "campus", "is_active")
    search_fields = ("classroom_code", "campus", "building", "room_no")
    list_select_related = ("school",)


@admin.register(RoomBlock)
class RoomBlockAdmin(admin.ModelAdmin):
    list_display = ("classroom", "start_at", "end_at", "reason", "block_code")
    list_filter = ("classroom__school", "classroom")
    search_fields = ("block_code", "reason", "classroom__classroom_code")
    list_select_related = ("classroom",)
    date_hierarchy = "start_at"
