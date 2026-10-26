"""Read-side queries for academic reference data."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from django.db.models import Q

from apps.academics.models import CourseOffering, Enrollment, TeachingAssignment


def assigned_teacher_ids(offering: CourseOffering) -> set[UUID]:
    """Return the teacher ids recorded as the offering's assignment scope.

    A rule's actual teachers must be a subset of this set; the roster scope is
    maintained by ``TeachingAssignment`` (import-spec 3.5).
    """
    return set(TeachingAssignment.objects.filter(offering=offering).values_list("teacher_id", flat=True))


def active_student_ids(offering: CourseOffering, on_date: date) -> set[UUID]:
    """Return the students enrolled in ``offering`` on ``on_date``.

    Enrolment ranges follow the project-wide ``[effective_from, effective_until)``
    convention: the start date is included, the end date is not.  A null end
    date means "still effective".
    """
    return set(
        Enrollment.objects.filter(
            offering=offering,
            effective_from__lte=on_date,
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gt=on_date))
        .values_list("student_id", flat=True)
    )
