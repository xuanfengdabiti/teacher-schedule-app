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


class HandoffError(Exception):
    """A business failure raised by the B21 handoff services."""

    def __init__(
        self,
        *,
        code: str,
        detail: str,
        status_code: int,
        field_errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.field_errors = field_errors or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "field_errors": self.field_errors,
        }


def handoff_not_found(detail: str = "交接不存在或不在本人可见范围。") -> HandoffError:
    return HandoffError(
        code="NOT_FOUND",
        detail=detail,
        status_code=HTTPStatus.NOT_FOUND,
    )


def not_handoff_sender() -> HandoffError:
    return HandoffError(
        code="NOT_HANDOFF_SENDER",
        detail="只有发送人可以撤回该交接。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def not_handoff_recipient() -> HandoffError:
    return HandoffError(
        code="NOT_HANDOFF_RECIPIENT",
        detail="只有接收人可以标记已读或确认该交接。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def handoff_already_acknowledged() -> HandoffError:
    return HandoffError(
        code="HANDOFF_ALREADY_ACKNOWLEDGED",
        detail="该交接已被确认，不能撤回。",
        status_code=HTTPStatus.CONFLICT,
    )


def handoff_withdrawn() -> HandoffError:
    return HandoffError(
        code="HANDOFF_WITHDRAWN",
        detail="该交接已撤回，不能确认。",
        status_code=HTTPStatus.CONFLICT,
    )


def handoff_not_offering_teacher() -> HandoffError:
    return HandoffError(
        code="NOT_OFFERING_TEACHER",
        detail="只有该课程开设的当前任课教师或来源课次授课教师可以发起交接。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def handoff_invalid_session(field: str) -> HandoffError:
    return HandoffError(
        code="INVALID_SESSION",
        detail="来源课次或目标课次不属于该课程开设。",
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors={field: ["课次必须属于该课程开设。"]},
    )


def handoff_invalid_recipients(detail: str) -> HandoffError:
    return HandoffError(
        code="INVALID_RECIPIENT",
        detail=detail,
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors={"recipient_ids": [detail]},
    )


def handoff_invalid_note(detail: str) -> HandoffError:
    return HandoffError(
        code="INVALID_NOTE",
        detail=detail,
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors={"note_id": [detail]},
    )


def handoff_empty_content() -> HandoffError:
    return HandoffError(
        code="VALIDATION_ERROR",
        detail="交接内容不能为空。",
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors={"content": ["交接内容不能为空。"]},
    )
