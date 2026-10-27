"""B12a rejects existing-field changes and stale previews."""

from __future__ import annotations

import pytest

from apps.academics.models import TeacherProfile, Term
from apps.imports.models import ImportIdentity, ImportStatus
from apps.imports.services import ImportCommitError, commit_batch, preview_batch
from apps.imports.tests.conftest import PREVIEW_NOW, copy_templates, make_batch, read_csv, write_csv
from apps.scheduling.models import ChangeLog, ClassSession, ScheduleRule

pytestmark = pytest.mark.django_db


def test_term_structure_difference_is_change_not_supported(school, academic_staff, tmp_path) -> None:
    first_batch = make_batch(school)
    preview_batch(first_batch, now=PREVIEW_NOW)
    commit_batch(first_batch, academic_staff)
    log_count = ChangeLog.objects.count()
    original_name = TeacherProfile.objects.get(teacher_no="0001").formal_name

    paths = copy_templates(tmp_path)
    files = {path.name: path for path in paths}
    rows = read_csv(files["terms.csv"])
    rows[1][2] = "2026-09-08"
    write_csv(files["terms.csv"], rows)
    second_batch = make_batch(school, files=[files["terms.csv"]])

    result = preview_batch(second_batch, now=PREVIEW_NOW)

    issue = next(
        (
            issue
            for issue in result.issues
            if issue.file == "terms.csv"
            and issue.row == 2
            and issue.field == "start_date"
            and issue.code == "CHANGE_NOT_SUPPORTED_YET"
        ),
        None,
    )
    assert issue is not None
    assert "新建学期" in issue.message
    with pytest.raises(ImportCommitError):
        commit_batch(second_batch, academic_staff)
    assert TeacherProfile.objects.get(teacher_no="0001").formal_name == original_name
    assert ChangeLog.objects.count() == log_count
    assert ClassSession.objects.count() == 40


def test_stale_schedule_version_forces_new_preview(school, academic_staff) -> None:
    first_batch = make_batch(school)
    preview_batch(first_batch, now=PREVIEW_NOW)
    commit_batch(first_batch, academic_staff)
    log_count = ChangeLog.objects.count()

    second_batch = make_batch(school)
    preview = preview_batch(second_batch, now=PREVIEW_NOW)
    assert not preview.has_errors
    term = Term.objects.get(code="2026-autumn")
    assert preview.preview_base_schedule_version == term.schedule_version
    Term.objects.filter(pk=term.pk).update(schedule_version=99)

    result = commit_batch(second_batch, academic_staff)

    assert not result.committed
    assert result.code == "PREVIEW_STALE"
    second_batch.refresh_from_db()
    assert second_batch.status == ImportStatus.UPLOADED
    assert any(error["code"] == "PREVIEW_STALE" for error in second_batch.errors_json)
    assert ChangeLog.objects.count() == log_count
    assert ClassSession.objects.count() == 40
    term.refresh_from_db()
    assert term.schedule_version == 99


def test_partial_new_rule_can_reference_previous_identities(school, academic_staff, tmp_path) -> None:
    first_batch = make_batch(school)
    preview_batch(first_batch, now=PREVIEW_NOW)
    commit_batch(first_batch, academic_staff)
    term = Term.objects.get(code="2026-autumn")
    original_version = term.schedule_version
    log_count = ChangeLog.objects.count()

    paths = copy_templates(tmp_path)
    files = {path.name: path for path in paths}
    rows = read_csv(files["rules.csv"])
    rows.append(
        [
            "R-EXTRA",
            "2026-autumn",
            "OFF-DATA-2026A",
            "2",
            "5",
            "6",
            "1",
            "0001",
            "B201",
            "2.0",
        ]
    )
    write_csv(files["rules.csv"], rows)
    partial_batch = make_batch(school, files=[files["rules.csv"]])

    preview = preview_batch(partial_batch, now=PREVIEW_NOW)
    assert not preview.has_errors, preview.errors
    assert preview.expected_new_sessions == 1
    assert preview.batch.term_id == term.pk

    commit = commit_batch(partial_batch, academic_staff)

    assert commit.committed
    assert ScheduleRule.objects.filter(rule_code="R-EXTRA").exists()
    assert ClassSession.objects.count() == 41
    term.refresh_from_db()
    assert term.schedule_version == original_version + 1
    assert ChangeLog.objects.count() == log_count + 2  # one new session + one term version


def test_generation_failure_rolls_back_and_marks_batch_failed(
    school,
    academic_staff,
    monkeypatch,
) -> None:
    batch = make_batch(school)
    preview_batch(batch, now=PREVIEW_NOW)

    def fail_creation(*args, **kwargs):
        raise RuntimeError("模拟生成失败")

    monkeypatch.setattr("apps.imports.commit.apply_import_session_batch", fail_creation)

    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff)

    batch.refresh_from_db()
    assert batch.status == ImportStatus.FAILED
    assert any(error["code"] == "COMMIT_FAILED" for error in batch.errors_json)
    assert not Term.objects.exists()
    assert not ClassSession.objects.exists()
    assert not ImportIdentity.objects.exists()
    assert not ChangeLog.objects.exists()
