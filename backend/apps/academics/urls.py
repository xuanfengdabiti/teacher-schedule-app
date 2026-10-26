"""B32 contact route."""

from __future__ import annotations

from django.urls import path

from apps.academics.views import TeacherContactView

urlpatterns = [
    path("teachers/<uuid:teacher_id>/contact/", TeacherContactView.as_view(), name="teacher-contact"),
]
