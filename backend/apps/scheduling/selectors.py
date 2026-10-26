"""Read-side selectors shared by the schedule APIs (B13).

Visibility is deliberately defined once here.  A teacher may see a session when
they are one of its actual ``SessionTeacher`` rows, when they are assigned to
the session's offering, or when they are assigned to any offering of the same
teaching class.  ``is_mine`` is narrower and means only the first case.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone as django_timezone

from apps.academics.models import CourseOffering, TeacherProfile, TeachingAssignment, TeachingClass, Term
from apps.scheduling.models import ClassSession, RescheduleRequest, SessionTeacher


def current_school_date() -> date:
    """Return today's date in the configured school timezone."""
    return django_timezone.localdate(timezone=ZoneInfo(settings.TIME_ZONE))


def _active_assignment_filter(day: date) -> Q:
    """Shared exclusive-end predicate for the assignment visibility window."""
    return Q(effective_until__isnull=True) | Q(effective_until__gt=day)


def active_assignments_for_teacher(
    teacher: TeacherProfile,
    *,
    on_date: date | None = None,
) -> QuerySet[TeachingAssignment]:
    """Return assignments still in scope on ``on_date`` (default school today).

    ``effective_until`` is exclusive, so an assignment ending today no longer
    grants visibility.  ``effective_from`` is intentionally not restricted:
    teachers can prepare for a course they are about to start.
    """
    day = on_date or current_school_date()
    return TeachingAssignment.objects.filter(teacher=teacher).filter(_active_assignment_filter(day))


def active_assignments_for_teachers(
    teacher_ids: Iterable[UUID],
    *,
    on_date: date | None = None,
) -> QuerySet[TeachingAssignment]:
    """Return active assignments for several teachers in one query.

    Contact visibility uses this batched form so a classroom history page does
    not re-query the same assignment window once per teacher.
    """
    day = on_date or current_school_date()
    return TeachingAssignment.objects.filter(teacher_id__in=teacher_ids).filter(_active_assignment_filter(day))


def visible_sessions_for_teacher(teacher: TeacherProfile) -> QuerySet[ClassSession]:
    """Return every session the active teacher is allowed to read.

    The queryset is annotated with ``is_mine``.  All schedule endpoints use
    this function (or a read-optimised wrapper below), so the role matrix does
    not get reimplemented in individual views.
    """
    active_assignments = active_assignments_for_teacher(teacher)
    assigned_offering_ids = active_assignments.values("offering_id")
    visible_class_ids = CourseOffering.objects.filter(
        id__in=assigned_offering_ids,
    ).values("teaching_class_id")
    is_mine = SessionTeacher.objects.filter(session=OuterRef("pk"), teacher=teacher)

    return (
        ClassSession.objects.filter(
            Q(session_teachers__teacher=teacher)
            | Q(offering_id__in=assigned_offering_ids)
            | Q(offering__teaching_class_id__in=visible_class_ids)
        )
        .annotate(is_mine=Exists(is_mine))
        .distinct()
    )


def can_reschedule_session(teacher: TeacherProfile, session: ClassSession) -> bool:
    """Return whether ``teacher`` may request a move for ``session``.

    This is the same permission used by B30 candidate queries and B31 request
    creation: an actual session teacher or an active assignment to the session's
    offering.  A teacher merely sharing the teaching class may view related
    requests but cannot create one for this session.
    """
    if SessionTeacher.objects.filter(session=session, teacher=teacher).exists():
        return True
    return active_assignments_for_teacher(teacher).filter(offering_id=session.offering_id).exists()


def visible_reschedule_requests(teacher: TeacherProfile, *, scope: str = "mine") -> QuerySet[RescheduleRequest]:
    """Return requests visible to a teacher for the ``mine``/``related`` scopes.

    ``related`` uses the same session visibility as the schedule APIs, which
    includes active teachers of any offering in the same teaching class.
    """
    queryset = RescheduleRequest.objects.select_related(
        "session__offering__course",
        "session__offering__term",
        "session__classroom",
        "target_classroom",
        "applicant",
        "decision_by",
    )
    if scope == "mine":
        return queryset.filter(applicant=teacher)
    return queryset.filter(session__in=visible_sessions_for_teacher(teacher))


def visible_sessions_for_read(teacher: TeacherProfile) -> QuerySet[ClassSession]:
    """Return visible sessions with all relations needed by the API serializers."""
    return (
        visible_sessions_for_teacher(teacher)
        .select_related(
            "offering__term",
            "offering__course",
            "offering__teaching_class",
            "offering__lead_teacher",
            "classroom",
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
    )


def visible_offerings_for_teacher(teacher: TeacherProfile) -> QuerySet[CourseOffering]:
    """Return offerings the teacher may read, annotated with ``is_assigned``.

    This includes the teacher's own offerings and every other offering in a
    teaching class where the teacher has an active assignment.  ``is_assigned``
    uses the same active-assignment window as visibility.
    """
    active_assignments = active_assignments_for_teacher(teacher)
    assigned_offering_ids = active_assignments.values("offering_id")
    visible_class_ids = CourseOffering.objects.filter(
        id__in=assigned_offering_ids,
    ).values("teaching_class_id")
    is_assigned = active_assignments.filter(offering=OuterRef("pk"))

    return (
        CourseOffering.objects.filter(Q(id__in=assigned_offering_ids) | Q(teaching_class_id__in=visible_class_ids))
        .annotate(is_assigned=Exists(is_assigned))
        .distinct()
    )


def visible_offerings_for_read(teacher: TeacherProfile) -> QuerySet[CourseOffering]:
    """Return visible offerings with the relations used by the list serializer."""
    return (
        visible_offerings_for_teacher(teacher)
        .select_related(
            "course",
            "teaching_class",
            "lead_teacher",
            "term",
        )
        .prefetch_related(
            Prefetch(
                "teaching_assignments",
                queryset=TeachingAssignment.objects.select_related("teacher").order_by(
                    "teacher__teacher_no",
                    "teacher_id",
                ),
            )
        )
    )


def visible_teaching_classes_for_teacher(teacher: TeacherProfile) -> QuerySet[TeachingClass]:
    """Return teaching classes in which the teacher has an active assignment."""
    active_offerings = active_assignments_for_teacher(teacher).values("offering_id")
    return TeachingClass.objects.filter(offerings__id__in=active_offerings).distinct()


def current_term_for_school(school_id: UUID) -> Term | None:
    """Return the first current term for a school, using each term's timezone.

    Terms normally share one school timezone; evaluating each row still keeps
    the boundary correct during the short overlap that can occur while a new
    term is being configured.
    """
    terms = Term.objects.filter(school_id=school_id).order_by("-start_date", "-code")
    for term in terms:
        today = django_timezone.localdate(timezone=term.tzinfo)
        if term.start_date <= today <= term.end_date:
            return term
    return None


def term_is_current(term: Term) -> bool:
    today = django_timezone.localdate(timezone=term.tzinfo)
    return term.start_date <= today <= term.end_date


def session_local_date(session: ClassSession) -> date:
    """Return the session's date in its term's school timezone."""
    return django_timezone.localtime(session.start_at, session.offering.term.tzinfo).date()
