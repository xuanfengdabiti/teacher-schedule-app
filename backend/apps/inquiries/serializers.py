"""B33 inquiry API contracts and JSON payload builders."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from rest_framework import serializers

from apps.academics.contacts import teacher_name
from apps.inquiries.models import Inquiry, InquiryReplyResult, InquiryStatus
from apps.inquiries.services import MAX_RECIPIENTS, CandidateResult

INQUIRY_NOTICE = "排定课程记录，不代表实际到场或物品去向。"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(ZoneInfo(settings.TIME_ZONE)).isoformat()


def _session_iso(value: datetime, session) -> str:
    return value.astimezone(session.offering.term.tzinfo).isoformat()


def classroom_brief(classroom) -> dict[str, Any]:
    return {
        "id": str(classroom.id),
        "classroom_code": classroom.classroom_code,
        "campus": classroom.campus,
        "building": classroom.building,
        "room_no": classroom.room_no,
    }


def teacher_brief(teacher) -> dict[str, str]:
    return {
        "id": str(teacher.id),
        "name": teacher_name(teacher),
        "department": teacher.department,
    }


def candidate_payload(result: CandidateResult) -> dict[str, Any]:
    candidates = []
    for entry in result.candidates:
        sessions = [
            {
                "id": str(session.id),
                "course_name": session.offering.course.name,
                "start_at": _session_iso(session.start_at, session),
                "end_at": _session_iso(session.end_at, session),
                "start_period": session.start_period,
                "end_period": session.end_period,
            }
            for session in entry.sessions
        ]
        candidates.append(
            {
                "teacher": teacher_brief(entry.teacher),
                "related_session_ids": [str(session.id) for session in entry.sessions],
                "course_names": list(dict.fromkeys(session.offering.course.name for session in entry.sessions)),
                "sessions": sessions,
            }
        )
    return {
        "classroom": classroom_brief(result.classroom),
        "from_at": _iso(result.from_at),
        "to_at": _iso(result.to_at),
        "candidates": candidates,
    }


def inquiry_payload(inquiry: Inquiry, teacher) -> dict[str, Any]:
    recipients = list(inquiry.recipients.all())
    replies = list(inquiry.replies.all())
    replied_author_ids = {reply.author_id for reply in replies}
    return {
        "id": str(inquiry.id),
        "classroom": classroom_brief(inquiry.classroom),
        "from_at": _iso(inquiry.from_at),
        "to_at": _iso(inquiry.to_at),
        "item_description": inquiry.item_description,
        "status": inquiry.status,
        "initiator": teacher_brief(inquiry.initiator),
        "recipients": [
            {
                "teacher": teacher_brief(recipient.teacher),
                "related_session_ids": recipient.related_session_ids,
                "first_viewed_at": _iso(recipient.first_viewed_at),
                "has_replied": recipient.teacher_id in replied_author_ids,
            }
            for recipient in recipients
        ],
        "replies": [
            {
                "id": str(reply.id),
                "author": teacher_brief(reply.author),
                "result": reply.result,
                "body": reply.body,
                "created_at": _iso(reply.created_at),
            }
            for reply in replies
        ],
        "closed_at": _iso(inquiry.closed_at),
        "close_note": inquiry.close_note,
        "notice": INQUIRY_NOTICE,
        "created_at": _iso(inquiry.created_at),
        "is_initiator": inquiry.initiator_id == teacher.id,
    }


# ---------------------------------------------------------------------------
# Input contracts
# ---------------------------------------------------------------------------


class InquiryCandidateQuerySerializer(serializers.Serializer):
    classroom_id = serializers.UUIDField()
    from_at = serializers.DateTimeField()
    to_at = serializers.DateTimeField()


class InquiryCreateSerializer(serializers.Serializer):
    classroom_id = serializers.UUIDField()
    from_at = serializers.DateTimeField()
    to_at = serializers.DateTimeField()
    item_description = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)
    recipient_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_RECIPIENTS,
        allow_empty=False,
    )


class InquiryListQuerySerializer(serializers.Serializer):
    box = serializers.ChoiceField(choices=("sent", "received"), default="received")
    status = serializers.ChoiceField(choices=InquiryStatus.choices, required=False)


class InquiryReplyCreateSerializer(serializers.Serializer):
    result = serializers.ChoiceField(choices=InquiryReplyResult.choices)
    body = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        body = (attrs.get("body") or "").strip()
        if attrs["result"] == InquiryReplyResult.OTHER and not body:
            raise serializers.ValidationError({"body": ["回复结果为 other 时必须填写文字说明。"]})
        attrs["body"] = body
        return attrs


class InquiryCloseSerializer(serializers.Serializer):
    close_note = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")


class InquiryErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    field_errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------


class InquiryClassroomSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    classroom_code = serializers.CharField()
    campus = serializers.CharField()
    building = serializers.CharField()
    room_no = serializers.CharField()


class InquiryTeacherSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    department = serializers.CharField(allow_blank=True)


class InquiryCandidateSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    course_name = serializers.CharField()
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    start_period = serializers.IntegerField()
    end_period = serializers.IntegerField()


class InquiryCandidateSerializer(serializers.Serializer):
    teacher = InquiryTeacherSerializer()
    related_session_ids = serializers.ListField(child=serializers.UUIDField())
    course_names = serializers.ListField(child=serializers.CharField())
    sessions = InquiryCandidateSessionSerializer(many=True)


class InquiryCandidatesResponseSerializer(serializers.Serializer):
    classroom = InquiryClassroomSerializer()
    from_at = serializers.DateTimeField()
    to_at = serializers.DateTimeField()
    candidates = InquiryCandidateSerializer(many=True)


class InquiryRecipientSerializer(serializers.Serializer):
    teacher = InquiryTeacherSerializer()
    related_session_ids = serializers.ListField(child=serializers.UUIDField())
    first_viewed_at = serializers.DateTimeField(allow_null=True)
    has_replied = serializers.BooleanField()


class InquiryReplySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    author = InquiryTeacherSerializer()
    result = serializers.ChoiceField(choices=InquiryReplyResult.choices)
    body = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class InquirySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    classroom = InquiryClassroomSerializer()
    from_at = serializers.DateTimeField()
    to_at = serializers.DateTimeField()
    item_description = serializers.CharField()
    status = serializers.ChoiceField(choices=InquiryStatus.choices)
    initiator = InquiryTeacherSerializer()
    recipients = InquiryRecipientSerializer(many=True)
    replies = InquiryReplySerializer(many=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    close_note = serializers.CharField(allow_blank=True)
    notice = serializers.CharField()
    created_at = serializers.DateTimeField()
    is_initiator = serializers.BooleanField()


class PaginatedInquirySerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = InquirySerializer(many=True)
