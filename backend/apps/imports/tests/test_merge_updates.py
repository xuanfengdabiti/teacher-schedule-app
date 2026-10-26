"""B12b merge updates, warnings and assignment-scope changes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import (
    CourseOffering,
    CoverageCompleteness,
    DataCoverage,
    Enrollment,
    TeacherProfile,
    TeacherStatus,
    TeachingAssignment,
    Term,
)
from apps.classrooms.models import Classroom
from apps.imports.models import ImportBatch, ImportIdentity, ImportStatus
from apps.imports.services import ImportCommitError, commit_batch, preview_batch
from apps.imports.tests.conftest import PREVIEW_NOW, copy_templates, make_batch, read_csv, write_csv
from apps.scheduling.models import ChangeLog, ClassSession, SessionStatus
from apps.scheduling.reschedule_options import OptionQuery, find_reschedule_options
from apps.scheduling.selectors import visible_offerings_for_teacher
from apps.teaching.models import TeachingPlan

pytestmark = pytest.mark.django_db

SCHOOL_DATE = date(2026, 10, 27)
TERM_CODE = "2026-autumn"
CRIME_CODE = "OFF-CRIME-2026A"
DATA_CODE = "OFF-DATA-2026A"


@pytest.fixture(autouse=True)
def fixed_school_date(monkeypatch):
    monkeypatch.setattr("apps.scheduling.selectors.current_school_date", lambda: SCHOOL_DATE)


def _import_all(school, academic_staff) -> ImportBatch:
    batch = make_batch(school)
    preview = preview_batch(batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    assert not preview.has_errors, preview.errors
    commit = commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)
    assert commit.committed
    return batch


def _copy_files(tmp_path: Path) -> dict[str, Path]:
    return {path.name: path for path in copy_templates(tmp_path)}


def _imported_object_id(entity_type: str, external_id: str):
    return ImportIdentity.objects.get(
        source_system="demo_csv_v1",
        entity_type=entity_type,
        external_id=external_id,
    ).object_id


def _preview_files(school, files: list[Path]):
    batch = make_batch(school, files=files)
    result = preview_batch(batch, now=PREVIEW_NOW, school_date=SCHOOL_DATE)
    return batch, result


def _link_user(teacher: TeacherProfile):
    user = get_user_model().objects.create_user(
        email=f"teacher-{teacher.teacher_no}@example.invalid",
        password="test-pass-123",
    )
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    return user


def _authenticate(client: APIClient, user) -> None:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def _offering_codes_for(teacher: TeacherProfile) -> set[str]:
    return set(visible_offerings_for_teacher(teacher).values_list("offering_code", flat=True))


def _offering_payload(teacher: TeacherProfile) -> list[dict]:
    client = APIClient()
    _authenticate(client, _link_user(teacher))
    response = client.get("/api/v1/offerings/")
    assert response.status_code == 200
    payload = response.data
    if isinstance(payload, dict):
        return payload.get("results", [])
    return payload


def test_missing_teacher_row_does_not_delete_or_change_existing(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    teacher = TeacherProfile.objects.get(teacher_no="0002")
    original = {
        "formal_name": teacher.formal_name,
        "department": teacher.department,
        "is_active": teacher.is_active,
        "assignment_scope_version": teacher.assignment_scope_version,
    }
    log_count = ChangeLog.objects.count()

    files = _copy_files(tmp_path)
    rows = read_csv(files["teachers.csv"])
    write_csv(files["teachers.csv"], [rows[0], rows[1]])
    batch, result = _preview_files(school, [files["teachers.csv"]])

    assert not result.has_errors
    assert result.row_counts["teachers.csv"]["unchanged"] == 1
    assert not result.change_summary["fields"]
    commit = commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)
    assert commit.committed

    teacher.refresh_from_db()
    assert teacher.formal_name == original["formal_name"]
    assert teacher.department == original["department"]
    assert teacher.is_active == original["is_active"]
    assert teacher.assignment_scope_version == original["assignment_scope_version"]
    assert ChangeLog.objects.count() == log_count


def test_teacher_profile_updates_respect_empty_and_linked_contact_rules(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    teacher_one.phone = "old-phone"
    teacher_one.save(update_fields=["phone", "updated_at"])
    teacher_two = TeacherProfile.objects.get(teacher_no="0002")
    teacher_two.phone = "app-phone"
    teacher_two.phone_visibility = "course_teachers"
    teacher_two.wechat = "app-wechat"
    teacher_two.wechat_visibility = "private"
    teacher_two.save(update_fields=["phone", "phone_visibility", "wechat", "wechat_visibility", "updated_at"])
    _link_user(teacher_two)
    session_one = ClassSession.objects.filter(session_teachers__teacher=teacher_one).first()
    session_two = ClassSession.objects.filter(session_teachers__teacher=teacher_two).first()
    assert session_one is not None and session_two is not None
    version_one = session_one.version
    version_two = session_two.version
    log_count = ChangeLog.objects.count()

    files = _copy_files(tmp_path)
    rows = read_csv(files["teachers.csv"])
    rows[1][1] = "更新后教师甲"
    rows[1][2] = "更新院系甲"
    rows[1][4] = ""
    rows[1][5] = "school_teachers"
    rows[1][6] = "roster-wechat"
    rows[2][1] = "更新后教师乙"
    rows[2][2] = "更新院系乙"
    rows[2][4] = "roster-phone"
    rows[2][5] = "school_teachers"
    rows[2][6] = "roster-wechat-2"
    rows[2][7] = "school_teachers"
    write_csv(files["teachers.csv"], rows)
    batch, result = _preview_files(school, [files["teachers.csv"]])

    assert not result.has_errors
    assert result.summary["changed_counts"]["teacher"] == 2
    assert not any(
        field["object"] == "teacher:0002" and field["field"] == "phone" for field in result.change_summary["fields"]
    )
    commit = commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)
    assert commit.committed and commit.changed == 2

    teacher_one.refresh_from_db()
    teacher_two.refresh_from_db()
    assert teacher_one.formal_name == "更新后教师甲"
    assert teacher_one.department == "更新院系甲"
    assert teacher_one.phone == "old-phone"
    assert teacher_one.phone_visibility == "school_teachers"
    assert teacher_one.wechat == "roster-wechat"
    assert teacher_two.formal_name == "更新后教师乙"
    assert teacher_two.department == "更新院系乙"
    assert teacher_two.phone == "app-phone"
    assert teacher_two.phone_visibility == "course_teachers"
    assert teacher_two.wechat == "app-wechat"
    assert teacher_two.wechat_visibility == "private"

    session_one.refresh_from_db()
    session_two.refresh_from_db()
    assert session_one.version == version_one
    assert session_two.version == version_two
    assert ChangeLog.objects.filter(
        entity_type="session",
        entity_id=session_one.pk,
        change_seq__gt=log_count,
    ).exists()
    assert ChangeLog.objects.filter(
        entity_type="session",
        entity_id=session_two.pk,
        change_seq__gt=log_count,
    ).exists()


def test_term_name_update_writes_term_log_without_schedule_bump(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    term = Term.objects.get(code=TERM_CODE)
    original_version = term.schedule_version
    log_count = ChangeLog.objects.count()

    files = _copy_files(tmp_path)
    rows = read_csv(files["terms.csv"])
    rows[1][1] = "更新后的学期展示名"
    write_csv(files["terms.csv"], rows)
    batch, result = _preview_files(school, [files["terms.csv"]])
    assert not result.has_errors
    assert result.summary["changed_counts"]["term"] == 1

    commit = commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)
    assert commit.committed
    term.refresh_from_db()
    assert term.name == "更新后的学期展示名"
    assert term.schedule_version == original_version
    assert ChangeLog.objects.filter(entity_type="term", entity_id=term.pk, change_seq__gt=log_count).exists()


def test_term_name_with_new_rule_uses_latest_term_version_in_log(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    term_rows = read_csv(files["terms.csv"])
    term_rows[1][1] = "同一批次更新名称"
    write_csv(files["terms.csv"], term_rows)
    rule_rows = read_csv(files["rules.csv"])
    rule_rows.append(
        [
            "R-EXTRA-B12B",
            TERM_CODE,
            DATA_CODE,
            "2",
            "5",
            "6",
            "1",
            "0001",
            "B201",
            "2.0",
        ]
    )
    write_csv(files["rules.csv"], rule_rows)

    batch, result = _preview_files(school, [files["terms.csv"], files["rules.csv"]])
    assert not result.has_errors, result.errors
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed

    term = Term.objects.get(code=TERM_CODE)
    latest_term_log = ChangeLog.objects.filter(entity_type="term").order_by("-change_seq").first()
    assert latest_term_log is not None
    assert latest_term_log.version == term.schedule_version


def test_classroom_capacity_update_and_room_number_conflict(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    rows = read_csv(files["classrooms.csv"])
    rows[1][4] = "40"
    write_csv(files["classrooms.csv"], rows)
    batch, result = _preview_files(school, [files["classrooms.csv"]])
    assert not result.has_errors
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed
    assert Classroom.objects.get(classroom_code="A301").capacity == 40

    other = Classroom.objects.get(classroom_code="A302")
    conflict_files = _copy_files(tmp_path / "conflict")
    conflict_rows = read_csv(conflict_files["classrooms.csv"])
    conflict_rows[1][1] = other.campus
    conflict_rows[1][2] = other.building
    conflict_rows[1][3] = other.room_no
    write_csv(conflict_files["classrooms.csv"], conflict_rows)
    conflict_batch, conflict_result = _preview_files(school, [conflict_files["classrooms.csv"]])

    assert conflict_result.has_errors
    assert any(issue.code == "ROOM_NO_CONFLICT" for issue in conflict_result.issues)
    with pytest.raises(ImportCommitError):
        commit_batch(conflict_batch, academic_staff, school_date=SCHOOL_DATE)
    assert Classroom.objects.get(classroom_code="A301").room_no == "301"


@pytest.mark.parametrize("withdrawn", [True, False])
def test_b30_student_conflict_uses_enrollment_valid_on_target_date(
    school,
    academic_staff,
    tmp_path,
    withdrawn,
) -> None:
    _import_all(school, academic_staff)
    term = Term.objects.get(code=TERM_CODE)
    data_offering = CourseOffering.objects.get(offering_code=DATA_CODE)
    student_two_enrollment = Enrollment.objects.create(
        offering=data_offering,
        student_id=Enrollment.objects.get(pk=_imported_object_id("enrollment", "EN-000002-CRIME")).student_id,
        effective_from=date(2026, 9, 7),
        effective_until=date(2027, 1, 25),
    )
    assert student_two_enrollment.pk

    source = ClassSession.objects.get(
        rule__rule_code="R-DATA-MON",
        origin_teaching_date=date(2026, 11, 2),
    )
    target_room = Classroom.objects.get(classroom_code="A302")
    query = OptionQuery(
        date_from=date(2026, 11, 9),
        date_until=date(2026, 11, 10),
        classroom_ids=[target_room.pk],
    )
    now = datetime(2026, 10, 28, 8, 0, tzinfo=ZoneInfo(term.timezone))

    def option_count() -> int:
        result = find_reschedule_options(source, query, now=now)
        option = next(
            item
            for item in result.options
            if item.start_period == 1 and item.end_period == 2 and item.classroom_id == target_room.pk
        )
        return option.student_conflict_count

    assert option_count() == 2
    if not withdrawn:
        return

    files = _copy_files(tmp_path)
    rows = read_csv(files["enrollments.csv"])
    row = next(item for item in rows if item[0] == "EN-000002-CRIME")
    row[5] = "2026-11-02"
    write_csv(files["enrollments.csv"], rows)
    batch, result = _preview_files(school, [files["enrollments.csv"]])
    assert not result.has_errors
    assert result.summary["changed_counts"]["enrollment"] == 1
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed
    assert Enrollment.objects.get(pk=_imported_object_id("enrollment", "EN-000002-CRIME")).effective_until == date(
        2026, 11, 2
    )
    assert option_count() == 1


def test_enrollment_change_that_overlaps_another_record_is_rejected(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    enrollment = Enrollment.objects.get(pk=_imported_object_id("enrollment", "EN-000002-CRIME"))
    Enrollment.objects.filter(pk=enrollment.pk).update(effective_until=date(2027, 1, 1))
    Enrollment.objects.create(
        offering=enrollment.offering,
        student=enrollment.student,
        effective_from=date(2027, 1, 10),
        effective_until=date(2027, 2, 1),
    )

    files = _copy_files(tmp_path)
    rows = read_csv(files["enrollments.csv"])
    row = next(item for item in rows if item[0] == "EN-000002-CRIME")
    row[5] = "2027-01-15"
    write_csv(files["enrollments.csv"], rows)
    batch, result = _preview_files(school, [files["enrollments.csv"]])

    assert result.has_errors
    assert any(issue.code == "ENROLLMENT_OVERLAP" for issue in result.issues)
    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)
    enrollment.refresh_from_db()
    assert enrollment.effective_until == date(2027, 1, 1)


def test_add_teacher_to_existing_offering_bumps_scope_and_grants_visibility(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    teacher_rows = read_csv(files["teachers.csv"])
    teacher_rows.append(
        [
            "0003",
            "新增教师丙",
            "新增院系",
            "t0003@example.invalid",
            "",
            "private",
            "",
            "private",
            "true",
        ]
    )
    write_csv(files["teachers.csv"], teacher_rows)
    offering_rows = read_csv(files["offerings.csv"])
    crime_row = next(item for item in offering_rows if item[1] == CRIME_CODE)
    crime_row[8] = "0001|0002|0003"
    write_csv(files["offerings.csv"], offering_rows)

    batch, result = _preview_files(school, [files["teachers.csv"], files["offerings.csv"]])
    assert not result.has_errors, result.errors
    added = [
        item
        for item in result.change_summary["assignments"]
        if item["action"] == "added" and item["teacher_code"] == "0003"
    ]
    assert len(added) == 1
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed

    teacher = TeacherProfile.objects.get(teacher_no="0003")
    term = Term.objects.get(code=TERM_CODE)
    assignment = TeachingAssignment.objects.get(
        offering__offering_code=CRIME_CODE,
        teacher=teacher,
    )
    assert assignment.effective_from == term.start_date
    assert assignment.effective_until == term.end_date + timedelta(days=1)
    assert assignment.role == "teacher"
    assert teacher.assignment_scope_version == 2
    assert CRIME_CODE in _offering_codes_for(teacher)
    payload = _offering_payload(teacher)
    assert any(item["offering_code"] == CRIME_CODE for item in payload)


def test_remove_teacher_without_future_scheduled_sessions_ends_scope(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    teacher = TeacherProfile.objects.get(teacher_no="0002")
    term = Term.objects.get(code=TERM_CODE)
    assert DATA_CODE in _offering_codes_for(teacher)
    future_start = datetime.combine(SCHOOL_DATE, time.min, tzinfo=ZoneInfo(term.timezone))
    ClassSession.objects.filter(
        session_teachers__teacher=teacher,
        status=SessionStatus.SCHEDULED,
        start_at__gte=future_start,
    ).update(
        status=SessionStatus.CANCELLED,
        actual_hours=Decimal("0.0"),
        status_reason="虚构测试：取消未来课次",
    )

    files = _copy_files(tmp_path)
    offering_rows = read_csv(files["offerings.csv"])
    crime_row = next(item for item in offering_rows if item[1] == CRIME_CODE)
    crime_row[8] = "0001"
    write_csv(files["offerings.csv"], offering_rows)
    batch, result = _preview_files(school, [files["offerings.csv"]])

    assert not result.has_errors, result.errors
    ended = [
        item
        for item in result.change_summary["assignments"]
        if item["action"] == "ended" and item["teacher_code"] == "0002"
    ]
    assert len(ended) == 1
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed

    teacher.refresh_from_db()
    assignment = TeachingAssignment.objects.get(offering__offering_code=CRIME_CODE, teacher=teacher)
    assert assignment.effective_until == SCHOOL_DATE
    assert teacher.assignment_scope_version == 2
    assert _offering_codes_for(teacher) == set()
    assert _offering_payload(teacher) == []


def test_remove_teacher_with_future_scheduled_sessions_rejects_whole_batch(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    teacher_two = TeacherProfile.objects.get(teacher_no="0002")
    original_name = teacher_one.formal_name
    original_until = TeachingAssignment.objects.get(
        offering__offering_code=CRIME_CODE,
        teacher=teacher_two,
    ).effective_until

    files = _copy_files(tmp_path)
    teacher_rows = read_csv(files["teachers.csv"])
    teacher_rows[1][1] = "不应提交的教师甲"
    write_csv(files["teachers.csv"], teacher_rows)
    offering_rows = read_csv(files["offerings.csv"])
    crime_row = next(item for item in offering_rows if item[1] == CRIME_CODE)
    crime_row[8] = "0001"
    write_csv(files["offerings.csv"], offering_rows)
    batch, result = _preview_files(school, [files["teachers.csv"], files["offerings.csv"]])

    issue = next(
        issue
        for issue in result.issues
        if issue.file == "offerings.csv" and issue.field == "teacher_codes" and issue.code == "CHANGE_NOT_SUPPORTED_YET"
    )
    assert "未来 scheduled 课次" in issue.message
    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)

    teacher_one.refresh_from_db()
    assert teacher_one.formal_name == original_name
    assert (
        TeachingAssignment.objects.get(
            offering__offering_code=CRIME_CODE,
            teacher=teacher_two,
        ).effective_until
        == original_until
    )
    batch.refresh_from_db()
    assert batch.status != ImportStatus.COMMITTED


def test_lead_change_updates_assignments_and_plan_owner(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    offering = CourseOffering.objects.get(offering_code=CRIME_CODE)
    teacher_one = TeacherProfile.objects.get(teacher_no="0001")
    teacher_two = TeacherProfile.objects.get(teacher_no="0002")
    plan = TeachingPlan.objects.create(offering=offering, owner=teacher_one)
    version_one = teacher_one.assignment_scope_version
    version_two = teacher_two.assignment_scope_version

    files = _copy_files(tmp_path)
    offering_rows = read_csv(files["offerings.csv"])
    crime_row = next(item for item in offering_rows if item[1] == CRIME_CODE)
    crime_row[7] = "0002"
    write_csv(files["offerings.csv"], offering_rows)
    batch, result = _preview_files(school, [files["offerings.csv"]])

    assert not result.has_errors, result.errors
    actions = {(item["teacher_code"], item["action"]) for item in result.change_summary["assignments"]}
    assert ("0001", "role_changed") in actions
    assert ("0002", "role_changed") in actions
    assert ("0002", "plan_owner_changed") in actions
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed

    offering.refresh_from_db()
    plan.refresh_from_db()
    teacher_one.refresh_from_db()
    teacher_two.refresh_from_db()
    assert offering.lead_teacher_id == teacher_two.pk
    assert TeachingAssignment.objects.get(offering=offering, teacher=teacher_one).role == "teacher"
    assert TeachingAssignment.objects.get(offering=offering, teacher=teacher_two).role == "lead"
    assert plan.owner_id == teacher_two.pk
    assert teacher_one.assignment_scope_version == version_one + 1
    assert teacher_two.assignment_scope_version == version_two + 1


def test_course_class_and_room_updates_write_session_logs_without_version_bump(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    session = ClassSession.objects.filter(offering__offering_code=CRIME_CODE).first()
    assert session is not None
    version = session.version
    log_count = ChangeLog.objects.count()

    files = _copy_files(tmp_path)
    offering_rows = read_csv(files["offerings.csv"])
    for row in offering_rows[1:]:
        row[5] = "更新后的教学班名称"
    crime_row = next(item for item in offering_rows if item[1] == CRIME_CODE)
    crime_row[3] = "更新后的课程名称"
    write_csv(files["offerings.csv"], offering_rows)
    classroom_rows = read_csv(files["classrooms.csv"])
    classroom_rows[1][3] = "301A"
    write_csv(files["classrooms.csv"], classroom_rows)

    batch, result = _preview_files(school, [files["offerings.csv"], files["classrooms.csv"]])
    assert not result.has_errors, result.errors
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed

    offering = CourseOffering.objects.get(offering_code=CRIME_CODE)
    session.refresh_from_db()
    assert offering.course.name == "更新后的课程名称"
    assert offering.teaching_class.name == "更新后的教学班名称"
    assert Classroom.objects.get(classroom_code="A301").room_no == "301A"
    assert session.version == version
    assert ChangeLog.objects.filter(
        entity_type="session",
        entity_id=session.pk,
        version=version,
        change_seq__gt=log_count,
    ).exists()


def test_coverage_upgrade_warns_and_requires_confirmation_ref(
    client,
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    coverage = DataCoverage.objects.get(pk=_imported_object_id("coverage", "COV-CALENDAR"))
    coverage.completeness = CoverageCompleteness.PARTIAL
    coverage.confirmation_ref = ""
    coverage.save(update_fields=["completeness", "confirmation_ref", "updated_at"])

    files = _copy_files(tmp_path)
    batch, result = _preview_files(school, [files["coverage.csv"]])
    assert not result.has_errors
    assert any(issue.code == "COVERAGE_COMPLETENESS_UPGRADE" for issue in result.issues)
    assert any(item["level"] == "warning" for item in batch.errors_json)
    assert not batch.has_errors
    client.force_login(academic_staff)
    detail = client.get(reverse("admin:imports_importbatch_change", args=[batch.pk]))
    assert detail.status_code == 200
    assert "COVERAGE_COMPLETENESS_UPGRADE" in detail.content.decode()
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed
    coverage.refresh_from_db()
    assert coverage.completeness == CoverageCompleteness.COMPLETE

    missing_ref_files = _copy_files(tmp_path / "invalid")
    rows = read_csv(missing_ref_files["coverage.csv"])
    rows[1][8] = ""
    write_csv(missing_ref_files["coverage.csv"], rows)
    invalid_batch, invalid_result = _preview_files(school, [missing_ref_files["coverage.csv"]])
    assert invalid_result.has_errors
    assert any(issue.code == "REQUIRED" and issue.field == "confirmation_ref" for issue in invalid_result.issues)
    with pytest.raises(ImportCommitError):
        commit_batch(invalid_batch, academic_staff, school_date=SCHOOL_DATE)


def test_room_and_person_block_time_updates_are_mergeable(school, academic_staff, tmp_path) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    room_rows = read_csv(files["room_blocks.csv"])
    room_rows[1][3] = "2026-10-28T08:30:00+08:00"
    room_rows[1][4] = "2026-10-28T10:00:00+08:00"
    room_rows[1][5] = "更新后的维修说明"
    write_csv(files["room_blocks.csv"], room_rows)
    person_rows = read_csv(files["resource_unavailability.csv"])
    person_rows[1][6] = "更新后的会议说明"
    write_csv(files["resource_unavailability.csv"], person_rows)

    batch, result = _preview_files(
        school,
        [files["room_blocks.csv"], files["resource_unavailability.csv"]],
    )
    assert not result.has_errors, result.errors
    assert result.summary["changed_counts"]["room_block"] == 1
    assert result.summary["changed_counts"]["person_block"] == 1
    assert commit_batch(batch, academic_staff, school_date=SCHOOL_DATE).committed


@pytest.mark.parametrize(
    ("file_name", "mutate"),
    [
        ("terms.csv", lambda rows: rows[1].__setitem__(2, "2026-09-08")),
        (
            "rules.csv",
            lambda rows: rows[1].__setitem__(6, "1|2|3|5|7|9|11|13|15|17|19"),
        ),
        ("periods.csv", lambda rows: rows[1].__setitem__(2, "08:01")),
        (
            "calendar_exceptions.csv",
            lambda rows: rows.append(
                [
                    "CAL-NEW",
                    TERM_CODE,
                    "2026-11-02",
                    "cancel",
                    "",
                    "新增校历例外会改变已有课次",
                ]
            ),
        ),
        ("sessions.csv", lambda rows: rows[1].__setitem__(11, "B201")),
    ],
)
def test_schedule_affecting_changes_are_rejected(
    school,
    academic_staff,
    tmp_path,
    file_name,
    mutate,
) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    rows = read_csv(files[file_name])
    mutate(rows)
    write_csv(files[file_name], rows)
    batch, result = _preview_files(school, [files[file_name]])

    assert result.has_errors
    assert any(issue.code == "CHANGE_NOT_SUPPORTED_YET" for issue in result.issues), result.errors
    with pytest.raises(ImportCommitError):
        commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)


def test_preview_is_stale_when_same_teacher_changes_before_commit(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    rows = read_csv(files["teachers.csv"])
    rows[1][1] = "预览中的教师甲"
    write_csv(files["teachers.csv"], rows)
    batch, result = _preview_files(school, [files["teachers.csv"]])
    assert not result.has_errors
    teacher = TeacherProfile.objects.get(teacher_no="0001")
    TeacherProfile.objects.filter(pk=teacher.pk).update(
        formal_name="预览后并发修改",
        assignment_scope_version=9,
    )
    log_count = ChangeLog.objects.count()

    commit = commit_batch(batch, academic_staff, school_date=SCHOOL_DATE)

    assert not commit.committed
    assert commit.code == "PREVIEW_STALE"
    batch.refresh_from_db()
    assert batch.status == ImportStatus.UPLOADED
    teacher.refresh_from_db()
    assert teacher.formal_name == "预览后并发修改"
    assert teacher.assignment_scope_version == 9
    assert ChangeLog.objects.count() == log_count


def test_reapplying_same_merge_update_is_all_unchanged_and_writes_no_log(
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    rows = read_csv(files["teachers.csv"])
    rows[1][1] = "重复上传应保持稳定"
    write_csv(files["teachers.csv"], rows)
    first_batch, first_preview = _preview_files(school, [files["teachers.csv"]])
    assert not first_preview.has_errors
    assert commit_batch(first_batch, academic_staff, school_date=SCHOOL_DATE).committed
    log_count = ChangeLog.objects.count()

    second_batch, second_preview = _preview_files(school, [files["teachers.csv"]])
    assert not second_preview.has_errors
    assert second_preview.summary["changed_counts"] == {}
    assert second_preview.row_counts["teachers.csv"]["unchanged"] == 2
    second_commit = commit_batch(second_batch, academic_staff, school_date=SCHOOL_DATE)
    assert second_commit.committed and second_commit.changed == 0
    assert ChangeLog.objects.count() == log_count


def test_admin_renders_change_detail_and_assignment_changes(
    client,
    school,
    academic_staff,
    tmp_path,
) -> None:
    _import_all(school, academic_staff)
    files = _copy_files(tmp_path)
    rows = read_csv(files["teachers.csv"])
    rows[1][1] = "管理后台可见的新姓名"
    write_csv(files["teachers.csv"], rows)
    batch, result = _preview_files(school, [files["teachers.csv"]])
    assert not result.has_errors
    client.force_login(academic_staff)

    response = client.get(reverse("admin:imports_importbatch_change", args=[batch.pk]))

    assert response.status_code == 200
    rendered = response.content.decode()
    assert "变更明细" in rendered
    assert "管理后台可见的新姓名" in rendered
    assert "任课范围变化" in rendered
