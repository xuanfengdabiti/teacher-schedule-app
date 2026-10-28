"""B21 teaching handoffs: creation, status transitions and prefill.

Every rule lives here so the HTTP views only translate input/output.  The
snapshot written at creation time is authoritative: later note edits or
republishes never rewrite an existing handoff (A14).  Read, acknowledge and
withdraw are different actions; read alone never changes ``status``.

A handoff targets exactly one recipient.  Sending to several teachers creates
independent rows, each with its own read/acknowledge/withdraw timestamps and
event keys.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone as django_timezone

from apps.academics.models import CourseOffering, TeacherProfile, TeachingAssignment
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.scheduling.models import ClassSession, SessionStatus, SessionTeacher
from apps.scheduling.selectors import active_assignments_for_teacher, current_school_date
from apps.teaching.exceptions import (
    HandoffError,
    handoff_already_acknowledged,
    handoff_empty_content,
    handoff_invalid_note,
    handoff_invalid_recipients,
    handoff_invalid_session,
    handoff_not_found,
    handoff_not_offering_teacher,
    handoff_withdrawn,
    not_handoff_recipient,
    not_handoff_sender,
)
from apps.teaching.handoff_serializers import (
    prefill_content,
    session_brief,
    teacher_brief,
)
from apps.teaching.models import Handoff, HandoffStatus, TeachingNote

MAX_RECIPIENTS = 10
CONTENT_MAX_LENGTH = 5000
SUMMARY_LENGTH = 80

_HANDOFF_RELATIONS = (
    "offering__course",
    "offering__teaching_class",
    "offering__term",
    "sender",
    "recipient",
    "source_session__offering__term",
    "target_session__offering__term",
)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _now(value: datetime) -> datetime:
    if django_timezone.is_naive(value):
        raise ValueError("now must be a timezone-aware datetime")
    return value


def _active_offering_assignments(offering: CourseOffering) -> QuerySet[TeachingAssignment]:
    day = current_school_date()
    return TeachingAssignment.objects.filter(offering=offering).filter(
        Q(effective_until__isnull=True) | Q(effective_until__gt=day)
    )


def is_handoff_sender(teacher: TeacherProfile, offering: CourseOffering, source_session: ClassSession | None) -> bool:
    """A sender must currently teach the offering or the source session."""
    if active_assignments_for_teacher(teacher).filter(offering_id=offering.id).exists():
        return True
    return bool(
        source_session is not None
        and source_session.offering_id == offering.id
        and SessionTeacher.objects.filter(session=source_session, teacher=teacher).exists()
    )


def _require_sender(teacher: TeacherProfile, offering: CourseOffering, source_session: ClassSession | None) -> None:
    if not is_handoff_sender(teacher, offering, source_session):
        raise handoff_not_offering_teacher()


def _validate_session(offering: CourseOffering, session: ClassSession | None, *, field: str) -> None:
    if session is not None and session.offering_id != offering.id:
        raise handoff_invalid_session(field)


def _validate_note(
    sender: TeacherProfile,
    source_session: ClassSession | None,
    note: TeachingNote | None,
) -> None:
    if note is None:
        return
    if source_session is None:
        raise handoff_invalid_note("关联备注时必须同时指定来源课次。")
    if note.author_id != sender.id:
        raise handoff_invalid_note("只能关联本人在来源课次上的备注。")
    if note.session_id != source_session.id:
        raise handoff_invalid_note("只能关联来源课次上的备注。")
    if note.published_at is None:
        raise handoff_invalid_note("只能关联已发布的备注。")


def _clean_content(content: str) -> str:
    cleaned = (content or "").strip()
    if not cleaned:
        raise handoff_empty_content()
    if len(cleaned) > CONTENT_MAX_LENGTH:
        raise HandoffError(
            code="VALIDATION_ERROR",
            detail=f"交接内容不能超过 {CONTENT_MAX_LENGTH} 个字符。",
            status_code=400,
            field_errors={"content": [f"交接内容不能超过 {CONTENT_MAX_LENGTH} 个字符。"]},
        )
    return cleaned


def _dedupe_recipients(recipients: list[TeacherProfile]) -> list[TeacherProfile]:
    seen: set[UUID] = set()
    unique: list[TeacherProfile] = []
    for recipient in recipients:
        if recipient.id not in seen:
            seen.add(recipient.id)
            unique.append(recipient)
    return unique


def _validate_recipients(
    offering: CourseOffering,
    sender: TeacherProfile,
    recipients: list[TeacherProfile],
) -> list[TeacherProfile]:
    recipients = _dedupe_recipients(recipients)
    if not recipients:
        raise handoff_invalid_recipients("至少选择一位接收人。")
    if len(recipients) > MAX_RECIPIENTS:
        raise handoff_invalid_recipients(f"一次最多选择 {MAX_RECIPIENTS} 位接收人。")

    active_ids = set(_active_offering_assignments(offering).values_list("teacher_id", flat=True))
    invalid = [recipient for recipient in recipients if recipient.id == sender.id or recipient.id not in active_ids]
    if invalid:
        # Name the offending rows only to the sender; the response is for their
        # own handoff form and does not widen visibility to other offerings.
        names = "、".join(teacher_brief(recipient)["name"] for recipient in invalid)
        raise handoff_invalid_recipients(f"以下教师不是该课程开设的当前任课教师或就是发送人：{names}。")
    return recipients


def _reload(handoff_id: UUID) -> Handoff:
    return Handoff.objects.select_related(*_HANDOFF_RELATIONS).get(pk=handoff_id)


def _locked(handoff_id: UUID) -> Handoff:
    # Lock only the handoff row (``of=("self",)``).  Without it PostgreSQL would
    # also lock the joined offering/term/teacher rows, widening contention far
    # beyond the one status transition being serialized.  Nullable session
    # relations are not joined here; the full read happens after the update.
    try:
        return (
            Handoff.objects.select_for_update(of=("self",))
            .select_related("offering__course", "offering__term", "sender", "recipient")
            .get(pk=handoff_id)
        )
    except Handoff.DoesNotExist as exc:
        raise handoff_not_found() from exc


def _require_recipient(teacher: TeacherProfile, handoff: Handoff) -> None:
    if handoff.recipient_id == teacher.id:
        return
    if handoff.sender_id == teacher.id:
        raise not_handoff_recipient()
    raise handoff_not_found()


def _require_handoff_sender(teacher: TeacherProfile, handoff: Handoff) -> None:
    if handoff.sender_id == teacher.id:
        return
    if handoff.recipient_id == teacher.id:
        raise not_handoff_sender()
    raise handoff_not_found()


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def create_handoffs(
    sender: TeacherProfile,
    offering: CourseOffering,
    recipients: list[TeacherProfile],
    content: str,
    source_session: ClassSession | None,
    target_session: ClassSession | None,
    note: TeachingNote | None,
    now: datetime,
) -> list[Handoff]:
    """Create one pending handoff per recipient in a single transaction.

    Any invalid recipient, session or note aborts the whole send: a partial
    multi-recipient handoff would leave the sender unsure who received what.
    """
    _now(now)
    content = _clean_content(content)

    with transaction.atomic():
        try:
            offering = CourseOffering.objects.select_related("course", "term").get(pk=offering.pk)
        except CourseOffering.DoesNotExist as exc:
            raise handoff_not_found("课程开设不存在。") from exc
        _validate_session(offering, source_session, field="source_session_id")
        _validate_session(offering, target_session, field="target_session_id")
        _require_sender(sender, offering, source_session)
        recipients = _validate_recipients(offering, sender, recipients)
        _validate_note(sender, source_session, note)

        note_version = note.published_version if note is not None else None
        if note is not None and note_version is None:
            # published_at and published_version are a database-checked pair;
            # keep a readable error instead of an IntegrityError if data is bad.
            raise handoff_invalid_note("关联备注缺少发布版本。")

        created: list[Handoff] = []
        summary = content[:SUMMARY_LENGTH]
        for recipient in recipients:
            handoff = Handoff.objects.create(
                offering=offering,
                sender=sender,
                recipient=recipient,
                source_session=source_session,
                target_session=target_session,
                note=note,
                note_published_version=note_version,
                content_snapshot=content,
                status=HandoffStatus.PENDING,
            )
            notify(
                recipient,
                type=NotificationType.HANDOFF_RECEIVED,
                object_type="handoff",
                object_id=handoff.id,
                title="收到教学交接",
                body=f"《{offering.course.name}》教学交接：{summary}",
                event_key=f"handoff:{handoff.id}:received",
            )
            created.append(handoff)

    return [_reload(handoff.id) for handoff in created]


def mark_read(recipient: TeacherProfile, handoff: Handoff, now: datetime) -> Handoff:
    """Record a read timestamp only; the status stays pending (A14)."""
    _now(now)
    with transaction.atomic():
        locked = _locked(handoff.id)
        _require_recipient(recipient, locked)
        if locked.read_at is None:
            locked.read_at = now
            locked.save(update_fields=["read_at", "updated_at"])
    return _reload(handoff.id)


def acknowledge(recipient: TeacherProfile, handoff: Handoff, now: datetime) -> Handoff:
    """Explicitly accept a pending handoff; repeated calls are idempotent."""
    _now(now)
    with transaction.atomic():
        locked = _locked(handoff.id)
        _require_recipient(recipient, locked)
        if locked.status == HandoffStatus.WITHDRAWN:
            raise handoff_withdrawn()
        if locked.status == HandoffStatus.ACKNOWLEDGED:
            # Replaying an acknowledgement must return the original timestamp
            # and must not send a second message.
            return _reload(locked.id)

        locked.status = HandoffStatus.ACKNOWLEDGED
        locked.acknowledged_at = now
        if locked.read_at is None:
            locked.read_at = now
        locked.save(update_fields=["status", "acknowledged_at", "read_at", "updated_at"])

        notify(
            locked.sender,
            type=NotificationType.HANDOFF_ACKNOWLEDGED,
            object_type="handoff",
            object_id=locked.id,
            title="教学交接已确认",
            body=(f"{teacher_brief(locked.recipient)['name']} 已确认收到《{locked.offering.course.name}》的教学交接。"),
            event_key=f"handoff:{locked.id}:acknowledged",
        )
    return _reload(handoff.id)


def withdraw(sender: TeacherProfile, handoff: Handoff, now: datetime) -> Handoff:
    """Withdraw only a pending handoff; no withdrawal message is sent in v1."""
    _now(now)
    with transaction.atomic():
        locked = _locked(handoff.id)
        _require_handoff_sender(sender, locked)
        if locked.status == HandoffStatus.ACKNOWLEDGED:
            raise handoff_already_acknowledged()
        if locked.status == HandoffStatus.WITHDRAWN:
            return _reload(locked.id)

        locked.status = HandoffStatus.WITHDRAWN
        locked.withdrawn_at = now
        locked.save(update_fields=["status", "withdrawn_at", "updated_at"])
    return _reload(handoff.id)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


def get_handoff_for_teacher(teacher: TeacherProfile, handoff_id: UUID) -> Handoff:
    """Return a handoff only to its sender or recipient; others get 404.

    Hiding existence from unrelated teachers is the same rule as the note and
    schedule APIs.  This read never writes ``read_at``.
    """
    handoff = Handoff.objects.select_related(*_HANDOFF_RELATIONS).filter(pk=handoff_id).first()
    if handoff is None or teacher.id not in {handoff.sender_id, handoff.recipient_id}:
        raise handoff_not_found()
    return handoff


def handoffs_for_box(
    teacher: TeacherProfile,
    *,
    box: str,
    status: str | None = None,
) -> QuerySet[Handoff]:
    queryset = Handoff.objects.select_related(*_HANDOFF_RELATIONS)
    if box == "sent":
        queryset = queryset.filter(sender=teacher)
    else:
        queryset = queryset.filter(recipient=teacher)
    if status:
        queryset = queryset.filter(status=status)
    return queryset.order_by("-created_at", "-id")


def prefill_for_session(teacher: TeacherProfile, source_session: ClassSession) -> dict[str, Any]:
    """Build the send-form prefill without writing anything.

    Suggested recipients are the ``SessionTeacher`` rows of the first later
    scheduled session in the same offering, excluding the sender.  A suggested
    teacher who is no longer an active offering teacher is not added to the
    recipient list: the rule that recipients must be current active teachers is
    stronger than the convenience suggestion.
    """
    offering = source_session.offering
    _require_sender(teacher, offering, source_session)

    assignments = (
        _active_offering_assignments(offering)
        .exclude(teacher=teacher)
        .select_related("teacher")
        .order_by("teacher__teacher_no", "teacher_id")
    )
    recipients: list[TeacherProfile] = []
    seen: set[UUID] = set()
    for assignment in assignments:
        if assignment.teacher_id not in seen:
            seen.add(assignment.teacher_id)
            recipients.append(assignment.teacher)

    next_session = (
        ClassSession.objects.filter(
            offering=offering,
            status=SessionStatus.SCHEDULED,
            start_at__gt=source_session.start_at,
        )
        .select_related("offering__term", "offering__course")
        .order_by("start_at", "id")
        .first()
    )
    suggested_ids: set[UUID] = set()
    suggested_target_teachers: list[dict[str, str]] = []
    if next_session is not None:
        next_session_teachers = (
            SessionTeacher.objects.filter(session=next_session)
            .select_related("teacher")
            .order_by("teacher__teacher_no", "teacher_id")
        )
        suggested_target_teachers = [teacher_brief(row.teacher) for row in next_session_teachers]
        suggested_ids = {row.teacher_id for row in next_session_teachers if row.teacher_id != teacher.id}

    note = (
        TeachingNote.objects.filter(
            session=source_session,
            author=teacher,
            published_at__isnull=False,
        )
        .order_by("-published_at", "-id")
        .first()
    )

    return {
        "recipients": [
            {
                "id": str(recipient.id),
                "name": teacher_brief(recipient)["name"],
                "is_suggested": recipient.id in suggested_ids,
            }
            for recipient in recipients
        ],
        "suggested_target_session": session_brief(next_session),
        "suggested_target_teachers": suggested_target_teachers,
        "content": prefill_content(note),
        "note_id": str(note.id) if note is not None else None,
        "note_published_version": note.published_version if note is not None else None,
    }


# ---------------------------------------------------------------------------
# scheduling hook
# ---------------------------------------------------------------------------


def notify_pending_handoffs_for_moved_session(session: ClassSession, change) -> None:
    """Ask senders to re-check pending handoffs after their target moved.

    This never changes a recipient or transfers a handoff automatically
    (domain-design 5.2).  The event key includes the change id, so replaying the
    same formal change cannot produce a second message.
    """
    tzinfo = session.offering.term.tzinfo
    local_start = session.start_at.astimezone(tzinfo)
    local_end = session.end_at.astimezone(tzinfo)
    moved_to = (
        f"{local_start:%Y-%m-%d} {local_start:%H:%M}–{local_end:%H:%M}"
        f"（第 {session.start_period}–{session.end_period} 节）"
    )
    with transaction.atomic():
        pending = Handoff.objects.filter(
            target_session=session,
            status=HandoffStatus.PENDING,
        ).select_related("sender", "offering__course")
        for handoff in pending:
            notify(
                handoff.sender,
                type=NotificationType.HANDOFF_CHECK,
                object_type="handoff",
                object_id=handoff.id,
                title="请核对交接接收人",
                body=(
                    f"交接《{handoff.offering.course.name}》的目标课次已调整到 {moved_to}。"
                    "请核对接收人是否仍合适；系统不会自动更改接收人或转交。"
                ),
                event_key=f"handoff:{handoff.id}:check:{change.id}",
            )
