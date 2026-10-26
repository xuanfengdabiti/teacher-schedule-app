"""Shared API error types and the DRF exception handler."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class TeacherNotLinked(APIException):
    """403 for an authenticated account without an active teacher profile."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "TEACHER_NOT_LINKED"
    default_detail = "当前账号未关联教师档案，不能执行该操作。"


class TeacherDisabled(APIException):
    """403 for an account whose teacher profile is disabled."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "TEACHER_DISABLED"
    default_detail = "教师档案已停用，不能执行该操作。"


def teacher_not_linked_response(detail: str | None = None) -> dict:
    return {
        "code": "TEACHER_NOT_LINKED",
        "detail": detail or TeacherNotLinked.default_detail,
        "field_errors": {},
    }


def teacher_disabled_response(detail: str | None = None) -> dict:
    return {
        "code": "TEACHER_DISABLED",
        "detail": detail or TeacherDisabled.default_detail,
        "field_errors": {},
    }


def api_exception_handler(exc, context):
    """Keep component errors intact and give teacher-status errors stable bodies."""
    response = exception_handler(exc, context)
    if response is None:
        return response
    if isinstance(exc, TeacherNotLinked):
        response.data = teacher_not_linked_response(str(exc.detail))
    elif isinstance(exc, TeacherDisabled):
        response.data = teacher_disabled_response(str(exc.detail))
    return response
