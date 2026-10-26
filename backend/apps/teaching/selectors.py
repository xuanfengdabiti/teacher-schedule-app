"""Read permissions and queries shared by note APIs and offline sync.

B20 deliberately does not reuse the broad B13 teaching-class visibility for
note content.  A teacher may read shared notes only when they actually teach the
session (``SessionTeacher``) or have a current assignment to its offering.  A
teacher who merely teaches another course in the same teaching class can still
see the session in the timetable, but not its notes (domain-design 5.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet

from apps.academics.models import CourseOffering, TeacherProfile, TeachingAssignment, Term
from apps.scheduling.models import ClassSession, SessionStatus, SessionTeacher
from apps.scheduling.selectors import active_assignments_for_teacher
from apps.teaching.exceptions import (
    not_offering_teacher,
    not_session_teacher,
    progress_not_found,
    progress_not_offering_teacher,
    session_not_found,
)
from apps.teaching.models import TeachingNote


@dataclass(frozen=True, slots=True)
class NoteAccess:
    is_session_teacher: bool
    is_offering_teacher: bool
    visible: bool

    @property
    def can_read(self) -> bool:
        return self.is_session_teacher or self.is_offering_teacher


def get_session_for_notes(session_id: UUID) -> ClassSession:
    """Load a session or raise the stable 404 shape used by note endpoints."""
    session = (
        ClassSession.objects.select_related(
            "offering__term",
            "offering__course",
            "offering__teaching_class",
            "classroom",
        )
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise session_not_found()
    return session


def note_access_for_session(teacher: TeacherProfile, session: ClassSession) -> NoteAccess:
    """Resolve the three B20 scopes without widening to the teaching class.

    * session teacher: read + write own note;
    * active offering teacher: read published notes;
    * active same-class/other visible teacher: session visible, notes forbidden;
    * everyone else: invisible (404, never leak existence).
    """
    is_session_teacher = SessionTeacher.objects.filter(session=session, teacher=teacher).exists()
    if is_session_teacher:
        return NoteAccess(is_session_teacher=True, is_offering_teacher=True, visible=True)

    active = active_assignments_for_teacher(teacher)
    is_offering_teacher = active.filter(offering_id=session.offering_id).exists()
    if is_offering_teacher:
        return NoteAccess(is_session_teacher=False, is_offering_teacher=True, visible=True)

    visible = active.filter(offering__teaching_class_id=session.offering.teaching_class_id).exists()
    return NoteAccess(
        is_session_teacher=False,
        is_offering_teacher=False,
        visible=visible,
    )


def require_note_read_access(teacher: TeacherProfile, session: ClassSession) -> NoteAccess:
    access = note_access_for_session(teacher, session)
    if access.can_read:
        return access
    if access.visible:
        raise not_offering_teacher()
    raise session_not_found()


def require_note_write_access(teacher: TeacherProfile, session: ClassSession) -> NoteAccess:
    access = note_access_for_session(teacher, session)
    if access.is_session_teacher:
        return access
    if access.visible:
        raise not_session_teacher()
    raise session_not_found()


def session_notes(session: ClassSession) -> list[TeachingNote]:
    """Return all notes for one session in one fixed query.

    Other authors' draft columns stay in the database result but are filtered
    out before serialization.  Loading all rows once keeps the endpoint query
    count constant and avoids a second per-author query.
    """
    return list(
        TeachingNote.objects.filter(session=session)
        .select_related("author", "session__offering__term")
        .order_by("author__teacher_no", "author_id")
    )


def previous_completed_session(session: ClassSession) -> ClassSession | None:
    """Return the latest earlier completed session with any published note.

    Ordering is by the session's actual ``start_at``, not by note publication
    time.  This is why back-filling an older record cannot replace the latest
    class in A10.  Current/later and non-completed sessions are excluded.
    """
    published_exists = TeachingNote.objects.filter(
        session=OuterRef("pk"),
        published_at__isnull=False,
    )
    return (
        ClassSession.objects.filter(
            offering_id=session.offering_id,
            status=SessionStatus.COMPLETED,
            start_at__lt=session.start_at,
        )
        .annotate(has_published_note=Exists(published_exists))
        .filter(has_published_note=True)
        .select_related("offering__course", "offering__term")
        .order_by("-start_at", "-id")
        .first()
    )


def published_notes_for_session(session: ClassSession) -> list[TeachingNote]:
    return list(
        TeachingNote.objects.filter(session=session, published_at__isnull=False)
        .select_related("author", "session__offering__term")
        .order_by("author__teacher_no", "author_id")
    )


def readable_notes_queryset(teacher: TeacherProfile) -> QuerySet[TeachingNote]:
    """Own notes plus shared published notes the teacher may currently read."""
    active_offering_ids = active_assignments_for_teacher(teacher).values("offering_id")
    shared = Q(published_at__isnull=False) & (
        Q(session__session_teachers__teacher=teacher) | Q(session__offering_id__in=active_offering_ids)
    )
    return TeachingNote.objects.filter(Q(author=teacher) | shared).distinct()


def readable_notes_for_term(teacher: TeacherProfile, term: Term) -> list[TeachingNote]:
    """Bootstrap query: own drafts and readable snapshots for one term.

    ``select_related`` keeps serialization independent of note count; the query
    count is constant for any number of notes (B20 sync requirement).
    """
    return list(
        readable_notes_queryset(teacher)
        .filter(session__offering__term=term)
        .select_related("author", "session__offering__course", "session__offering__term")
        .order_by("session__start_at", "author__teacher_no", "id")
    )


@dataclass(frozen=True, slots=True)
class OfferingProgressAccess:
    """Visibility for plans/progress, deliberately narrower than B13."""

    is_offering_teacher: bool
    is_session_teacher: bool
    visible: bool

    @property
    def can_read(self) -> bool:
        return self.is_offering_teacher or self.is_session_teacher


def get_offering_for_progress(offering_id: UUID) -> CourseOffering:
    offering = (
        CourseOffering.objects.select_related(
            "term",
            "course",
            "teaching_class",
            "lead_teacher",
        )
        .filter(pk=offering_id)
        .first()
    )
    if offering is None:
        raise progress_not_found("课程开设不存在或不在本人可见范围。")
    return offering


def get_session_for_completion(session_id: UUID) -> ClassSession:
    session = (
        ClassSession.objects.select_related(
            "offering__term",
            "offering__course",
            "offering__teaching_class",
            "offering__lead_teacher",
            "classroom",
        )
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise progress_not_found("课次不存在或不在本人可见范围。")
    return session


def offering_progress_access(teacher: TeacherProfile, offering: CourseOffering) -> OfferingProgressAccess:
    """Resolve plan/completion visibility without sharing by teaching class.

    Read permission comes from a current assignment to the offering or from any
    ``SessionTeacher`` row in that offering.  A teacher who only teaches another
    course in the same teaching class may still see the timetable, but this
    endpoint returns 403 instead of leaking plan content.
    """
    is_offering_teacher = active_assignments_for_teacher(teacher).filter(offering_id=offering.id).exists()
    is_session_teacher = SessionTeacher.objects.filter(
        teacher=teacher,
        session__offering_id=offering.id,
    ).exists()
    if is_offering_teacher or is_session_teacher:
        return OfferingProgressAccess(
            is_offering_teacher=is_offering_teacher,
            is_session_teacher=is_session_teacher,
            visible=True,
        )

    visible = (
        active_assignments_for_teacher(teacher).filter(offering__teaching_class_id=offering.teaching_class_id).exists()
    )
    return OfferingProgressAccess(
        is_offering_teacher=False,
        is_session_teacher=False,
        visible=visible,
    )


def require_offering_read_access(teacher: TeacherProfile, offering: CourseOffering) -> OfferingProgressAccess:
    access = offering_progress_access(teacher, offering)
    if access.can_read:
        return access
    if access.visible:
        raise progress_not_offering_teacher()
    raise progress_not_found("课程开设不存在或不在本人可见范围。")


def is_session_teacher_for(teacher: TeacherProfile, session: ClassSession) -> bool:
    return SessionTeacher.objects.filter(session=session, teacher=teacher).exists()


def active_offering_teachers(
    offering: CourseOffering,
    *,
    on_date: date,
) -> list[TeacherProfile]:
    """Current effective offering teachers, deduplicated in one query.

    This intentionally follows the established visibility convention:
    ``effective_until`` is exclusive and ``effective_from`` being in the future
    does not remove the teacher from the course scope.
    """
    assignments = (
        TeachingAssignment.objects.filter(offering=offering)
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gt=on_date))
        .select_related("teacher")
        .order_by("teacher__teacher_no", "teacher_id")
    )
    teachers: list[TeacherProfile] = []
    seen: set[UUID] = set()
    for assignment in assignments:
        if assignment.teacher_id not in seen:
            seen.add(assignment.teacher_id)
            teachers.append(assignment.teacher)
    return teachers
