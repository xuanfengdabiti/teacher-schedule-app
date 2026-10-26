"""Bootstrap and incremental-change endpoints for offline App sync.

The bootstrap read runs in one ``REPEATABLE READ READ ONLY`` transaction.  The
cursor comes from the same snapshot as the payload, so a change committed while
the response is being read cannot be skipped by a cursor taken "after" it.
``changes`` advances its cursor over the raw log page even when every entity is
outside the teacher's current visibility; otherwise a later visible change
could be hidden behind an invisible one.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Min
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import TeacherProfile, Term
from apps.accounts.services import require_active_teacher
from apps.scheduling.models import ChangeEntityType, ChangeLog, SchoolSyncState
from apps.scheduling.selectors import (
    current_term_for_school,
    visible_offerings_for_read,
    visible_offerings_for_teacher,
    visible_sessions_for_read,
)
from apps.scheduling.serializers import (
    OfferingSerializer,
    SessionSummarySerializer,
    TermCalendarSerializer,
)
from apps.scheduling.sync_serializers import (
    SyncBootstrapResponseSerializer,
    SyncChangesResponseSerializer,
)
from apps.teaching.models import TeachingNote
from apps.teaching.selectors import readable_notes_for_term, readable_notes_queryset
from apps.teaching.serializers import sync_note_payload

CHANGES_PAGE_SIZE = 500
_CURSOR_RE = re.compile(r"^[0-9]+$")


@contextmanager
def read_only_repeatable_snapshot():
    """Open a read-only REPEATABLE READ transaction for one consistent read.

    On Django 5.2 + psycopg 3, entering ``atomic()`` emits PostgreSQL ``BEGIN``
    immediately when autocommit is switched off.  ``BEGIN`` is transaction
    control, not a query; the explicit ``SET TRANSACTION`` below is the first
    statement after it and no ``SELECT`` runs before it.  The bootstrap builder
    follows that rule: JWT/teacher authentication happens before the context,
    then every read and serializer evaluation happens inside it.  A test
    captures the SQL sequence and asserts ``BEGIN, SET TRANSACTION, SHOW …``.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield


def _last_seq(school_id) -> int:
    value = SchoolSyncState.objects.filter(school_id=school_id).values_list("last_seq", flat=True).first()
    return value or 0


def _assignment_scope_version(teacher: TeacherProfile) -> int:
    return teacher.assignment_scope_version


def build_bootstrap_payload(teacher: TeacherProfile) -> dict:
    """Read the current term, visible offerings/sessions and cursor together."""
    with read_only_repeatable_snapshot():
        last_seq = _last_seq(teacher.school_id)
        # authentication loaded the teacher before the snapshot; re-read the
        # scope version inside it so the advertised cache version and the
        # visible session set describe the same instant.
        scope_version = TeacherProfile.objects.values_list("assignment_scope_version", flat=True).get(pk=teacher.pk)
        term = current_term_for_school(teacher.school_id)
        generated_at = timezone.now()
        if term is not None:
            # Refetch with the relations the calendar serializer needs.  This
            # is still inside the snapshot; current_term_for_school's earlier
            # lookup only selected the term id.
            term = Term.objects.prefetch_related("periods", "calendar_exceptions").get(pk=term.pk)
            offerings = list(visible_offerings_for_read(teacher).filter(term=term).order_by("offering_code", "id"))
            sessions = list(
                visible_sessions_for_read(teacher)
                .filter(offering__term=term)
                .order_by("start_at", "offering__course__course_code", "id")
            )
            term_data = TermCalendarSerializer(term).data
            offering_data = OfferingSerializer(offerings, many=True).data
            session_data = SessionSummarySerializer(
                sessions,
                many=True,
                context={"teacher": teacher},
            ).data
            note_data = [sync_note_payload(note, teacher) for note in readable_notes_for_term(teacher, term)]
            tzinfo = term.tzinfo
        else:
            term_data = None
            offering_data = []
            session_data = []
            note_data = []
            tzinfo = ZoneInfo(settings.TIME_ZONE)

        payload = {
            "cursor": str(last_seq),
            "assignment_scope_version": scope_version,
            "term": term_data,
            "offerings": offering_data,
            "sessions": session_data,
            "notes": note_data,
            "generated_at": generated_at.astimezone(tzinfo).isoformat(),
        }
    # Return only after the read-only snapshot commits; no lazy queryset or
    # model field is evaluated outside the transaction.
    return payload


def _reset_payload(teacher: TeacherProfile, last_seq: int) -> dict:
    return {
        "next_cursor": str(last_seq),
        "has_more": False,
        "assignment_scope_version": _assignment_scope_version(teacher),
        "reset_required": True,
        "sessions": [],
        "terms": [],
        "notes": [],
    }


def _latest_logs(logs: list[ChangeLog]) -> list[ChangeLog]:
    """Keep one (the last) log per entity, ordered by that last change seq."""
    latest: dict[tuple[str, object], ChangeLog] = {}
    for log in logs:
        latest[(log.entity_type, log.entity_id)] = log
    return sorted(latest.values(), key=lambda item: item.change_seq)


def _session_data_for_logs(
    teacher: TeacherProfile,
    logs: list[ChangeLog],
) -> list[dict]:
    active_logs = [log for log in logs if not log.tombstone]
    if active_logs:
        visible_sessions = visible_sessions_for_read(teacher).filter(pk__in=[log.entity_id for log in active_logs])
        serialized = SessionSummarySerializer(
            list(visible_sessions),
            many=True,
            context={"teacher": teacher},
        ).data
        by_id = {str(item["id"]): item for item in serialized}
    else:
        by_id = {}

    tombstone_logs = [log for log in logs if log.tombstone]
    offering_ids = {log.offering_id for log in tombstone_logs if log.offering_id is not None}
    visible_offering_ids = set()
    if offering_ids:
        visible_offering_ids = set(
            visible_offerings_for_teacher(teacher).filter(id__in=offering_ids).values_list("id", flat=True)
        )

    result: list[dict] = []
    for log in logs:
        entity_id = str(log.entity_id)
        if log.tombstone:
            # A deleted session has no current row for the normal selector.
            # Its log's offering still lets us decide whether this teacher had
            # visibility; unrelated offerings are skipped without revealing
            # that the session ever existed.
            if entity_id in by_id or log.offering_id in visible_offering_ids:
                result.append({"id": entity_id, "tombstone": True})
            continue
        item = by_id.get(entity_id)
        if item is not None:
            result.append(item)
    return result


def _term_data_for_logs(teacher: TeacherProfile, logs: list[ChangeLog]) -> list[dict]:
    term_ids = [log.entity_id for log in logs if not log.tombstone]
    if not term_ids:
        return []
    terms = list(
        Term.objects.filter(school_id=teacher.school_id, pk__in=term_ids).prefetch_related(
            "periods", "calendar_exceptions"
        )
    )
    by_id = {str(term.id): TermCalendarSerializer(term).data for term in terms}
    return [by_id[str(log.entity_id)] for log in logs if not log.tombstone and str(log.entity_id) in by_id]


def _note_data_for_logs(
    teacher: TeacherProfile,
    logs: list[ChangeLog],
    page_versions: set[tuple[object, int]],
) -> list[dict]:
    """Serialize note logs without leaking another author's draft.

    A draft save after a published version must not produce a shared sync item.
    We therefore require the publish version itself to be present in this raw
    log page when the reader is not the author.  The cursor still advances over
    the skipped draft log.
    """
    active_logs = [log for log in logs if not log.tombstone]
    if not active_logs:
        return []
    notes = list(
        TeachingNote.objects.filter(pk__in=[log.entity_id for log in active_logs]).select_related(
            "author",
            "session__offering__term",
            "session__offering__course",
        )
    )
    by_id = {note.id: note for note in notes}
    readable_ids = set(readable_notes_queryset(teacher).filter(pk__in=by_id).values_list("id", flat=True))
    result: list[dict] = []
    for log in active_logs:
        note = by_id.get(log.entity_id)
        if note is None:
            continue
        if note.author_id == teacher.id:
            result.append(sync_note_payload(note, teacher))
            continue
        if note.published_at is None or note.id not in readable_ids:
            continue
        published_version = note.published_version
        if published_version is None or (note.id, published_version) not in page_versions:
            # A pure later draft edit is private to its author; the reader
            # already has the previous published snapshot.
            continue
        result.append(sync_note_payload(note, teacher))
    return result


def build_changes_payload(teacher: TeacherProfile, raw_cursor: str | None) -> dict:
    """Return at most 500 raw log entries, de-duplicated by entity for output."""
    last_seq = _last_seq(teacher.school_id)

    if raw_cursor is None or not _CURSOR_RE.fullmatch(raw_cursor):
        return _reset_payload(teacher, last_seq)
    try:
        cursor = int(raw_cursor)
    except ValueError:
        return _reset_payload(teacher, last_seq)
    if cursor > last_seq:
        return _reset_payload(teacher, last_seq)

    earliest_seq = ChangeLog.objects.filter(school_id=teacher.school_id).aggregate(value=Min("change_seq"))["value"]
    if earliest_seq is not None and earliest_seq > cursor + 1:
        # The client's next expected sequence was already pruned.  Replaying
        # later rows would silently lose the gap.
        return _reset_payload(teacher, last_seq)

    fetched = list(
        ChangeLog.objects.filter(
            school_id=teacher.school_id,
            change_seq__gt=cursor,
        ).order_by("change_seq")[: CHANGES_PAGE_SIZE + 1]
    )
    has_more = len(fetched) > CHANGES_PAGE_SIZE
    page = fetched[:CHANGES_PAGE_SIZE]
    next_cursor = str(page[-1].change_seq) if page else raw_cursor

    latest = _latest_logs(page)
    session_logs = [log for log in latest if log.entity_type == ChangeEntityType.SESSION]
    term_logs = [log for log in latest if log.entity_type == ChangeEntityType.TERM]
    note_logs = [log for log in latest if log.entity_type == ChangeEntityType.NOTE]
    page_note_versions = {(log.entity_id, log.version) for log in page if log.entity_type == ChangeEntityType.NOTE}

    return {
        "next_cursor": next_cursor,
        "has_more": has_more,
        "assignment_scope_version": _assignment_scope_version(teacher),
        "reset_required": False,
        "sessions": _session_data_for_logs(teacher, session_logs),
        "terms": _term_data_for_logs(teacher, term_logs),
        "notes": _note_data_for_logs(teacher, note_logs, page_note_versions),
    }


class SyncBootstrapView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="当前学期离线同步快照",
        description=(
            "在一个 REPEATABLE READ 只读事务中返回当前学期校历、本人可见 offerings 与全部可见课次；"
            "cursor 为同一快照中的学校 last_seq。cursor、assignment_scope_version 均为字符串/整数，"
            "客户端只透传 cursor，不解析。没有当前学期时 term=null、列表为空。"
        ),
        responses={200: SyncBootstrapResponseSerializer, 403: None},
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        return Response(build_bootstrap_payload(teacher))


class SyncChangesView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="增量同步变更",
        description=(
            "返回 cursor 之后最多 500 条变更日志对应的当前对象状态；同一对象一页只返回一次。"
            "next_cursor 推进到本页最后一条日志，即使该对象因不可见被过滤。"
            "cursor 非法、超前或早于保留的最早序号时返回 200 与 reset_required=true 的空列表，"
            "客户端应重新 bootstrap 并保留本地草稿。"
        ),
        parameters=[
            OpenApiParameter(
                name="cursor",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="客户端透传的字符串游标；缺失或非法时返回 reset_required=true。",
            )
        ],
        responses={200: SyncChangesResponseSerializer, 403: None},
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        return Response(build_changes_payload(teacher, request.query_params.get("cursor")))
