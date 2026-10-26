"""Stable business errors for B33 inquiries."""

from __future__ import annotations


class InquiryError(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int,
        field_errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.field_errors = field_errors or {}

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "detail": self.detail,
            "field_errors": self.field_errors,
        }


def inquiry_not_found(detail: str = "协查不存在或不在本人可见范围。") -> InquiryError:
    return InquiryError("NOT_FOUND", detail, status_code=404)


def inquiry_invalid_range(detail: str) -> InquiryError:
    return InquiryError(
        "INVALID_DATE_RANGE",
        detail,
        status_code=400,
        field_errors={"to_at": [detail]},
    )


def inquiry_invalid_recipients(detail: str) -> InquiryError:
    return InquiryError(
        "INVALID_RECIPIENT",
        detail,
        status_code=400,
        field_errors={"recipient_ids": [detail]},
    )


def inquiry_not_recipient() -> InquiryError:
    return InquiryError(
        "NOT_INQUIRY_RECIPIENT",
        "只有该协查的接收人可以执行此操作。",
        status_code=403,
    )


def inquiry_not_initiator() -> InquiryError:
    return InquiryError(
        "NOT_INQUIRY_INITIATOR",
        "只有该协查的发起人可以结案。",
        status_code=403,
    )


def inquiry_closed() -> InquiryError:
    return InquiryError(
        "INQUIRY_CLOSED",
        "该协查已结案，不能再回复。",
        status_code=409,
    )


def inquiry_body_required() -> InquiryError:
    return InquiryError(
        "VALIDATION_ERROR",
        "回复结果为 other 时必须填写文字说明。",
        status_code=400,
        field_errors={"body": ["回复结果为 other 时必须填写文字说明。"]},
    )
