"""Inquiry candidate, creation, read, reply and close services (B33)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone as django_timezone

from apps.academics.contacts import teacher_name
from apps.academics.models import TeacherProfile
from apps.classrooms.models import Classroom
from apps.inquiries.exceptions import (
    InquiryError,
    inquiry_body_required,
    inquiry_closed,
    inquiry_invalid_range,
    inquiry_invalid_recipients,
    inquiry_not_found,
    inquiry_not_initiator,
    inquiry_not_recipient,
)
from apps.inquiries.models import Inquiry, InquiryRecipient, InquiryReply, InquiryReplyResult, InquiryStatus
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.scheduling.models import ClassSession, SessionStatus, SessionTeacher

MAX_INQUIRY_SPAN = timedelta(days=7)
MAX_RECIPIENTS = 20


@dataclass(frozen=True)
class CandidateEntry:
    teacher: TeacherProfile
    sessions: tuple[ClassSession, ...]


@dataclass(frozen=True)
class CandidateResult:
    classroom: Classroom
    from_at: datetime
    to_at: datetime
    candidates: tuple[CandidateEntry, ...]


def validate_range(from_at: datetime, to_at: datetime) -> None:
    if to_at <= from_at:
        raise inquiry_invalid_range("协查结束时间必须晚于开始时间。")
    if to_at - from_at > MAX_INQUIRY_SPAN:
        raise inquiry_invalid_range("协查时间跨度不能超过 7 天。")


def candidate_sessions(classroom: Classroom, from_at: datetime, to_at: datetime) -> list[ClassSession]:
    return list(
        ClassSession.objects.filter(
            classroom=classroom,
            status__in=(SessionStatus.SCHEDULED, SessionStatus.COMPLETED),
            start_at__lt=to_at,
            end_at__gt=from_at,
        )
        .select_related(
            "offering__course",
            "offering__teaching_class",
            "offering__term",
        )
        .prefetch_related(
            Prefetch(
                "session_teachers",
                queryset=SessionTeacher.objects.select_related("teacher").order_by(
                    "teacher__teacher_no",
                    "teacher_id",
                ),
            )
        )
        .order_by("start_at", "id")
    )


def inquiry_candidates(
    viewer: TeacherProfile,
    classroom: Classroom,
    from_at: datetime,
    to_at: datetime,
) -> CandidateResult:
    """Return selected-session candidates; GET is strictly read-only."""
    validate_range(from_at, to_at)
    grouped: dict[UUID, tuple[TeacherProfile, list[ClassSession]]] = {}
    for session in candidate_sessions(classroom, from_at, to_at):
        for link in session.session_teachers.all():
            if link.teacher_id == viewer.id:
                continue
            teacher, sessions = grouped.setdefault(link.teacher_id, (link.teacher, []))
            sessions.append(session)

    entries = [
        CandidateEntry(teacher=teacher, sessions=tuple(sessions))
        for teacher, sessions in sorted(
            grouped.values(),
            key=lambda item: (item[0].teacher_no, str(item[0].id)),
        )
    ]
    return CandidateResult(
        classroom=classroom,
        from_at=from_at,
        to_at=to_at,
        candidates=tuple(entries),
    )


def _notification_window(from_at: datetime, to_at: datetime) -> str:
    start = django_timezone.localtime(from_at)
    end = django_timezone.localtime(to_at)
    return f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"


def create_inquiry(
    initiator: TeacherProfile,
    classroom: Classroom,
    *,
    from_at: datetime,
    to_at: datetime,
    item_description: str,
    recipient_ids: list[UUID],
) -> Inquiry:
    """Create the request, recipient snapshots and messages in one transaction.

    Candidate membership is checked before any row is written, so an invalid
    recipient cannot leave a partial inquiry behind.  Only explicitly selected
    teachers receive ``inquiry_received``; there is no automatic group send.
    """
    description = (item_description or "").strip()
    if not description:
        raise InquiryError(
            "VALIDATION_ERROR",
            "物品描述不能为空。",
            status_code=400,
            field_errors={"item_description": ["物品描述不能为空。"]},
        )

    ordered_ids: list[UUID] = []
    seen: set[UUID] = set()
    for recipient_id in recipient_ids:
        if recipient_id not in seen:
            seen.add(recipient_id)
            ordered_ids.append(recipient_id)
    if not ordered_ids or len(ordered_ids) > MAX_RECIPIENTS:
        raise inquiry_invalid_recipients(f"接收人数量必须为 1–{MAX_RECIPIENTS} 个。")

    candidates = inquiry_candidates(initiator, classroom, from_at, to_at)
    by_teacher = {entry.teacher.id: entry for entry in candidates.candidates}
    missing = [recipient_id for recipient_id in ordered_ids if recipient_id not in by_teacher]
    if missing:
        raise inquiry_invalid_recipients("部分接收人不在该教室和时段的候选教师中。")

    with transaction.atomic():
        inquiry = Inquiry.objects.create(
            initiator=initiator,
            classroom=classroom,
            from_at=from_at,
            to_at=to_at,
            item_description=description,
            status=InquiryStatus.OPEN,
        )
        InquiryRecipient.objects.bulk_create(
            [
                InquiryRecipient(
                    inquiry=inquiry,
                    teacher=by_teacher[recipient_id].teacher,
                    related_session_ids=[str(session.id) for session in by_teacher[recipient_id].sessions],
                )
                for recipient_id in ordered_ids
            ]
        )
        window = _notification_window(from_at, to_at)
        for recipient_id in ordered_ids:
            entry = by_teacher[recipient_id]
            notify(
                entry.teacher,
                type=NotificationType.INQUIRY_RECEIVED,
                object_type="inquiry",
                object_id=inquiry.id,
                title="收到失物协查请求",
                body=(
                    f"{teacher_name(initiator)} 发起了 {window} 在 {classroom.classroom_code} 的失物协查。"
                    f"物品描述：{description}"
                ),
                event_key=f"inquiry:{inquiry.id}:received",
            )
    return inquiry


def inquiry_queryset():
    return Inquiry.objects.select_related("classroom", "initiator").prefetch_related(
        Prefetch(
            "recipients",
            queryset=InquiryRecipient.objects.select_related("teacher").order_by(
                "teacher__teacher_no",
                "teacher_id",
            ),
        ),
        Prefetch(
            "replies",
            queryset=InquiryReply.objects.select_related("author").order_by("created_at", "id"),
        ),
    )


def inquiries_for_box(teacher: TeacherProfile, *, box: str = "received", status: str | None = None):
    queryset = inquiry_queryset()
    if box == "sent":
        queryset = queryset.filter(initiator=teacher)
    else:
        queryset = queryset.filter(recipients__teacher=teacher).distinct()
    if status:
        queryset = queryset.filter(status=status)
    return queryset.order_by("-created_at", "-id")


def get_inquiry_for_teacher(teacher: TeacherProfile, inquiry_id: UUID) -> Inquiry:
    inquiry = (
        inquiry_queryset()
        .filter(Q(initiator=teacher) | Q(recipients__teacher=teacher))
        .distinct()
        .filter(pk=inquiry_id)
        .first()
    )
    if inquiry is None:
        raise inquiry_not_found()
    return inquiry


def mark_read(teacher: TeacherProfile, inquiry: Inquiry, now: datetime) -> InquiryRecipient:
    """Record first view only; reading never changes ``status``."""
    get_inquiry_for_teacher(teacher, inquiry.id)
    recipient = InquiryRecipient.objects.filter(inquiry=inquiry, teacher=teacher).first()
    if recipient is None:
        raise inquiry_not_recipient()
    if recipient.first_viewed_at is None:
        recipient.first_viewed_at = now
        recipient.save(update_fields=["first_viewed_at", "updated_at"])
    return recipient


def _locked_inquiry(inquiry_id: UUID) -> Inquiry:
    return Inquiry.objects.select_for_update().get(pk=inquiry_id)


def reply_to_inquiry(
    teacher: TeacherProfile,
    inquiry: Inquiry,
    *,
    result: str,
    body: str,
    now: datetime,
) -> InquiryReply:
    """Append one reply; the first changes ``open`` to ``replied``.

    The inquiry row lock serialises replies and closing, so after a close wins
    the other transaction observes ``closed`` and returns 409.
    """
    get_inquiry_for_teacher(teacher, inquiry.id)
    body = (body or "").strip()
    if result == InquiryReplyResult.OTHER and not body:
        raise inquiry_body_required()

    with transaction.atomic():
        locked = _locked_inquiry(inquiry.id)
        recipient = InquiryRecipient.objects.filter(inquiry=locked, teacher=teacher).first()
        if recipient is None:
            if locked.initiator_id == teacher.id:
                raise inquiry_not_recipient()
            raise inquiry_not_found()
        if locked.status == InquiryStatus.CLOSED:
            raise inquiry_closed()

        reply = InquiryReply.objects.create(
            inquiry=locked,
            author=teacher,
            result=result,
            body=body,
        )
        if locked.status == InquiryStatus.OPEN:
            locked.status = InquiryStatus.REPLIED
            locked.save(update_fields=["status", "updated_at"])

        body_text = f"{teacher_name(teacher)} 回复：{reply.get_result_display()}。"
        if body:
            body_text += f"说明：{body}"
        notify(
            locked.initiator,
            type=NotificationType.INQUIRY_REPLIED,
            object_type="inquiry_reply",
            object_id=reply.id,
            title="协查收到回复",
            body=body_text,
            event_key=f"inquiry_reply:{reply.id}",
        )
    return reply


def close_inquiry(
    teacher: TeacherProfile,
    inquiry: Inquiry,
    *,
    close_note: str = "",
    now: datetime,
) -> Inquiry:
    """Close as the initiator; repeated close is idempotent and adds nothing."""
    get_inquiry_for_teacher(teacher, inquiry.id)
    with transaction.atomic():
        locked = _locked_inquiry(inquiry.id)
        if locked.initiator_id != teacher.id:
            if InquiryRecipient.objects.filter(inquiry=locked, teacher=teacher).exists():
                raise inquiry_not_initiator()
            raise inquiry_not_found()
        if locked.status == InquiryStatus.CLOSED:
            return locked
        locked.status = InquiryStatus.CLOSED
        locked.closed_at = now
        locked.close_note = (close_note or "").strip()
        locked.save(update_fields=["status", "closed_at", "close_note", "updated_at"])
        return locked
