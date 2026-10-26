"""B32 classroom routes."""

from __future__ import annotations

from django.urls import path

from apps.classrooms.views import ClassroomListView, ClassroomUsageView

urlpatterns = [
    path("classrooms/", ClassroomListView.as_view(), name="classroom-list"),
    path("classrooms/<uuid:classroom_id>/usage/", ClassroomUsageView.as_view(), name="classroom-usage"),
]
