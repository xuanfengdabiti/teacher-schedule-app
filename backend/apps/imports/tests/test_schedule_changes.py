"""B12c merge changes that move, cancel, restore or replace sessions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import (
    CourseOffering,
    Enrollment,
    Period,
    School,
    TeacherProfile,
    TeacherStatus,
    TeachingAssignment,
    Term,
)
from apps.classrooms.models import Classroom
from apps.imports.models import ImportBatch, ImportDecision, ImportIdentity, ImportStatus
from apps.imports.services import ImportCommitError, commit_batch, preview_batch
from apps.imports.tests.conftest import PREVIEW_NOW, copy_templates, make_batch, read_csv, write_csv
from apps.notifications.models import Notification, NotificationType
from apps.scheduling.changes import apply_session_change
from apps.scheduling.models import (
    ChangeLog,
    ClassSession,
    SessionChange,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.requests import create_request
from apps.scheduling.reschedule_options import (
    ImportEvaluationRequest,
    ImportOccupancyTarget,
    evaluate_import_targets,
)
from apps.scheduling.sync import build_bootstrap_payload, build_changes_payload
from apps.teaching.models import TeachingNote

pytestmark = pytest.mark.django_db

SCHOOL_DATE = date(2026, 10, 27)
LATE_NOW = datetime(2027, 2, 1, 8, 0, tzinfo=PREVIEW_NOW.tzinfo)
TERM_CODE = "2026-autumn"
CRIME = "OFF-CRIME-2026A"
DATA = "OFF-DATA-2026A"


@pytest.fixture(autouse=True)
def fixed_school_date(monkeypatch):
    monkeypatch.setattr("apps.scheduling.selectors.current_school_date", lambda: SCHOOL_DATE)


def _import_all(school, academic_staff) -> ImportBatch:
    batch = make_batch(school)
    preview = preview_batch(batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert not preview.has_errors, preview.errors
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    return batch


def _copy_files(tmp_path: Path) -> dict[str, Path]:
    return {path.name: path for path in copy_templates(tmp_path)}


def _all_periods_start_at_one(files: dict[str, Path]) -> None:
    rows = read_csv(files["rules.csv"])
    for row in rows[1:]:
        row[4] = "1"
        row[5] = "2"
    write_csv(files["rules.csv"], rows)


def _expand_rules(files: dict[str, Path], copies: int = 4) -> None:
    rows = read_csv(files["rules.csv"])
    originals = rows[1:]
    for index in range(1, copies + 1):
        for row in originals:
            copied = row[:]
            copied[0] = f"{row[0]}-C{index}"
            rows.append(copied)
    write_csv(files["rules.csv"], rows)


def _rules_only(files: dict[str, Path]) -> list[Path]:
    names = (
        "terms.csv",
        "periods.csv",
        "teachers.csv",
        "classrooms.csv",
        "offerings.csv",
        "enrollments.csv",
        "calendar_exceptions.csv",
        "rules.csv",
        "coverage.csv",
    )
    return [files[name] for name in names]


def _core_files(files: dict[str, Path]) -> list[Path]:
    names = (
        "terms.csv",
        "periods.csv",
        "teachers.csv",
        "classrooms.csv",
        "offerings.csv",
        "enrollments.csv",
        "calendar_exceptions.csv",
        "rules.csv",
        "sessions.csv",
        "coverage.csv",
    )
    return [files[name] for name in names]


def _preview_files(school, files: list[Path], *, now: datetime = PREVIEW_NOW):
    batch = make_batch(school, files=files)
    result = preview_batch(batch, now=now, school_date=SCHOOL_DATE)
    return batch, result


def _decide(
    batch: ImportBatch,
    staff,
    *,
    choice: str = "keep_existing",
    note: str = "",
    session_id=None,
    only_decided: bool = False,
) -> None:
    for decision in batch.decisions.all():
        if session_id is not None and decision.session_id != session_id:
            continue
        if only_decided and decision.choice:
            continue
        decision.choice = choice
        decision.note = note
        decision.decided_by = staff
        decision.decided_at = PREVIEW_NOW
        decision.save(update_fields=["choice", "note", "decided_by", "decided_at", "updated_at"])


def _session(rule_code: str, origin: date) -> ClassSession:
    return ClassSession.objects.get(rule__rule_code=rule_code, origin_teaching_date=origin)


def _evaluation_request(
    session: ClassSession,
    *,
    day: date,
    start_period: int,
    end_period: int,
    classroom: Classroom,
    teacher_ids=None,
) -> ImportEvaluationRequest:
    term = session.offering.term
    periods = {period.period_number: period for period in Period.objects.filter(term=term)}
    start_slot = periods[start_period]
    end_slot = periods[end_period]
    return ImportEvaluationRequest(
        session=session,
        day=day,
        start_period=start_period,
        end_period=end_period,
        classroom=classroom,
        start_at=datetime.combine(day, start_slot.start_time, tzinfo=term.tzinfo),
        end_at=datetime.combine(day, end_slot.end_time, tzinfo=term.tzinfo),
        teacher_ids=teacher_ids,
    )


def _link_user(teacher: TeacherProfile):
    user = get_user_model().objects.create_user(
        email=f"teacher-{teacher.teacher_no}@example.invalid",
        password="test-pass-123",
    )
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user


def _client_for(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def test_week_removal_cancels_future_session_and_repeat_is_unchanged(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    target = _session("R-CRIME-EVEN", date(2027, 1, 4))
    target_id = target.id
    teacher_two = TeacherProfile.objects.get(teacher_no="0002")

    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    even = next(row for row in rows if row[0] == "R-CRIME-EVEN")
    even[6] = "|".join(str(week) for week in range(2, 21, 2) if week != 18)
    write_csv(files["rules.csv"], rows)
    batch, result = _preview_files(school, [files["rules.csv"]])

    assert not result.has_errors, result.errors
    assert result.summary["session_target_counts"]["auto_cancelled"] == 1
    assert not batch.decisions.exists()
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    assert commit.sessions_cancelled == 1

    target.refresh_from_db()
    assert target.id == target_id
    assert target.status == SessionStatus.CANCELLED
    assert target.actual_hours == Decimal("0")
    assert SessionChange.objects.filter(session=target).count() == 1
    assert SessionChange.objects.get(session=target).source == "import"
    assert ChangeLog.objects.filter(entity_type="session", entity_id=target_id).exists()
    assert (
        Notification.objects.filter(
            recipient=teacher_two,
            event_key=f"import:{batch.pk}:{teacher_two.id}",
        ).count()
        == 1
    )
    notification = Notification.objects.get(
        recipient=teacher_two,
        event_key=f"import:{batch.pk}:{teacher_two.id}",
    )
    assert "取消 1 节" in notification.body

    second_batch, second = _preview_files(school, [files["rules.csv"]])
    assert second.summary["session_target_counts"]["unchanged"] == 40
    assert not second_batch.decisions.exists()
    log_count = ChangeLog.objects.count()
    second_commit = commit_batch(second_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert second_commit.committed
    assert second_commit.sessions_cancelled == 0
    assert ChangeLog.objects.count() == log_count
    target.refresh_from_db()
    assert target.status == SessionStatus.CANCELLED


def test_weekday_change_moves_future_sessions_and_keeps_saved_origin(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    past = _session("R-DATA-MON", date(2026, 10, 26))
    noted = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    TeachingNote.objects.create(session=noted, author=teacher_one, draft_covered="已有教学备注")

    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[3] = "3"
    write_csv(files["rules.csv"], rows)
    batch, result = _preview_files(school, [files["rules.csv"]])

    assert not result.has_errors, result.errors
    assert result.summary["session_target_counts"]["kept_past"] >= 1
    assert result.summary["session_target_counts"]["manual"] == 1
    decision = batch.decisions.get()
    assert decision.session_id == noted.id
    assert decision.kind == "touched_change"
    assert "教学备注" in decision.reason
    _decide(batch, academic_staff, choice="keep_existing")
    assert commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE).committed

    past.refresh_from_db()
    noted.refresh_from_db()
    moved = _session("R-DATA-MON", date(2026, 11, 9))
    assert past.start_at.astimezone(past.offering.term.tzinfo).date() == date(2026, 10, 26)
    assert noted.start_at.astimezone(noted.offering.term.tzinfo).date() == date(2026, 11, 2)
    assert moved.start_at.astimezone(moved.offering.term.tzinfo).date() == date(2026, 11, 11)
    assert moved.origin_teaching_date == date(2026, 11, 9)
    assert moved.origin_period == 3
    rule = moved.rule
    assert rule is not None
    assert rule.weekday == 3

    before_repeat = {
        session.id: (
            session.origin_teaching_date,
            session.origin_period,
            session.start_at,
            session.status,
        )
        for session in ClassSession.objects.all()
    }
    repeat_batch, repeat = _preview_files(school, [files["rules.csv"]])
    assert not repeat.has_errors, repeat.errors
    assert repeat.summary["session_target_counts"] == {
        "new": 0,
        "unchanged": 31,
        "auto_updated": 0,
        "auto_cancelled": 0,
        "auto_restored": 0,
        "manual": 1,
        "conflict": 0,
        "completed_locked": 0,
        "kept_past": 8,
        "kept_completed": 0,
    }
    assert repeat_batch.decisions.count() == 1
    assert repeat_batch.decisions.get().session_id == noted.id
    _decide(repeat_batch, academic_staff, choice="keep_existing")
    repeat_commit = commit_batch(repeat_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert repeat_commit.committed
    assert repeat_commit.sessions_new == 0
    assert repeat_commit.sessions_cancelled == 0
    assert repeat_commit.sessions_restored == 0
    after_repeat = {
        session.id: (
            session.origin_teaching_date,
            session.origin_period,
            session.start_at,
            session.status,
        )
        for session in ClassSession.objects.all()
    }
    assert after_repeat == before_repeat


def test_new_cancel_exception_then_exception_date_change_restores(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    data = _session("R-DATA-MON", date(2026, 11, 2))

    files = _copy_files(tmp_path)
    rows = read_csv(files["calendar_exceptions.csv"])
    rows.append(["CAL-20261102", TERM_CODE, "2026-11-02", "cancel", "", "新增停课"])
    write_csv(files["calendar_exceptions.csv"], rows)
    batch, result = _preview_files(school, [files["calendar_exceptions.csv"]])

    assert not result.has_errors, result.errors
    assert result.summary["session_target_counts"]["auto_cancelled"] == 2
    assert commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE).committed
    crime.refresh_from_db()
    data.refresh_from_db()
    assert crime.status == SessionStatus.CANCELLED
    assert data.status == SessionStatus.CANCELLED

    # merge never deletes a row: moving the exception to the corrected date is
    # the visible way to withdraw the stop.  11-02 is restored and 11-09 (week
    # 10) is stopped instead.
    rows = read_csv(files["calendar_exceptions.csv"])
    new_row = next(row for row in rows if row[0] == "CAL-20261102")
    new_row[2] = "2026-11-09"
    write_csv(files["calendar_exceptions.csv"], rows)
    move_batch, move_result = _preview_files(school, [files["calendar_exceptions.csv"]])

    assert not move_result.has_errors, move_result.errors
    assert move_result.summary["session_target_counts"]["auto_restored"] == 2
    assert move_result.summary["session_target_counts"]["auto_cancelled"] == 2
    assert commit_batch(move_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE).committed
    crime.refresh_from_db()
    data.refresh_from_db()
    assert crime.status == SessionStatus.SCHEDULED
    assert data.status == SessionStatus.SCHEDULED
    assert _session("R-CRIME-EVEN", date(2026, 11, 9)).status == SessionStatus.CANCELLED
    assert _session("R-DATA-MON", date(2026, 11, 9)).status == SessionStatus.CANCELLED


def test_period_start_time_change_updates_future_but_locks_completed(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    completed = _session("R-CRIME-ODD", date(2026, 10, 19))
    future = _session("R-CRIME-ODD", date(2026, 11, 2))
    completed_start = completed.start_at

    files = _copy_files(tmp_path)
    rows = read_csv(files["periods.csv"])
    rows[1][2] = "08:10"
    write_csv(files["periods.csv"], rows)
    batch, result = _preview_files(school, [files["periods.csv"]])

    assert not result.has_errors, result.errors
    assert result.summary["session_target_counts"]["completed_locked"] == 2
    assert result.summary["session_target_counts"]["auto_updated"] > 0
    for decision in batch.decisions.all():
        assert decision.kind == "completed_locked"
        assert decision.options == ["keep_existing"]
    _decide(batch, academic_staff, choice="keep_existing")
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    assert commit.decisions_kept == 2

    completed.refresh_from_db()
    future.refresh_from_db()
    assert completed.start_at == completed_start
    assert future.start_at.astimezone(future.offering.term.tzinfo).strftime("%H:%M") == "08:10"


def test_sessions_row_change_requires_change_ref_and_records_it(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    target = _session("R-DATA-MON", date(2026, 10, 19))

    files = _copy_files(tmp_path)
    rows = read_csv(files["sessions.csv"])
    data_row = next(row for row in rows if row[0] == "S-DATA-W07")
    data_row[11] = "B201"
    data_row[19] = ""
    write_csv(files["sessions.csv"], rows)
    missing_batch, missing = _preview_files(school, [files["sessions.csv"]])

    assert missing.has_errors
    assert any(issue.code == "CHANGE_REF_REQUIRED" for issue in missing.issues)
    with pytest.raises(ImportCommitError):
        commit_batch(missing_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)

    change_files = _copy_files(tmp_path / "change")
    rows = read_csv(change_files["sessions.csv"])
    data_row = next(row for row in rows if row[0] == "S-DATA-W07")
    data_row[11] = "B201"
    data_row[19] = "SCHOOL-ADJ-001"
    write_csv(change_files["sessions.csv"], rows)
    batch, result = _preview_files(school, [change_files["sessions.csv"]])

    assert not result.has_errors, result.errors
    assert result.summary["session_target_counts"]["auto_updated"] == 1
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    target.refresh_from_db()
    assert target.classroom.classroom_code == "B201"
    change = SessionChange.objects.get(session=target)
    assert change.approval_ref == "SCHOOL-ADJ-001"
    assert str(batch.pk) in change.reason
    assert "SCHOOL-ADJ-001" in change.reason


def _conflict_batch(school, tmp_path):
    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[4] = "1"
    data_row[5] = "2"
    write_csv(files["rules.csv"], rows)
    return _preview_files(school, [files["rules.csv"]])


def test_conflict_becomes_decision_even_when_untouched_and_keep_does_nothing(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    target = _session("R-DATA-MON", date(2026, 11, 2))
    original_start = target.start_at

    batch, result = _conflict_batch(school, tmp_path)

    assert not result.has_errors, result.errors
    assert result.summary["session_target_counts"]["conflict"] >= 1
    decision = batch.decisions.get(session=target)
    assert decision.kind == "conflict"
    assert "冲突" in decision.reason
    _decide(batch, academic_staff, choice="keep_existing")
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    assert commit.decisions_kept == batch.decisions.count()
    target.refresh_from_db()
    assert target.start_at == original_start
    assert target.start_period == 3


def test_conflict_apply_import_records_note_as_approval_reference(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    target = _session("R-DATA-MON", date(2026, 11, 2))

    batch, result = _conflict_batch(school, tmp_path)
    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=target)
    assert decision.kind == "conflict"
    _decide(batch, academic_staff, choice="apply_import", note="", session_id=target.id)
    _decide(batch, academic_staff, only_decided=True)
    with pytest.raises(ImportCommitError) as missing_note:
        commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert missing_note.value.code == "IMPORT_DECISION_NOTE_REQUIRED"

    _decide(batch, academic_staff, choice="apply_import", note="学校已书面同意临时调整", session_id=target.id)
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)

    assert commit.committed
    assert commit.decisions_applied == 1
    target.refresh_from_db()
    assert target.start_period == 1
    assert target.end_period == 2
    change = SessionChange.objects.get(session=target)
    assert change.approval_ref == "学校已书面同意临时调整"
    assert change.forced_override is True


def test_approved_reschedule_makes_rule_room_change_a_decision(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    target = _session("R-DATA-MON", date(2026, 11, 2))
    classroom = Classroom.objects.get(classroom_code="B201")
    apply_session_change(
        target.id,
        target_date=date(2026, 11, 3),
        start_period=5,
        end_period=6,
        classroom_id=classroom.id,
        actor=academic_staff,
        reason="已有正式调课",
        approval_ref="B31-001",
        now=PREVIEW_NOW,
    )

    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[8] = "B201"
    write_csv(files["rules.csv"], rows)
    batch, result = _preview_files(school, [files["rules.csv"]])

    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=target)
    assert decision.kind == "touched_change"
    assert "正式调课" in decision.reason
    with pytest.raises(ImportCommitError) as excinfo:
        commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert excinfo.value.code == "UNDECIDED_IMPORT_DECISIONS"

    _decide(batch, academic_staff, choice="keep_existing")
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    assert commit.decisions_kept == 1
    target.refresh_from_db()
    assert target.start_at.astimezone(target.offering.term.tzinfo).date() == date(2026, 11, 3)


def test_formal_change_between_preview_and_commit_rejects_stale_decisions(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    noted = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    TeachingNote.objects.create(session=noted, author=teacher_one, draft_covered="备注")

    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[3] = "3"
    write_csv(files["rules.csv"], rows)
    batch, result = _preview_files(school, [files["rules.csv"]])
    _decide(batch, academic_staff, choice="keep_existing")

    other = _session("R-CRIME-ODD", date(2026, 11, 2))
    classroom = Classroom.objects.get(classroom_code="B201")
    apply_session_change(
        other.id,
        target_date=date(2026, 11, 3),
        start_period=5,
        end_period=6,
        classroom_id=classroom.id,
        actor=academic_staff,
        reason="预览后被正式调课",
        approval_ref="B31-STALE",
        now=PREVIEW_NOW,
    )

    stale = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert not stale.committed
    assert stale.code == "PREVIEW_STALE"
    batch.refresh_from_db()
    assert batch.status == ImportStatus.UPLOADED
    noted.refresh_from_db()
    assert noted.start_at.astimezone(noted.offering.term.tzinfo).date() == date(2026, 11, 2)


@pytest.mark.django_db(transaction=True)
def test_teacher_replacement_updates_scope_and_visibility(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    teacher_two = TeacherProfile.objects.get(teacher_no="0002")
    completed = _session("R-CRIME-ODD", date(2026, 10, 19))
    future = _session("R-CRIME-ODD", date(2026, 11, 2))
    # Visibility also includes every offering of the same teaching class.  End
    # the unrelated DATA assignment first so the assertion isolates the CRIME
    # roster change instead of proving the shared-class rule.
    TeachingAssignment.objects.filter(
        offering__offering_code=DATA,
        teacher=teacher_one,
    ).update(effective_until=SCHOOL_DATE)
    _link_user(teacher_one)
    _link_user(teacher_two)
    cursor = str(ChangeLog.objects.filter(school=school).order_by("-change_seq").first().change_seq)

    files = _copy_files(tmp_path)
    offering_rows = read_csv(files["offerings.csv"])
    header = offering_rows[0]
    crime = next(row for row in offering_rows if row[1] == CRIME)
    crime[7] = "0002"
    crime[8] = "0002"
    # Keep only the CRIME row: merge does not delete DATA, and re-sending its
    # unchanged teacher_codes would reopen the assignment this test ended.
    write_csv(files["offerings.csv"], [header, crime])
    rule_rows = read_csv(files["rules.csv"])
    odd = next(row for row in rule_rows if row[0] == "R-CRIME-ODD")
    odd[7] = "0002"
    write_csv(files["rules.csv"], rule_rows)
    batch, result = _preview_files(school, [files["offerings.csv"], files["rules.csv"]])

    assert not result.has_errors, result.errors
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    teacher_one.refresh_from_db()
    teacher_two.refresh_from_db()
    assert teacher_one.assignment_scope_version == 2
    assert teacher_two.assignment_scope_version == 2
    assert set(future.teachers.values_list("teacher_no", flat=True)) == {"0002"}
    assert set(completed.teachers.values_list("teacher_no", flat=True)) == {"0001"}

    changes = build_changes_payload(teacher_one, cursor)
    changed_session_ids = {item["id"] for item in changes["sessions"]}
    assert str(future.id) not in changed_session_ids

    bootstrap_one = build_bootstrap_payload(teacher_one)
    bootstrap_two = build_bootstrap_payload(teacher_two)
    ids_one = {item["id"] for item in bootstrap_one["sessions"]}
    ids_two = {item["id"] for item in bootstrap_two["sessions"]}
    assert str(future.id) not in ids_one
    assert str(future.id) in ids_two
    # Completed history stays visible to its actual teacher.
    assert str(completed.id) in ids_one


def test_pending_request_stays_submitted_and_is_notified(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    target = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    classroom = Classroom.objects.get(classroom_code="B201")
    request_obj = create_request(
        teacher_one,
        target,
        target_date=date(2026, 11, 3),
        target_start_period=5,
        target_end_period=6,
        target_classroom=classroom,
        reason="教师申请调课",
        submit=True,
        now=PREVIEW_NOW,
    )
    assert request_obj.status == "submitted"

    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[8] = "B201"
    write_csv(files["rules.csv"], rows)
    batch, result = _preview_files(school, [files["rules.csv"]])

    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=target)
    assert "待审批" in decision.reason
    _decide(batch, academic_staff, only_decided=True)
    _decide(batch, academic_staff, choice="apply_import", note="学校正式课表调整", session_id=target.id)
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed

    request_obj.refresh_from_db()
    assert request_obj.status == "submitted"
    assert request_obj.check_summary["import_update"]["batch_id"] == str(batch.pk)
    assert Notification.objects.filter(
        recipient=teacher_one,
        event_key=f"import:{batch.pk}:request:{request_obj.id}",
    ).exists()


def test_completed_row_cannot_be_reverted_by_incremental_import(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    target = _session("R-CRIME-ODD", date(2026, 10, 5))
    assert target.status == SessionStatus.COMPLETED

    files = _copy_files(tmp_path)
    rows = read_csv(files["sessions.csv"])
    row = next(item for item in rows if item[0] == "S-CRIME-W05")
    row[13] = "scheduled"
    row[15] = ""
    row[16] = ""
    row[17] = ""
    row[18] = ""
    write_csv(files["sessions.csv"], rows)
    batch, result = _preview_files(school, [files["sessions.csv"]])

    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=target)
    assert decision.kind == "completed_locked"
    assert decision.options == ["keep_existing"]
    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)

    _decide(batch, academic_staff, choice="keep_existing")
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    assert commit.decisions_kept == 1
    target.refresh_from_db()
    assert target.status == SessionStatus.COMPLETED
    assert target.actual_hours == Decimal("2.0")


def test_repreview_preserves_choice_only_when_proposal_is_unchanged(
    school,
    academic_staff,
    tmp_path,
    settings,
) -> None:
    _import_all(school, academic_staff)
    noted = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    TeachingNote.objects.create(session=noted, author=teacher_one, draft_covered="备注")

    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[3] = "3"
    write_csv(files["rules.csv"], rows)
    batch, _ = _preview_files(school, [files["rules.csv"]])
    _decide(batch, academic_staff, choice="keep_existing", note="教务确认保留")
    preview_batch(batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    decision = batch.decisions.get(session=noted)
    assert decision.choice == "keep_existing"
    assert decision.note == "教务确认保留"

    stored = Path(settings.MEDIA_ROOT) / "imports" / str(batch.pk) / "rules.csv"
    stored_rows = read_csv(stored)
    stored_row = next(row for row in stored_rows if row[0] == "R-DATA-MON")
    stored_row[3] = "4"
    write_csv(stored, stored_rows)
    preview_batch(batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    decision.refresh_from_db()
    assert decision.choice == ""
    assert decision.note == ""
    assert decision.decided_at is None


def test_admin_lists_and_saves_all_keep_decisions(client, school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    noted = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    TeachingNote.objects.create(session=noted, author=teacher_one, draft_covered="备注")
    files = _copy_files(tmp_path)
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[3] = "3"
    write_csv(files["rules.csv"], rows)
    batch, _ = _preview_files(school, [files["rules.csv"]])

    client.force_login(academic_staff)
    detail = client.get(reverse("admin:imports_importbatch_change", args=[batch.pk]))
    assert detail.status_code == 200
    rendered = detail.content.decode()
    assert "人工决定项" in rendered
    assert "全部保留现状" in rendered
    assert "存在未决定项时不能提交" in rendered

    response = client.post(
        reverse("admin:imports_importbatch_decisions", args=[batch.pk]),
        {"action": "keep_all"},
    )
    assert response.status_code == 302
    assert all(decision.choice == "keep_existing" for decision in ImportDecision.objects.filter(batch=batch))
    detail = client.get(reverse("admin:imports_importbatch_change", args=[batch.pk]))
    assert "提交导入" in detail.content.decode()
    commit = client.post(reverse("admin:imports_importbatch_commit", args=[batch.pk]))
    assert commit.status_code == 302
    batch.refresh_from_db()
    assert batch.status == ImportStatus.COMMITTED


def test_first_import_commit_query_count_is_flat_and_messages_are_aggregated(
    school,
    academic_staff,
    tmp_path,
    django_assert_max_num_queries,
) -> None:
    small_files = _copy_files(tmp_path / "small")
    small_batch = make_batch(school, files=_core_files(small_files))
    small_preview = preview_batch(small_batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert not small_preview.has_errors, small_preview.errors
    with CaptureQueriesContext(connection) as small_queries:
        with django_assert_max_num_queries(220):
            small_commit = commit_batch(small_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert small_commit.committed, (small_commit.message, small_batch.errors_json)
    assert small_commit.sessions_new == 40
    for teacher_no in ("0001", "0002"):
        teacher = TeacherProfile.objects.get(school=school, teacher_no=teacher_no)
        assert (
            Notification.objects.filter(
                recipient=teacher,
                type=NotificationType.SCHEDULE_CHANGED,
                event_key=f"import:{small_batch.pk}:{teacher.id}",
            ).count()
            == 1
        )
    assert (
        Notification.objects.filter(
            type=NotificationType.SCHEDULE_CHANGED,
            event_key__startswith=f"import:{small_batch.pk}:",
        ).count()
        == 2
    )

    large_school = School.objects.create(code="DEMO-SCHOOL-200", name="虚构演示学校二百")
    large_files = _copy_files(tmp_path / "large")
    _expand_rules(large_files)
    large_batch = make_batch(large_school, files=_core_files(large_files))
    large_preview = preview_batch(large_batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert not large_preview.has_errors, large_preview.errors[:8]
    with CaptureQueriesContext(connection) as large_queries:
        with django_assert_max_num_queries(260):
            large_commit = commit_batch(large_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert large_commit.committed
    assert large_commit.sessions_new == 200
    for teacher_no in ("0001", "0002"):
        teacher = TeacherProfile.objects.get(school=large_school, teacher_no=teacher_no)
        assert (
            Notification.objects.filter(
                recipient=teacher,
                type=NotificationType.SCHEDULE_CHANGED,
                event_key=f"import:{large_batch.pk}:{teacher.id}",
            ).count()
            == 1
        )
    assert abs(len(large_queries) - len(small_queries)) <= 100


def test_period_time_preview_query_count_is_flat(
    school,
    academic_staff,
    tmp_path,
    django_assert_max_num_queries,
) -> None:
    def import_rules_only(target_school, directory: Path, copies: int) -> None:
        files = _copy_files(directory)
        _all_periods_start_at_one(files)
        if copies:
            _expand_rules(files, copies)
        batch = make_batch(target_school, files=_rules_only(files))
        result = preview_batch(batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
        assert not result.has_errors, result.errors
        commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
        assert commit.committed

    import_rules_only(school, tmp_path / "preview-small", 0)
    small_periods = _copy_files(tmp_path / "preview-small-periods")["periods.csv"]
    rows = read_csv(small_periods)
    rows[1][2] = "08:10"
    write_csv(small_periods, rows)
    small_batch = make_batch(school, files=[small_periods])
    with CaptureQueriesContext(connection) as small_queries:
        with django_assert_max_num_queries(220):
            small_preview = preview_batch(small_batch, now=LATE_NOW, school_date=LATE_NOW.date())
    assert not small_preview.has_errors, small_preview.errors
    assert small_preview.summary["session_target_counts"]["kept_past"] == 40

    large_school = School.objects.create(code="DEMO-SCHOOL-200-PREVIEW", name="虚构演示学校二百预览")
    import_rules_only(large_school, tmp_path / "preview-large", 4)
    large_periods = _copy_files(tmp_path / "preview-large-periods")["periods.csv"]
    rows = read_csv(large_periods)
    rows[1][2] = "08:10"
    write_csv(large_periods, rows)
    large_batch = make_batch(large_school, files=[large_periods])
    with CaptureQueriesContext(connection) as large_queries:
        with django_assert_max_num_queries(260):
            large_preview = preview_batch(large_batch, now=LATE_NOW, school_date=LATE_NOW.date())
    assert not large_preview.has_errors, large_preview.errors[:8]
    assert large_preview.summary["session_target_counts"]["kept_past"] == 200
    assert abs(len(large_queries) - len(small_queries)) <= 20


def test_import_batch_conflict_overlay_sees_peer_targets_and_vacated_slots(
    school,
    academic_staff,
) -> None:
    _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    data = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(school=school, teacher_no="0001")
    room_301 = Classroom.objects.get(school=school, classroom_code="A301")
    room_302 = Classroom.objects.get(school=school, classroom_code="A302")
    periods = {period.period_number: period for period in Period.objects.filter(term=crime.offering.term)}

    same_target = [
        _evaluation_request(
            crime,
            day=date(2026, 11, 3),
            start_period=5,
            end_period=6,
            classroom=room_301,
            teacher_ids=(teacher_one.id,),
        ),
        _evaluation_request(
            data,
            day=date(2026, 11, 3),
            start_period=5,
            end_period=6,
            classroom=room_301,
            teacher_ids=(teacher_one.id,),
        ),
    ]
    evaluations = evaluate_import_targets(same_target, periods=periods, now=PREVIEW_NOW)
    assert evaluations[crime.id].conflicts
    assert evaluations[data.id].conflicts
    assert {conflict.category for conflict in evaluations[crime.id].conflicts} >= {"teacher", "classroom"}
    assert {conflict.category for conflict in evaluations[data.id].conflicts} >= {"teacher", "classroom"}

    swap = [
        _evaluation_request(
            crime,
            day=date(2026, 11, 2),
            start_period=3,
            end_period=4,
            classroom=room_302,
            teacher_ids=(teacher_one.id,),
        ),
        _evaluation_request(
            data,
            day=date(2026, 11, 2),
            start_period=1,
            end_period=2,
            classroom=room_301,
            teacher_ids=(teacher_one.id,),
        ),
    ]
    evaluations = evaluate_import_targets(swap, periods=periods, now=PREVIEW_NOW)
    assert not evaluations[crime.id].conflicts
    assert not evaluations[data.id].conflicts


def test_teacher_only_and_teacher_plus_time_use_target_teachers(
    school,
    academic_staff,
) -> None:
    _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 16))
    data = _session("R-DATA-MON", date(2026, 11, 9))
    teacher_two = TeacherProfile.objects.get(school=school, teacher_no="0002")
    room_301 = Classroom.objects.get(school=school, classroom_code="A301")
    room_302 = Classroom.objects.get(school=school, classroom_code="A302")
    periods = {period.period_number: period for period in Period.objects.filter(term=crime.offering.term)}
    Enrollment.objects.filter(offering=data.offering, student__student_no="000001").delete()

    def move_data_occupancy(day: date) -> None:
        data.start_at = datetime.combine(day, periods[1].start_time, tzinfo=data.offering.term.tzinfo)
        data.end_at = datetime.combine(day, periods[2].end_time, tzinfo=data.offering.term.tzinfo)
        data.start_period = 1
        data.end_period = 2
        data.classroom = room_302
        data.save(update_fields=["start_at", "end_at", "start_period", "end_period", "classroom", "updated_at"])
        SessionTeacher.objects.filter(session=data).delete()
        SessionTeacher.objects.create(session=data, teacher=teacher_two)

    move_data_occupancy(date(2026, 11, 16))
    teacher_only = _evaluation_request(
        crime,
        day=date(2026, 11, 16),
        start_period=1,
        end_period=2,
        classroom=room_301,
        teacher_ids=(teacher_two.id,),
    )
    evaluations = evaluate_import_targets([teacher_only], periods=periods, now=PREVIEW_NOW)
    assert "teacher" in {conflict.category for conflict in evaluations[crime.id].conflicts}

    move_data_occupancy(date(2026, 11, 9))
    teacher_and_time = _evaluation_request(
        crime,
        day=date(2026, 11, 9),
        start_period=1,
        end_period=2,
        classroom=room_301,
        teacher_ids=(teacher_two.id,),
    )
    evaluations = evaluate_import_targets([teacher_and_time], periods=periods, now=PREVIEW_NOW)
    assert "teacher" in {conflict.category for conflict in evaluations[crime.id].conflicts}


def test_preview_batch_target_overlay_detects_same_target_and_vacated_slot(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    data = _session("R-DATA-MON", date(2026, 11, 2))
    for external_id, session in (("X-CRIME-1102", crime), ("X-DATA-1102", data)):
        ImportIdentity.objects.create(
            school=school,
            source_system="demo_csv_v1",
            entity_type="session",
            external_id=external_id,
            object_id=session.id,
            batch=baseline,
        )

    def row(
        external_id: str,
        offering_code: str,
        rule_code: str,
        origin_period: int,
        classroom_code: str,
        session_date: str,
        start_period: int,
        end_period: int,
    ) -> list[str]:
        return [
            external_id,
            TERM_CODE,
            offering_code,
            rule_code,
            "2026-11-02",
            str(origin_period),
            session_date,
            str(start_period),
            str(end_period),
            "",
            "",
            classroom_code,
            "0001",
            "scheduled",
            "2.0",
            "",
            "",
            "",
            "",
            "T14-OVERLAY",
        ]

    same_files = _copy_files(tmp_path / "same-target")
    rows = read_csv(same_files["sessions.csv"])
    rows.append(row("X-CRIME-1102", CRIME, "R-CRIME-ODD", 1, "A301", "2026-11-03", 5, 6))
    rows.append(row("X-DATA-1102", DATA, "R-DATA-MON", 3, "A301", "2026-11-03", 5, 6))
    write_csv(same_files["sessions.csv"], rows)
    batch, result = _preview_files(school, [same_files["sessions.csv"]])
    assert not result.has_errors, result.errors
    conflict_ids = set(batch.decisions.filter(kind="conflict").values_list("session_id", flat=True))
    assert conflict_ids == {crime.id, data.id}

    swap_files = _copy_files(tmp_path / "swap-target")
    rows = read_csv(swap_files["sessions.csv"])
    rows.append(row("X-CRIME-1102", CRIME, "R-CRIME-ODD", 1, "A302", "2026-11-02", 3, 4))
    rows.append(row("X-DATA-1102", DATA, "R-DATA-MON", 3, "A301", "2026-11-02", 1, 2))
    write_csv(swap_files["sessions.csv"], rows)
    swap_batch, swap = _preview_files(school, [swap_files["sessions.csv"]])
    assert not swap.has_errors, swap.errors
    assert not swap_batch.decisions.filter(session_id__in=(crime.id, data.id), kind="conflict").exists()


def test_preview_teacher_change_enters_conflict_check_for_teacher_only_and_teacher_plus_time(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 16))
    data = _session("R-DATA-MON", date(2026, 11, 9))
    teacher_two = TeacherProfile.objects.get(school=school, teacher_no="0002")
    room_302 = Classroom.objects.get(school=school, classroom_code="A302")
    periods = {period.period_number: period for period in Period.objects.filter(term=crime.offering.term)}
    Enrollment.objects.filter(offering=data.offering, student__student_no="000001").delete()
    occupancy = ClassSession.objects.create(
        offering=data.offering,
        start_at=datetime.combine(date(2026, 11, 16), periods[1].start_time, tzinfo=data.offering.term.tzinfo),
        end_at=datetime.combine(date(2026, 11, 16), periods[2].end_time, tzinfo=data.offering.term.tzinfo),
        start_period=1,
        end_period=2,
        classroom=room_302,
        status=SessionStatus.SCHEDULED,
        planned_hours=Decimal("2.0"),
    )
    SessionTeacher.objects.create(session=occupancy, teacher=teacher_two)
    ImportIdentity.objects.create(
        school=school,
        source_system="demo_csv_v1",
        entity_type="session",
        external_id="X-CRIME-1116",
        object_id=crime.id,
        batch=baseline,
    )

    def move_data_occupancy(day: date) -> None:
        occupancy.start_at = datetime.combine(day, periods[1].start_time, tzinfo=data.offering.term.tzinfo)
        occupancy.end_at = datetime.combine(day, periods[2].end_time, tzinfo=data.offering.term.tzinfo)
        occupancy.save(update_fields=["start_at", "end_at", "updated_at"])

    def session_row(target_date: str) -> list[str]:
        return [
            "X-CRIME-1116",
            TERM_CODE,
            CRIME,
            "R-CRIME-ODD",
            "2026-11-16",
            "1",
            target_date,
            "1",
            "2",
            "",
            "",
            "A301",
            "0002",
            "scheduled",
            "2.0",
            "",
            "",
            "",
            "",
            "T14-TEACHER",
        ]

    move_data_occupancy(date(2026, 11, 16))
    teacher_only_files = _copy_files(tmp_path / "teacher-only")
    rows = read_csv(teacher_only_files["sessions.csv"])
    rows.append(session_row("2026-11-16"))
    write_csv(teacher_only_files["sessions.csv"], rows)
    teacher_only_batch, teacher_only = _preview_files(school, [teacher_only_files["sessions.csv"]])
    assert not teacher_only.has_errors, teacher_only.errors
    decision = teacher_only_batch.decisions.get(session=crime)
    assert decision.kind == "conflict"
    assert "教师冲突" in decision.reason

    move_data_occupancy(date(2026, 11, 9))
    teacher_time_files = _copy_files(tmp_path / "teacher-time")
    rows = read_csv(teacher_time_files["sessions.csv"])
    rows.append(session_row("2026-11-09"))
    write_csv(teacher_time_files["sessions.csv"], rows)
    teacher_time_batch, teacher_time = _preview_files(school, [teacher_time_files["sessions.csv"]])
    assert not teacher_time.has_errors, teacher_time.errors
    decision = teacher_time_batch.decisions.get(session=crime)
    assert decision.kind == "conflict"
    assert "教师冲突" in decision.reason


def test_commit_rechecks_actual_keep_apply_combination(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    data = _session("R-DATA-MON", date(2026, 11, 2))
    teacher_one = TeacherProfile.objects.get(school=school, teacher_no="0001")
    TeachingNote.objects.create(session=crime, author=teacher_one, draft_covered="保留原安排")
    for external_id, session in (("X-CRIME-1102-SWAP", crime), ("X-DATA-1102-SWAP", data)):
        ImportIdentity.objects.create(
            school=school,
            source_system="demo_csv_v1",
            entity_type="session",
            external_id=external_id,
            object_id=session.id,
            batch=baseline,
        )

    files = _copy_files(tmp_path / "keep-apply")
    rows = read_csv(files["sessions.csv"])
    for external_id, offering_code, rule_code, origin_period, classroom_code, start_period, end_period in (
        ("X-CRIME-1102-SWAP", CRIME, "R-CRIME-ODD", 1, "A302", 3, 4),
        ("X-DATA-1102-SWAP", DATA, "R-DATA-MON", 3, "A301", 1, 2),
    ):
        rows.append(
            [
                external_id,
                TERM_CODE,
                offering_code,
                rule_code,
                "2026-11-02",
                str(origin_period),
                "2026-11-02",
                str(start_period),
                str(end_period),
                "",
                "",
                classroom_code,
                "0001",
                "scheduled",
                "2.0",
                "",
                "",
                "",
                "",
                "T14-KEEP-APPLY",
            ]
        )
    write_csv(files["sessions.csv"], rows)
    batch, result = _preview_files(school, [files["sessions.csv"]])
    assert not result.has_errors, result.errors
    assert not batch.decisions.filter(session=data, kind="conflict").exists()
    _decide(batch, academic_staff, choice="keep_existing", session_id=crime.id)
    with pytest.raises(ImportCommitError) as exc:
        commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert exc.value.code == "IMPORT_FINAL_CONFLICT"
    crime.refresh_from_db()
    data.refresh_from_db()
    assert crime.start_at.astimezone(crime.offering.term.tzinfo).date() == date(2026, 11, 2)
    assert data.start_at.astimezone(data.offering.term.tzinfo).date() == date(2026, 11, 2)


def test_sessions_row_offering_must_match_rule_and_existing_session(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    ImportIdentity.objects.create(
        school=school,
        source_system="demo_csv_v1",
        entity_type="session",
        external_id="X-OFFERING-MISMATCH",
        object_id=crime.id,
        batch=baseline,
    )
    files = _copy_files(tmp_path / "offering-mismatch")
    rows = read_csv(files["sessions.csv"])

    def row(external_id: str, offering_code: str, rule_code: str, origin_period: int) -> list[str]:
        return [
            external_id,
            TERM_CODE,
            offering_code,
            rule_code,
            "2026-11-02",
            str(origin_period),
            "2026-11-02",
            "1",
            "2",
            "",
            "",
            "A301",
            "0001",
            "scheduled",
            "2.0",
            "",
            "",
            "",
            "",
            "T14-OFFERING",
        ]

    rows.append(row("X-NEW-OFFERING-MISMATCH", DATA, "R-CRIME-ODD", 1))
    rows.append(row("X-OFFERING-MISMATCH", DATA, "R-DATA-MON", 3))
    write_csv(files["sessions.csv"], rows)
    batch, result = _preview_files(school, [files["sessions.csv"]])
    assert result.has_errors
    mismatches = [error for error in result.errors if error["code"] == "SESSION_OFFERING_MISMATCH"]
    assert len(mismatches) == 2
    assert {error["field"] for error in mismatches} == {"offering_code", "origin_rule_code"}
    with pytest.raises(ImportCommitError) as exc:
        commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert exc.value.code == "PREVIEW_HAS_ERRORS"


def test_start_period_change_repeat_keeps_identity_and_origin(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path / "start-period-repeat")
    rows = read_csv(files["rules.csv"])
    data_row = next(row for row in rows if row[0] == "R-DATA-MON")
    data_row[4] = "5"
    data_row[5] = "6"
    write_csv(files["rules.csv"], rows)
    batch, result = _preview_files(school, [files["rules.csv"]])
    assert not result.has_errors, result.errors
    assert commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE).committed

    before_repeat = {
        session.id: (
            session.origin_teaching_date,
            session.origin_period,
            session.start_at,
            session.status,
        )
        for session in ClassSession.objects.all()
    }
    repeat_batch, repeat = _preview_files(school, [files["rules.csv"]])
    assert not repeat.has_errors, repeat.errors
    assert repeat.summary["session_target_counts"]["new"] == 0
    assert repeat.summary["session_target_counts"]["auto_updated"] == 0
    assert repeat.summary["session_target_counts"]["auto_cancelled"] == 0
    assert repeat.summary["session_target_counts"]["auto_restored"] == 0
    assert repeat_batch.decisions.count() == 0
    repeat_commit = commit_batch(repeat_batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert repeat_commit.committed
    after_repeat = {
        session.id: (
            session.origin_teaching_date,
            session.origin_period,
            session.start_at,
            session.status,
        )
        for session in ClassSession.objects.all()
    }
    assert after_repeat == before_repeat


def test_preview_new_session_target_is_visible_to_existing_move(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    ImportIdentity.objects.create(
        school=school,
        source_system="demo_csv_v1",
        entity_type="session",
        external_id="X-CRIME-NEW-OCCUPANCY",
        object_id=crime.id,
        batch=baseline,
    )
    files = _copy_files(tmp_path / "new-occupancy")
    rows = read_csv(files["sessions.csv"])
    move_row = [
        "X-CRIME-NEW-OCCUPANCY",
        TERM_CODE,
        CRIME,
        "R-CRIME-ODD",
        "2026-11-02",
        "1",
        "2026-11-03",
        "5",
        "6",
        "",
        "",
        "A301",
        "0001",
        "scheduled",
        "2.0",
        "",
        "",
        "",
        "",
        "T14-NEW-OCCUPANCY",
    ]
    new_row = [
        "X-NEW-SESSION-OCCUPANCY",
        TERM_CODE,
        DATA,
        "",
        "",
        "",
        "2026-11-03",
        "5",
        "6",
        "",
        "",
        "A301",
        "0001",
        "scheduled",
        "2.0",
        "",
        "",
        "",
        "",
        "",
    ]
    rows.extend([move_row, new_row])
    write_csv(files["sessions.csv"], rows)
    batch, result = _preview_files(school, [files["sessions.csv"]])
    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=crime)
    assert decision.kind == "conflict"
    assert "教室冲突" in decision.reason


def test_preview_new_room_and_person_blocks_are_visible(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    ImportIdentity.objects.create(
        school=school,
        source_system="demo_csv_v1",
        entity_type="session",
        external_id="X-CRIME-BLOCK",
        object_id=crime.id,
        batch=baseline,
    )
    files = _copy_files(tmp_path / "new-blocks")
    session_rows = read_csv(files["sessions.csv"])
    session_rows.append(
        [
            "X-CRIME-BLOCK",
            TERM_CODE,
            CRIME,
            "R-CRIME-ODD",
            "2026-11-02",
            "1",
            "2026-11-03",
            "5",
            "6",
            "",
            "",
            "A301",
            "0001",
            "scheduled",
            "2.0",
            "",
            "",
            "",
            "",
            "T14-BLOCK",
        ]
    )
    write_csv(files["sessions.csv"], session_rows)
    room_rows = read_csv(files["room_blocks.csv"])
    room_rows.append(
        [
            "RB-NEW-BLOCK",
            TERM_CODE,
            "A301",
            "2026-11-03T14:00:00+08:00",
            "2026-11-03T15:40:00+08:00",
            "虚构临时维修",
        ]
    )
    write_csv(files["room_blocks.csv"], room_rows)
    person_rows = read_csv(files["resource_unavailability.csv"])
    person_rows.append(
        [
            "PB-NEW-BLOCK",
            TERM_CODE,
            "teacher",
            "0001",
            "2026-11-03T14:00:00+08:00",
            "2026-11-03T15:40:00+08:00",
            "虚构临时会议",
        ]
    )
    write_csv(files["resource_unavailability.csv"], person_rows)

    batch, result = _preview_files(
        school,
        [files["sessions.csv"], files["room_blocks.csv"], files["resource_unavailability.csv"]],
    )
    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=crime)
    assert decision.kind == "conflict"
    assert "教室冲突" in decision.reason
    assert "教师冲突" in decision.reason
    _decide(batch, academic_staff, choice="apply_import", note="已核对维修和会议安排")
    commit = commit_batch(batch, academic_staff, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert commit.committed
    crime.refresh_from_db()
    assert crime.start_at.astimezone(crime.offering.term.tzinfo).date() == date(2026, 11, 3)


def test_cross_term_batch_overlay_does_not_drop_other_term_occupancy(
    school,
    academic_staff,
) -> None:
    _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    data = _session("R-DATA-MON", date(2026, 11, 9))
    teacher_one = TeacherProfile.objects.get(school=school, teacher_no="0001")
    teacher_two = TeacherProfile.objects.get(school=school, teacher_no="0002")
    room_301 = Classroom.objects.get(school=school, classroom_code="A301")
    term = crime.offering.term
    periods = {period.period_number: period for period in Period.objects.filter(term=term)}
    second_term = Term.objects.create(
        school=school,
        code="2026-cross-term",
        name="虚构交叉学期",
        start_date=term.start_date,
        end_date=term.end_date,
        first_week_monday=term.first_week_monday,
        teaching_week_count=term.teaching_week_count,
        timezone=term.timezone,
    )
    second_offering = CourseOffering.objects.create(
        term=second_term,
        offering_code="OFF-CROSS-TERM",
        course=data.offering.course,
        teaching_class=data.offering.teaching_class,
        target_hours=Decimal("2.0"),
        lead_teacher=teacher_two,
        minutes_per_hour=45,
    )
    day = date(2026, 11, 3)
    start_at = datetime.combine(day, periods[5].start_time, tzinfo=term.tzinfo)
    end_at = datetime.combine(day, periods[6].end_time, tzinfo=term.tzinfo)
    other = ClassSession.objects.create(
        offering=second_offering,
        start_at=start_at,
        end_at=end_at,
        start_period=5,
        end_period=6,
        classroom=room_301,
        status=SessionStatus.SCHEDULED,
        planned_hours=Decimal("2.0"),
    )
    SessionTeacher.objects.create(session=other, teacher=teacher_two)

    request = _evaluation_request(
        crime,
        day=day,
        start_period=5,
        end_period=6,
        classroom=room_301,
        teacher_ids=(teacher_one.id,),
    )
    occupancy_targets = [
        ImportOccupancyTarget(
            id=crime.id,
            offering_id=crime.offering_id,
            start_at=start_at,
            end_at=end_at,
            classroom_id=room_301.id,
            teacher_ids=(teacher_one.id,),
            course_name=CRIME,
        ),
        ImportOccupancyTarget(
            id=other.id,
            offering_id=second_offering.id,
            start_at=start_at,
            end_at=end_at,
            classroom_id=room_301.id,
            teacher_ids=(teacher_two.id,),
            course_name="OFF-CROSS-TERM",
        ),
    ]
    evaluations = evaluate_import_targets(
        [request],
        periods=periods,
        now=PREVIEW_NOW,
        replaced_session_ids={crime.id, other.id},
        occupancy_targets=occupancy_targets,
    )
    assert "classroom" in {conflict.category for conflict in evaluations[crime.id].conflicts}


def test_preview_changed_room_block_uses_new_time_and_drops_old_time(
    school,
    academic_staff,
    tmp_path,
) -> None:
    baseline = _import_all(school, academic_staff)
    crime = _session("R-CRIME-ODD", date(2026, 11, 2))
    ImportIdentity.objects.create(
        school=school,
        source_system="demo_csv_v1",
        entity_type="session",
        external_id="X-CRIME-CHANGED-BLOCK",
        object_id=crime.id,
        batch=baseline,
    )
    files = _copy_files(tmp_path / "changed-block")
    session_rows = read_csv(files["sessions.csv"])
    session_rows.append(
        [
            "X-CRIME-CHANGED-BLOCK",
            TERM_CODE,
            CRIME,
            "R-CRIME-ODD",
            "2026-11-02",
            "1",
            "2026-11-03",
            "5",
            "6",
            "",
            "",
            "A301",
            "0001",
            "scheduled",
            "2.0",
            "",
            "",
            "",
            "",
            "T14-CHANGED-BLOCK",
        ]
    )
    write_csv(files["sessions.csv"], session_rows)
    room_rows = read_csv(files["room_blocks.csv"])
    block_row = next(row for row in room_rows if row[0] == "RB-A301-20261028")
    block_row[3] = "2026-11-03T14:00:00+08:00"
    block_row[4] = "2026-11-03T15:40:00+08:00"
    block_row[5] = "虚构改期维修"
    write_csv(files["room_blocks.csv"], room_rows)

    batch, result = _preview_files(
        school,
        [files["sessions.csv"], files["room_blocks.csv"]],
    )
    assert not result.has_errors, result.errors
    decision = batch.decisions.get(session=crime)
    assert decision.kind == "conflict"
    assert "教室冲突" in decision.reason
