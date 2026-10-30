"""Classroom catalogue and per-day usage history (B32).

The usage response is a *scheduled-record* view.  It never claims a teacher or
an item was physically present.  Formal moves keep the same :class:`ClassSession`
row, so an old room finds the move in ``SessionChange.before_json`` while the
new room sees the normal session with ``is_moved=true`` (A15).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.academics.contacts import ContactVisibilityResolver, teacher_name
from apps.academics.models import Term
from apps.classrooms.models import Classroom, RoomBlock
from apps.scheduling.models import ClassSession, SessionChange, SessionStatus, SessionTeacher
from apps.scheduling.reschedule_options import (
    classroom_codes_coverage_complete,
    classroom_day_coverage_complete,
)

USAGE_NOTICE = "排定课程记录，不代表实际到场或物品去向。"
DATA_COMPLETE = "complete"
DATA_INCOMPLETE = "incomplete"
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_UNVERIFIED = "unverified"

ENTRY_SESSION = "session"
ENTRY_CANCELLED_SESSION = "cancelled_session"
ENTRY_MOVED_OUT = "moved_out"
ENTRY_ROOM_BLOCK = "room_block"
ENTRY_KIND_CHOICES = (
    (ENTRY_SESSION, "课次"),
    (ENTRY_CANCELLED_SESSION, "停课课次"),
    (ENTRY_MOVED_OUT, "已调出课次"),
    (ENTRY_ROOM_BLOCK, "教室占用"),
)


def classrooms_for_school(
    school_id,
    *,
    q: str = "",
    campus: str = "",
    building: str = "",
):
    """Return the school's rooms in stable campus/building/room order."""
    queryset = Classroom.objects.filter(school_id=school_id)
    if q:
        queryset = queryset.filter(
            Q(classroom_code__icontains=q) | Q(campus__icontains=q) | Q(building__icontains=q) | Q(room_no__icontains=q)
        )
    if campus:
        queryset = queryset.filter(campus=campus)
    if building:
        queryset = queryset.filter(building=building)
    return queryset.order_by("campus", "building", "room_no", "id")


def _iso(value: datetime, tzinfo: ZoneInfo) -> str:
    return value.astimezone(tzinfo).isoformat()


def _is_moved(session: ClassSession, tzinfo: ZoneInfo) -> bool:
    if session.origin_teaching_date is None:
        return False
    local_day = session.start_at.astimezone(tzinfo).date()
    return local_day != session.origin_teaching_date or (
        session.origin_period is not None and session.start_period != session.origin_period
    )


def _term_for_day(school_id, day: date) -> Term | None:
    return (
        Term.objects.filter(school_id=school_id, start_date__lte=day, end_date__gte=day)
        .order_by("-start_date", "-code")
        .first()
    )


@dataclass(frozen=True)
class FreeClassroom:
    classroom: Classroom
    availability: str
    data_completeness: str


@dataclass(frozen=True)
class FreeClassroomResult:
    day: date
    start_time: time
    end_time: time
    checked_at: datetime
    available_count: int
    unverified_count: int
    items: tuple[FreeClassroom, ...]


def free_classrooms_for_school(
    school_id,
    *,
    day: date,
    start_time: time,
    end_time: time,
    q: str = "",
    campus: str = "",
    building: str = "",
    min_capacity: int | None = None,
    now: datetime | None = None,
) -> FreeClassroomResult:
    """Rooms without a known overlap in ``[start, end)`` on one local day.

    All occupancy and coverage is fetched in batches; the query count does not
    grow with the number of rooms.  ``available`` means the coverage contract
    proves the room schedule and room blocks are complete for that day;
    otherwise the room is ``unverified`` and must not be presented as free.
    """
    term = _term_for_day(school_id, day)
    tzinfo = term.tzinfo if term is not None else ZoneInfo(settings.TIME_ZONE)
    start_at = datetime.combine(day, start_time, tzinfo=tzinfo)
    end_at = datetime.combine(day, end_time, tzinfo=tzinfo)

    queryset = classrooms_for_school(
        school_id,
        q=q,
        campus=campus,
        building=building,
    ).filter(is_active=True)
    if min_capacity is not None:
        queryset = queryset.filter(capacity__gte=min_capacity)
    rooms = list(queryset)
    room_ids = [room.id for room in rooms]

    occupied_ids: set = set()
    if room_ids:
        occupied_ids.update(
            ClassSession.objects.filter(
                classroom_id__in=room_ids,
                start_at__lt=end_at,
                end_at__gt=start_at,
            )
            .exclude(status=SessionStatus.CANCELLED)
            .values_list("classroom_id", flat=True)
        )
        occupied_ids.update(
            RoomBlock.objects.filter(
                classroom_id__in=room_ids,
                start_at__lt=end_at,
                end_at__gt=start_at,
            ).values_list("classroom_id", flat=True)
        )

    free_rooms = [room for room in rooms if room.id not in occupied_ids]
    completeness = classroom_codes_coverage_complete(
        school_id=school_id,
        classroom_codes=[room.classroom_code for room in free_rooms],
        day=day,
    )
    items: list[FreeClassroom] = []
    for room in free_rooms:
        complete = completeness.get(room.classroom_code, False)
        items.append(
            FreeClassroom(
                classroom=room,
                availability=AVAILABILITY_AVAILABLE if complete else AVAILABILITY_UNVERIFIED,
                data_completeness=DATA_COMPLETE if complete else DATA_INCOMPLETE,
            )
        )
    available_count = sum(1 for item in items if item.availability == AVAILABILITY_AVAILABLE)
    return FreeClassroomResult(
        day=day,
        start_time=start_time,
        end_time=end_time,
        checked_at=now or timezone.now(),
        available_count=available_count,
        unverified_count=len(items) - available_count,
        items=tuple(items),
    )


@dataclass(frozen=True)
class ClassroomUsage:
    classroom: Classroom
    date: date
    entries: tuple[dict, ...]
    data_completeness: str


def classroom_usage_for_day(
    classroom: Classroom,
    day: date,
    *,
    viewer,
) -> ClassroomUsage:
    """Build one local-day usage history with a constant query count."""
    term = _term_for_day(classroom.school_id, day)
    tzinfo = term.tzinfo if term is not None else ZoneInfo(settings.TIME_ZONE)
    day_start = datetime.combine(day, time.min, tzinfo=tzinfo)
    day_end = day_start + timedelta(days=1)

    sessions = list(
        ClassSession.objects.filter(
            classroom=classroom,
            start_at__gte=day_start,
            start_at__lt=day_end,
        )
        .select_related(
            "offering__term",
            "offering__course",
            "offering__teaching_class",
        )
        .prefetch_related(
            Prefetch(
                "session_teachers",
                queryset=SessionTeacher.objects.select_related("teacher__user").order_by(
                    "teacher__teacher_no",
                    "teacher_id",
                ),
            )
        )
        .order_by("start_at", "offering__course__course_code", "id")
    )

    contact_resolver = ContactVisibilityResolver(viewer)
    contact_resolver.prime(link.teacher for session in sessions for link in session.session_teachers.all())

    sortable: list[tuple[datetime, dict[str, object]]] = []
    for session in sessions:
        if session.status == SessionStatus.CANCELLED:
            sortable.append(
                (
                    session.start_at,
                    {
                        "kind": ENTRY_CANCELLED_SESSION,
                        "session_id": str(session.id),
                        "start_at": _iso(session.start_at, tzinfo),
                        "end_at": _iso(session.end_at, tzinfo),
                        "start_period": session.start_period,
                        "end_period": session.end_period,
                        "course_name": session.offering.course.name,
                        "teaching_class": session.offering.teaching_class.name,
                        "status": session.status,
                        "status_reason": session.status_reason,
                    },
                )
            )
            continue
        teachers = [
            {
                "id": str(link.teacher_id),
                "name": teacher_name(link.teacher),
                "has_visible_contact": contact_resolver.has_visible_contact(link.teacher),
            }
            for link in session.session_teachers.all()
        ]
        sortable.append(
            (
                session.start_at,
                {
                    "kind": ENTRY_SESSION,
                    "session_id": str(session.id),
                    "start_at": _iso(session.start_at, tzinfo),
                    "end_at": _iso(session.end_at, tzinfo),
                    "start_period": session.start_period,
                    "end_period": session.end_period,
                    "course_name": session.offering.course.name,
                    "teaching_class": session.offering.teaching_class.name,
                    "status": session.status,
                    "is_moved": _is_moved(session, tzinfo),
                    "teachers": teachers,
                },
            )
        )

    # One session can produce several matching snapshots (for example moved
    # away, back and away again).  Only the latest change describes the current
    # result; older rows remain audit history, not duplicate moved-out entries.
    latest_by_session: dict = {}
    changes = (
        SessionChange.objects.filter(
            before_json__classroom_id=str(classroom.id),
            before_json__date=day.isoformat(),
        )
        .select_related(
            "session__offering__term",
            "session__offering__course",
            "session__offering__teaching_class",
            "session__classroom",
        )
        .order_by("session_id", "session_version")
    )
    for change in changes:
        latest_by_session[change.session_id] = change

    for change in latest_by_session.values():
        session = change.session
        current_tz = session.offering.term.tzinfo
        current_day = session.start_at.astimezone(current_tz).date()
        if session.classroom_id == classroom.id and current_day == day:
            # The session is currently here, so it is a live session/cancel row
            # above, never a moved-out one.
            continue
        before = change.before_json
        original_start = datetime.fromisoformat(str(before["start_at"]))
        sortable.append(
            (
                original_start,
                {
                    "kind": ENTRY_MOVED_OUT,
                    "session_id": str(session.id),
                    "start_at": _iso(original_start, tzinfo),
                    "end_at": _iso(datetime.fromisoformat(str(before["end_at"])), tzinfo),
                    "start_period": before["start_period"],
                    "end_period": before["end_period"],
                    "current_start_at": _iso(session.start_at, tzinfo),
                    "current_end_at": _iso(session.end_at, tzinfo),
                    "current_start_period": session.start_period,
                    "current_end_period": session.end_period,
                    "current_classroom_id": str(session.classroom_id),
                    "current_classroom_code": session.classroom.classroom_code,
                    "course_name": session.offering.course.name,
                    "teaching_class": session.offering.teaching_class.name,
                    "status": session.status,
                    "is_moved": True,
                },
            )
        )

    room_blocks = RoomBlock.objects.filter(
        classroom=classroom,
        start_at__lt=day_end,
        end_at__gt=day_start,
    ).order_by("start_at", "id")
    for block in room_blocks:
        sortable.append(
            (
                block.start_at,
                {
                    "kind": ENTRY_ROOM_BLOCK,
                    "room_block_id": str(block.id),
                    "start_at": _iso(block.start_at, tzinfo),
                    "end_at": _iso(block.end_at, tzinfo),
                    "reason": block.reason,
                    "block_code": block.block_code,
                },
            )
        )

    sortable.sort(key=lambda item: (item[0], str(item[1]["kind"]), str(item[1].get("session_id", ""))))
    complete = classroom_day_coverage_complete(
        school_id=classroom.school_id,
        classroom_code=classroom.classroom_code,
        day=day,
    )
    return ClassroomUsage(
        classroom=classroom,
        date=day,
        entries=tuple(payload for _, payload in sortable),
        data_completeness=DATA_COMPLETE if complete else DATA_INCOMPLETE,
    )
