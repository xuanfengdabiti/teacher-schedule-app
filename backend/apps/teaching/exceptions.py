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


class TeachingProgressError(Exception):
    """Business failure for B22 plans, completion and progress reads."""

    def __init__(
        self,
        *,
        code: str,
        detail: str,
        status_code: int,
        field_errors: dict[str, list[str]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.field_errors = field_errors or {}
        self.extra = extra or {}

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "detail": self.detail,
            "field_errors": self.field_errors,
        }
        payload.update(self.extra)
        return payload


def progress_not_found(detail: str = "对象不存在或不在本人可见范围。") -> TeachingProgressError:
    return TeachingProgressError(code="NOT_FOUND", detail=detail, status_code=HTTPStatus.NOT_FOUND)


def progress_not_offering_teacher() -> TeachingProgressError:
    return TeachingProgressError(
        code="NOT_OFFERING_TEACHER",
        detail="只有该课程开设的当前任课教师或该开设课次的授课教师可以查看教学计划与进度。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def not_plan_owner() -> TeachingProgressError:
    return TeachingProgressError(
        code="NOT_PLAN_OWNER",
        detail="只有该课程开设的课程负责人可以保存教学计划。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def plan_version_conflict(current: dict[str, Any] | None) -> TeachingProgressError:
    return TeachingProgressError(
        code="PLAN_VERSION_CONFLICT",
        detail="教学计划已在其他设备更新，请重新读取后再保存。",
        status_code=HTTPStatus.CONFLICT,
        extra={"current": current},
    )


def invalid_plan(detail: str, *, field_errors: dict[str, list[str]] | None = None) -> TeachingProgressError:
    return TeachingProgressError(
        code="INVALID_PLAN",
        detail=detail,
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors=field_errors,
    )


def completion_not_session_teacher() -> TeachingProgressError:
    return TeachingProgressError(
        code="NOT_SESSION_TEACHER",
        detail="只有该课次的实际授课教师可以确认或更正授课完成。",
        status_code=HTTPStatus.FORBIDDEN,
    )


def session_version_conflict(current: dict[str, Any]) -> TeachingProgressError:
    return TeachingProgressError(
        code="SESSION_VERSION_CONFLICT",
        detail="课次完成信息已更新，请重新读取后再提交。",
        status_code=HTTPStatus.CONFLICT,
        extra={"current": current},
    )


def invalid_completion_transition(detail: str) -> TeachingProgressError:
    return TeachingProgressError(
        code="INVALID_COMPLETION_TRANSITION",
        detail=detail,
        status_code=HTTPStatus.CONFLICT,
    )


def invalid_completion(detail: str, *, field_errors: dict[str, list[str]] | None = None) -> TeachingProgressError:
    return TeachingProgressError(
        code="INVALID_COMPLETION",
        detail=detail,
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors=field_errors,
    )


def invalid_allocation(detail: str, *, field: str = "allocations") -> TeachingProgressError:
    return TeachingProgressError(
        code="INVALID_ALLOCATION",
        detail=detail,
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors={field: [detail]},
    )


def allocation_exceeds(total: Any, limit: Any) -> TeachingProgressError:
    from apps.teaching.hours import hours_string

    total_text = hours_string(total)
    limit_text = hours_string(limit)
    return TeachingProgressError(
        code="ALLOCATION_EXCEEDS_SESSION_HOURS",
        detail=f"专题分配合计 {total_text} 学时，超过本课次实际学时 {limit_text}。",
        status_code=HTTPStatus.BAD_REQUEST,
        field_errors={"allocations": [f"合计不能超过本课次实际学时 {limit_text}。"]},
        extra={"allocation_total": total_text, "session_actual_hours": limit_text},
    )


class LessonPreparationError(Exception):
    """Business failures for the AI lesson-preparation slice."""

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


def lesson_ai_not_configured() -> LessonPreparationError:
    return LessonPreparationError(
        code="AI_NOT_CONFIGURED",
        detail="AI 备课尚未配置，请先完成配置后再试。",
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
    )


def lesson_ai_unavailable() -> LessonPreparationError:
    return LessonPreparationError(
        code="AI_UNAVAILABLE",
        detail="AI 服务暂时不可用，请稍后重试。",
        status_code=HTTPStatus.BAD_GATEWAY,
    )


def lesson_ai_timeout() -> LessonPreparationError:
    return LessonPreparationError(
        code="AI_TIMEOUT",
        detail="AI 生成超时，请稍后重试。",
        status_code=HTTPStatus.GATEWAY_TIMEOUT,
    )


def lesson_ai_request_failed() -> LessonPreparationError:
    return LessonPreparationError(
        code="AI_REQUEST_FAILED",
        detail="AI 服务未能完成这次生成，请稍后再试。",
        status_code=HTTPStatus.BAD_GATEWAY,
    )


def lesson_ai_invalid_response() -> LessonPreparationError:
    return LessonPreparationError(
        code="AI_INVALID_RESPONSE",
        detail="AI 返回的内容不完整，请重试。",
        status_code=HTTPStatus.BAD_GATEWAY,
    )


def preparation_version_conflict(current: dict[str, Any] | None) -> LessonPreparationError:
    return LessonPreparationError(
        code="PREPARATION_VERSION_CONFLICT",
        detail="备课草稿已在其他设备更新，请重新读取后再保存。",
        status_code=HTTPStatus.CONFLICT,
        current=current,
        include_current=True,
    )
