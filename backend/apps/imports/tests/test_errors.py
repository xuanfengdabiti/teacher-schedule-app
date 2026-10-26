"""Row-addressable error reporting and all-or-nothing rejection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from apps.academics.models import Course, CourseOffering, Enrollment, Student, TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.imports.services import ImportCommitError, commit_batch, preview_batch
from apps.imports.tests.conftest import PREVIEW_NOW, copy_templates, make_batch, read_csv, write_csv
from apps.scheduling.models import ClassSession, ScheduleRule

pytestmark = pytest.mark.django_db


def _preview_changed(school, tmp_path: Path, mutate: Callable[[dict[str, Path]], None]):
    paths = copy_templates(tmp_path)
    files = {path.name: path for path in paths}
    mutate(files)
    batch = make_batch(school, files=paths)
    result = preview_batch(batch, now=PREVIEW_NOW)
    return batch, result


def _issue(result, *, file: str, row: int, field: str, code: str):
    for issue in result.issues:
        if (issue.file, issue.row, issue.field, issue.code) == (file, row, field, code):
            return issue
    return None


def _assert_no_business_rows() -> None:
    assert Term.objects.count() == 0
    assert TeacherProfile.objects.count() == 0
    assert Student.objects.count() == 0
    assert Classroom.objects.count() == 0
    assert Course.objects.count() == 0
    assert CourseOffering.objects.count() == 0
    assert Enrollment.objects.count() == 0
    assert ScheduleRule.objects.count() == 0
    assert ClassSession.objects.count() == 0


def test_unknown_origin_rule_points_to_sessions_row_four(school, academic_staff, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["sessions.csv"])
        assert rows[3][0] == "S-DATA-W07"
        rows[3][3] = "R-NOT-FOUND"
        write_csv(files["sessions.csv"], rows)

    batch, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="sessions.csv",
        row=4,
        field="origin_rule_code",
        code="RULE_NOT_FOUND",
    )
    assert issue is not None
    assert "不存在" in issue.message
    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff)
    _assert_no_business_rows()


def test_invalid_date_reports_file_row_and_field(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["terms.csv"])
        rows[1][2] = "2026/09/07"
        write_csv(files["terms.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(result, file="terms.csv", row=2, field="start_date", code="INVALID_DATE")
    assert issue is not None
    assert issue.value == "2026/09/07"


def test_null_placeholder_is_not_an_empty_optional_value(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["teachers.csv"])
        rows[1][2] = "null"
        write_csv(files["teachers.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="teachers.csv",
        row=2,
        field="department",
        code="PLACEHOLDER_NOT_ALLOWED",
    )
    assert issue is not None


def test_duplicate_external_id_in_same_file_marks_both_rows(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["teachers.csv"])
        rows.append(list(rows[1]))
        write_csv(files["teachers.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    duplicate_rows = {
        issue.row
        for issue in result.issues
        if issue.file == "teachers.csv" and issue.field == "teacher_code" and issue.code == "DUPLICATE_ID"
    }
    assert duplicate_rows == {2, 4}


def test_completed_requires_completion_ref(school, academic_staff, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["sessions.csv"])
        assert rows[1][0] == "S-CRIME-W05"
        rows[1][18] = ""
        write_csv(files["sessions.csv"], rows)

    batch, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="sessions.csv",
        row=2,
        field="completion_ref",
        code="REQUIRED",
    )
    assert issue is not None
    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff)
    _assert_no_business_rows()


def test_complete_coverage_requires_confirmation_ref(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["coverage.csv"])
        rows[1][8] = ""
        write_csv(files["coverage.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="coverage.csv",
        row=2,
        field="confirmation_ref",
        code="REQUIRED",
    )
    assert issue is not None


def test_extra_header_column_is_rejected(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["classrooms.csv"])
        rows[0].append("unexpected_column")
        write_csv(files["classrooms.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="classrooms.csv",
        row=1,
        field="unexpected_column",
        code="HEADER_FIELDS_MISMATCH",
    )
    assert issue is not None


def test_origin_key_without_expanded_session_is_not_fuzzy_matched(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["sessions.csv"])
        rows[1][5] = "2"  # origin_start_period; the rule has an origin period of 1
        write_csv(files["sessions.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="sessions.csv",
        row=2,
        field="origin_teaching_date",
        code="RULE_SESSION_NOT_FOUND",
    )
    assert issue is not None


def test_completion_time_before_session_end_is_rejected(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["sessions.csv"])
        rows[1][17] = "2026-10-10T08:30:00+08:00"
        write_csv(files["sessions.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="sessions.csv",
        row=2,
        field="completed_at",
        code="COMPLETION_BEFORE_END",
    )
    assert issue is not None


def test_completed_by_must_belong_to_session_teachers(school, tmp_path) -> None:
    def mutate(files):
        rows = read_csv(files["sessions.csv"])
        rows[1][16] = "0002"
        write_csv(files["sessions.csv"], rows)

    _, result = _preview_changed(school, tmp_path, mutate)

    issue = _issue(
        result,
        file="sessions.csv",
        row=2,
        field="completed_by_teacher_code",
        code="COMPLETED_BY_NOT_TEACHER",
    )
    assert issue is not None
