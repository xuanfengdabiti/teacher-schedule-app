"""Selector tests for date-effective enrolment (B11 acceptance item 7)."""

from __future__ import annotations

from datetime import date

import pytest

from apps.academics.models import Enrollment
from apps.academics.selectors import active_student_ids

pytestmark = pytest.mark.django_db


class TestActiveStudentIds:
    def test_half_open_range_includes_from_and_excludes_until(self, demo_term) -> None:
        offering = demo_term.offerings["OFF-CRIME-2026A"]
        extra = demo_term.students["000003"]
        Enrollment.objects.create(
            offering=offering,
            student=extra,
            effective_from=date(2026, 9, 7),
            effective_until=date(2026, 10, 19),
        )

        assert extra.id in active_student_ids(offering, date(2026, 9, 7))
        assert extra.id in active_student_ids(offering, date(2026, 10, 18))
        assert extra.id not in active_student_ids(offering, date(2026, 10, 19))
        assert extra.id not in active_student_ids(offering, date(2026, 9, 6))

    def test_registered_students_and_offering_scope(self, demo_term) -> None:
        offering = demo_term.offerings["OFF-CRIME-2026A"]
        ids = active_student_ids(offering, date(2026, 10, 26))

        assert ids == {demo_term.students["000001"].id, demo_term.students["000002"].id}
        # A student of the other offering must not leak into this list.
        assert demo_term.students["000003"].id not in ids

    def test_null_until_stays_active(self, demo_term) -> None:
        offering = demo_term.offerings["OFF-CRIME-2026A"]
        extra = demo_term.students["000003"]
        Enrollment.objects.create(
            offering=offering,
            student=extra,
            effective_from=date(2026, 10, 19),
            effective_until=None,
        )

        assert extra.id in active_student_ids(offering, date(2027, 1, 24))
        assert extra.id not in active_student_ids(offering, date(2026, 10, 18))

    def test_model_classmethod_delegates_to_selector(self, demo_term) -> None:
        offering = demo_term.offerings["OFF-DATA-2026A"]

        assert Enrollment.active_student_ids(offering, date(2026, 9, 7)) == active_student_ids(
            offering,
            date(2026, 9, 7),
        )
