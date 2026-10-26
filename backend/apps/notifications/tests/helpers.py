"""Account/teacher helpers shared by notification tests."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.academics.models import TeacherStatus


def make_user(email: str):
    return get_user_model().objects.create_user(email=email, password="test-pass-123")


def link_teacher(teacher, email: str):
    user = make_user(email)
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user
