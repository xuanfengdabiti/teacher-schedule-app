"""Stable API errors for the B20 teaching-note slice."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any


class TeachingNoteError(Exception):
    """A business failure that the view renders with a stable error body."""

    def __init__(
        self,
        *,
        code: str,
        detail: str,
        status_code: int,
        field_errors: dict[str, list[str]] | None = None,
        current: dict[str, Any] | None = None,
        include_current: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.field_errors = field_errors or {}
        self.current = current
        self.include_current = include_current

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "detail": self.detail,
            "field_errors": self.field_errors,
        }
        if self.include_current:
            payload["current"] = self.current
        return payload


def session_not_found() -> TeachingNoteError:
    return TeachingNoteError(
        code="NOT_FOUND",
        detail="课次不存在或不在本人可见范围。",
        status_code=HTTPStatus.NOT_FOUND,
    )


def not_session_teacher() -> TeachingNoteError:
    return TeachingNoteError(
        code="NOT_SESSION_TEACHER",
        detail="只有该课次的实际授课教师可以保存本人备注。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def not_offering_teacher() -> TeachingNoteError:
    """A same-class teacher may see the session but not its shared notes."""

    return TeachingNoteError(
        code="NOT_OFFERING_TEACHER",
        detail="只有该课次或课程开设的实际授课教师可以查看教学备注。",
        status_code=HTTPStatus.FORBIDDEN,
    )
