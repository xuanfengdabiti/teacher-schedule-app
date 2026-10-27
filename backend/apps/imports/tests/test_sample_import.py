"""Sample acceptance and repeated-import tests (import-spec section 6, A17)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.conf import settings
from django.db.models import Count
from django.utils import timezone as django_timezone

from apps.academics.models import Enrollment, Student, TeacherProfile, Term
from apps.imports.models import ImportIdentity, ImportStatus
from apps.imports.services import commit_batch, preview_batch
from apps.imports.tests.conftest import PREVIEW_NOW, make_batch
from apps.scheduling.models import ChangeLog, ClassSession, SessionStatus

pytestmark = pytest.mark.django_db


def _local_date(value, term: Term) -> date:
    return django_timezone.localtime(value, term.tzinfo).date()


class TestSampleAcceptance:
    def test_preview_reports_expected_objects_and_sessions(self, school, academic_staff) -> None:
        batch = make_batch(school)

        result = preview_batch(batch, now=PREVIEW_NOW)

        assert not result.has_errors, result.errors
        assert result.summary["new_counts"]["teacher"] == 2
        assert result.summary["new_counts"]["student"] == 3
        assert result.summary["new_counts"]["classroom"] == 3
        assert result.summary["new_counts"]["offering"] == 2
        assert result.summary["new_counts"]["rule"] == 3
        assert result.expected_new_sessions == 40
        assert result.estimated_hours_by_offering == {"OFF-CRIME-2026A": "4.0"}
        assert result.row_counts["terms.csv"]["new"] == 1
        assert result.row_counts["sessions.csv"]["new"] == 3
        assert batch.status == ImportStatus.PREVIEWED
        assert len(batch.file_hash) == 64
        assert len(batch.file_names) == 12
        assert (settings.MEDIA_ROOT / "imports" / str(batch.pk) / "terms.csv").is_file()
        # Preview is still read-only.
        assert not Term.objects.exists()
        assert not ClassSession.objects.exists()

    def test_commit_creates_exactly_forty_sessions_and_completion(self, school, academic_staff) -> None:
        batch = make_batch(school)
        preview_batch(batch, now=PREVIEW_NOW)

        result = commit_batch(batch, academic_staff)

        assert result.committed
        batch.refresh_from_db()
        assert batch.status == ImportStatus.COMMITTED
        assert batch.committed_by == academic_staff
        assert batch.committed_at is not None
        assert batch.term_id is not None
        assert Term.objects.count() == 1
        term = Term.objects.get(code="2026-autumn")
        assert term.schedule_version == 2
        assert TeacherProfile.objects.count() == 2
        assert Student.objects.count() == 3
        assert Enrollment.objects.count() == 4
        assert ClassSession.objects.count() == 40
        assert TeacherProfile.objects.filter(user__isnull=False).count() == 0

        identity_counts = dict(
            ImportIdentity.objects.values("entity_type")
            .order_by()
            .annotate(total=Count("id"))
            .values_list("entity_type", "total")
        )
        assert identity_counts == {
            "term": 1,
            "period": 8,
            "teacher": 2,
            "student": 3,
            "classroom": 3,
            "course": 2,
            "teaching_class": 1,
            "offering": 2,
            "enrollment": 4,
            "calendar_exception": 2,
            "rule": 3,
            "session": 3,
            "room_block": 1,
            "person_block": 2,
            "coverage": 8,
        }

        sessions = ClassSession.objects.filter(offering__term=term).prefetch_related("teachers")
        assert sessions.count() == 40
        assert sessions.filter(status=SessionStatus.COMPLETED).count() == 2
        assert sessions.filter(status=SessionStatus.SCHEDULED).count() == 38
        completed = sessions.filter(status=SessionStatus.COMPLETED)
        assert sum((session.actual_hours for session in completed), Decimal("0")) == Decimal("4.0")
        assert all(session.recorded_by_user_id == academic_staff.pk for session in completed)

        moved = sessions.filter(origin_teaching_date=date(2026, 10, 5))
        assert moved.count() == 2
        for session in moved:
            assert _local_date(session.start_at, term) == date(2026, 10, 10)
            assert session.origin_teaching_date == date(2026, 10, 5)

        data_w07 = sessions.get(offering__offering_code="OFF-DATA-2026A", origin_teaching_date=date(2026, 10, 19))
        assert data_w07.status == SessionStatus.SCHEDULED
        assert data_w07.actual_hours is None

        # Three sessions rows bind to three already-generated sessions; no 41st.
        session_identities = ImportIdentity.objects.filter(
            source_system="demo_csv_v1",
            entity_type="session",
        )
        assert session_identities.count() == 3
        assert set(session_identities.values_list("object_id", flat=True)) <= set(sessions.values_list("id", flat=True))

        session_logs = ChangeLog.objects.filter(entity_type="session")
        # B12c creates the explicit sessions rows with their final state in one
        # write, so the two completed rows no longer produce a second log entry.
        assert session_logs.count() == 40
        assert ChangeLog.objects.filter(entity_type="term").count() == 1
        assert set(session_logs.values_list("entity_id", flat=True)) >= set(completed.values_list("id", flat=True))

    def test_repeated_import_is_all_unchanged_and_writes_nothing(self, school, academic_staff) -> None:
        first_batch = make_batch(school)
        preview_batch(first_batch, now=PREVIEW_NOW)
        commit_batch(first_batch, academic_staff)

        term = Term.objects.get(code="2026-autumn")
        session_ids = set(ClassSession.objects.values_list("id", flat=True))
        log_count = ChangeLog.objects.count()
        identity_count = ImportIdentity.objects.count()
        version = term.schedule_version

        second_batch = make_batch(school)
        result = preview_batch(second_batch, now=PREVIEW_NOW)

        assert not result.has_errors, result.errors
        assert result.expected_new_sessions == 0
        assert result.estimated_hours_by_offering == {}
        assert result.summary["new_counts"] == {}
        for counts in result.row_counts.values():
            assert counts["new"] == 0
            assert counts["error"] == 0
        assert any(counts["unchanged"] > 0 for counts in result.row_counts.values())

        commit = commit_batch(second_batch, academic_staff)

        assert commit.committed
        assert commit.sessions_generated == 0
        assert commit.sessions_changed == 0
        assert ClassSession.objects.count() == 40
        assert set(ClassSession.objects.values_list("id", flat=True)) == session_ids
        assert ChangeLog.objects.count() == log_count
        assert ImportIdentity.objects.count() == identity_count
        term.refresh_from_db()
        assert term.schedule_version == version
        completed = ClassSession.objects.filter(status=SessionStatus.COMPLETED)
        assert sum((session.actual_hours for session in completed), Decimal("0")) == Decimal("4.0")
        second_batch.refresh_from_db()
        assert second_batch.status == ImportStatus.COMMITTED
