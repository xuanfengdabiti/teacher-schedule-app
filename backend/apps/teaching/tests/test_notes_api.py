"""B20 note API, permission, version and idempotency acceptance tests."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext

from apps.academics.models import AssignmentRole, TeacherProfile, TeachingAssignment
from apps.scheduling.models import (
    ChangeEntityType,
    ChangeLog,
    ClassSession,
    SchoolSyncState,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.tests import factories
from apps.teaching.models import ClientMutation, TeachingNote
from apps.teaching.services import save_draft
from apps.teaching.tests.helpers import (
    FIXED_NOW,
    auth_client,
    complete_session,
    get_notes,
    link_user,
    note_payload,
    publish_note,
    save_note,
    session_for,
)

pytestmark = pytest.mark.django_db


def _add_data_only_teacher(demo):
    teacher = factories.make_teacher(demo.school, "B20-DATA", "只任课数据分析的示例教师")
    TeachingAssignment.objects.create(
        offering=demo.offerings["OFF-DATA-2026A"],
        teacher=teacher,
        role=AssignmentRole.TEACHER,
        effective_from=demo.term.start_date,
        effective_until=demo.term.end_date,
    )
    return teacher


def _make_extra_session(demo, *, day: date, status: str) -> ClassSession:
    tz = demo.term.tzinfo
    start_at = datetime.combine(day, time(18, 0), tzinfo=tz)
    end_at = datetime.combine(day, time(19, 30), tzinfo=tz)
    session = ClassSession.objects.create(
        offering=demo.offerings["OFF-CRIME-2026A"],
        start_period=5,
        end_period=6,
        start_at=start_at,
        end_at=end_at,
        classroom=demo.classrooms["A301"],
        status=status,
        planned_hours=Decimal("2.0"),
        actual_hours=Decimal("0") if status == SessionStatus.CANCELLED else None,
    )
    SessionTeacher.objects.create(session=session, teacher=demo.teachers["0001"])
    return session


def _publish_with_note(client, session: ClassSession, *, key: str, covered: str):
    saved = save_note(
        client,
        session,
        covered=covered,
        unfinished="未完成",
        next_plan="下次",
        base_version=0,
        client_mutation_id=f"{key}-draft",
    )
    assert saved.status_code == 200, saved.data
    published = publish_note(
        client,
        session,
        base_version=saved.data["version"],
        client_mutation_id=f"{key}-publish",
    )
    assert published.status_code == 200, published.data
    return published


class TestA25PrivateDraftAndPublication:
    def test_shared_teacher_sees_only_published_snapshot_and_same_class_teacher_is_denied(
        self,
        generated_demo,
    ) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        data_only = _add_data_only_teacher(demo)
        user1 = link_user(demo.teachers["0001"], "a25-0001@example.invalid")
        user2 = link_user(demo.teachers["0002"], "a25-0002@example.invalid")
        user_data = link_user(data_only, "a25-data@example.invalid")
        client1 = auth_client(user1)
        client2 = auth_client(user2)
        client_data = auth_client(user_data)

        draft = save_note(
            client2,
            session,
            covered="A25 私有草稿正文",
            unfinished="未完成",
            next_plan="下次计划",
            base_version=0,
            client_mutation_id="a25-draft-1",
        )
        assert draft.status_code == 200
        assert draft.data["version"] == 1
        assert draft.data["published"] is None

        before_publish = get_notes(client1, session)
        assert before_publish.status_code == 200
        assert before_publish.data["mine"] is None
        assert before_publish.data["others"] == []
        assert "A25 私有草稿正文" not in json.dumps(before_publish.data, ensure_ascii=False)

        published = publish_note(
            client2,
            session,
            base_version=draft.data["version"],
            client_mutation_id="a25-publish-1",
        )
        assert published.status_code == 200
        assert published.data["version"] == 2
        assert published.data["published"]["covered"] == "A25 私有草稿正文"

        after_publish = get_notes(client1, session)
        assert after_publish.status_code == 200
        assert len(after_publish.data["others"]) == 1
        assert after_publish.data["others"][0]["author"]["id"] == str(demo.teachers["0002"].id)
        assert after_publish.data["others"][0]["published"]["covered"] == "A25 私有草稿正文"

        edited = save_note(
            client2,
            session,
            covered="A25 第二轮私有草稿",
            unfinished="未完成 2",
            next_plan="下次 2",
            base_version=published.data["version"],
            client_mutation_id="a25-draft-2",
        )
        assert edited.status_code == 200
        assert edited.data["version"] == 3
        assert edited.data["published"]["covered"] == "A25 私有草稿正文"

        after_edit = get_notes(client1, session)
        assert after_edit.status_code == 200
        assert after_edit.data["others"][0]["published"]["covered"] == "A25 私有草稿正文"
        assert "A25 第二轮私有草稿" not in json.dumps(after_edit.data, ensure_ascii=False)

        denied = get_notes(client_data, session)
        assert denied.status_code == 403
        assert denied.data["code"] == "NOT_OFFERING_TEACHER"
        assert "A25 私有草稿正文" not in json.dumps(denied.data, ensure_ascii=False)


class TestA10PreviousRecord:
    def test_previous_uses_completed_session_time_not_publication_time_or_offering(self, plain_demo) -> None:
        demo = plain_demo
        session_1019 = session_for(demo, "R-CRIME-ODD", date(2026, 10, 19))
        session_1005 = session_for(demo, "R-CRIME-ODD", date(2026, 10, 5))
        session_1012 = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 12))
        session_1026 = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        complete_session(session_1019, demo.teachers["0001"])
        complete_session(session_1005, demo.teachers["0001"])
        user1 = link_user(demo.teachers["0001"], "a10-0001@example.invalid")
        client = auth_client(user1)

        _publish_with_note(client, session_1019, key="a10-1019", covered="10 月 19 日记录")
        _publish_with_note(client, session_1005, key="a10-1005", covered="补录 10 月 5 日记录")

        oct26 = get_notes(client, session_1026)
        assert oct26.status_code == 200
        assert oct26.data["previous"]["session"]["id"] == str(session_1019.id)
        assert oct26.data["previous"]["session"]["date"] == "2026-10-19"
        assert oct26.data["previous"]["notes"][0]["published"]["covered"] == "10 月 19 日记录"

        oct12 = get_notes(client, session_1012)
        assert oct12.status_code == 200
        assert oct12.data["previous"]["session"]["id"] == str(session_1005.id)
        assert oct12.data["previous"]["session"]["date"] == "2026-10-05"

        scheduled = _make_extra_session(demo, day=date(2026, 10, 20), status=SessionStatus.SCHEDULED)
        cancelled = _make_extra_session(demo, day=date(2026, 10, 21), status=SessionStatus.CANCELLED)
        _publish_with_note(client, scheduled, key="a10-scheduled", covered="未完成课次不应成为上次")
        _publish_with_note(client, cancelled, key="a10-cancelled", covered="取消课次不应成为上次")

        data_session = session_for(demo, "R-DATA-MON", date(2026, 10, 19))
        _publish_with_note(client, data_session, key="a10-data", covered="数据分析课不应串联")

        oct26_after = get_notes(client, session_1026)
        assert oct26_after.status_code == 200
        assert oct26_after.data["previous"]["session"]["id"] == str(session_1019.id)
        assert oct26_after.data["previous"]["session"]["course_name"] == "刑事侦查专题研究（示例）"
        assert all(
            note["published"]["covered"] != "未完成课次不应成为上次"
            and note["published"]["covered"] != "取消课次不应成为上次"
            and note["published"]["covered"] != "数据分析课不应串联"
            for note in oct26_after.data["previous"]["notes"]
        )

    def test_completed_session_with_only_blank_draft_is_not_previous(self, plain_demo) -> None:
        demo = plain_demo
        session_1019 = session_for(demo, "R-CRIME-ODD", date(2026, 10, 19))
        session_1026 = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        complete_session(session_1019, demo.teachers["0001"])
        user1 = link_user(demo.teachers["0001"], "a10-empty-draft@example.invalid")
        client = auth_client(user1)
        _publish_with_note(client, session_1019, key="a10-empty-real", covered="真正的上次记录")

        blank_completed = _make_extra_session(demo, day=date(2026, 10, 20), status=SessionStatus.SCHEDULED)
        complete_session(blank_completed, demo.teachers["0001"])
        saved = save_note(
            client,
            blank_completed,
            covered="  ",
            unfinished="\n",
            next_plan=None,
            base_version=0,
            client_mutation_id="a10-empty-draft",
        )
        assert saved.status_code == 200
        assert saved.data["published"] is None
        rejected = publish_note(
            client,
            blank_completed,
            base_version=1,
            client_mutation_id="a10-empty-publish",
        )
        assert rejected.status_code == 409
        assert rejected.data["code"] == "NOTHING_TO_PUBLISH"

        response = get_notes(client, session_1026)

        assert response.status_code == 200
        assert response.data["previous"]["session"]["id"] == str(session_1019.id)
        assert response.data["previous"]["session"]["date"] == "2026-10-19"
        assert response.data["previous"]["notes"][0]["published"]["covered"] == "真正的上次记录"


class TestA23Idempotency:
    def test_same_mutation_returns_stored_response_and_does_not_publish(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "a23-0002@example.invalid")
        user1 = link_user(demo.teachers["0001"], "a23-0001@example.invalid")
        client2 = auth_client(user2)
        client1 = auth_client(user1)
        payload = note_payload(
            covered="A23 第一次请求正文",
            unfinished="未完成",
            next_plan="下次",
            base_version=0,
            client_mutation_id="a23-same-key",
        )

        first = client2.put(f"/api/v1/sessions/{session.id}/notes/mine/", payload, format="json")
        assert first.status_code == 200
        note = TeachingNote.objects.get(session=session, author=demo.teachers["0002"])
        updated_at = note.updated_at
        logs_before = ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count()
        mutations_before = ClientMutation.objects.count()

        second = client2.put(f"/api/v1/sessions/{session.id}/notes/mine/", payload, format="json")
        assert second.status_code == 200
        assert second.data == first.data
        note.refresh_from_db()
        assert note.version == 1
        assert note.updated_at == updated_at
        assert note.published_at is None
        assert ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count() == logs_before == 1
        assert ClientMutation.objects.count() == mutations_before == 1
        assert get_notes(client1, session).data["others"] == []

        changed = {**payload, "covered": "A23 同 key 不同正文"}
        reused = client2.put(f"/api/v1/sessions/{session.id}/notes/mine/", changed, format="json")
        assert reused.status_code == 409
        assert reused.data["code"] == "MUTATION_KEY_REUSED"
        assert reused.data["current"]["version"] == 1
        assert reused.data["current"]["draft"]["covered"] == "A23 第一次请求正文"
        note.refresh_from_db()
        assert note.version == 1
        assert note.draft_covered == "A23 第一次请求正文"

        publish_payload = {"base_version": 1, "client_mutation_id": "a23-publish-key"}
        published = client2.post(
            f"/api/v1/sessions/{session.id}/notes/mine/publish/",
            publish_payload,
            format="json",
        )
        assert published.status_code == 200
        assert published.data["version"] == 2
        published_at = published.data["published_at"]
        repeated_publish = client2.post(
            f"/api/v1/sessions/{session.id}/notes/mine/publish/",
            publish_payload,
            format="json",
        )
        assert repeated_publish.status_code == 200
        assert repeated_publish.data == published.data
        note.refresh_from_db()
        assert note.version == 2
        assert note.published_at is not None
        assert published_at == repeated_publish.data["published_at"]
        assert ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count() == 2

        reused_publish = client2.post(
            f"/api/v1/sessions/{session.id}/notes/mine/publish/",
            {"base_version": 0, "client_mutation_id": "a23-publish-key"},
            format="json",
        )
        assert reused_publish.status_code == 409
        assert reused_publish.data["code"] == "MUTATION_KEY_REUSED"


class TestPublishRequiresContent:
    def test_publish_without_note_is_409_and_writes_nothing(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "empty-publish-none@example.invalid")
        client2 = auth_client(user2)
        last_seq = SchoolSyncState.objects.get(school=demo.school).last_seq

        response = publish_note(
            client2,
            session,
            base_version=0,
            client_mutation_id="empty-publish-no-note",
        )

        assert response.status_code == 409
        assert response.data["code"] == "NOTHING_TO_PUBLISH"
        assert response.data["current"] is None
        assert not TeachingNote.objects.exists()
        assert not ClientMutation.objects.exists()
        assert not ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).exists()
        assert SchoolSyncState.objects.get(school=demo.school).last_seq == last_seq

    def test_publish_with_all_blank_drafts_is_409_and_writes_nothing(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "empty-publish-blank@example.invalid")
        client2 = auth_client(user2)
        saved = save_note(
            client2,
            session,
            covered="   ",
            unfinished="\n\t ",
            next_plan=None,
            base_version=0,
            client_mutation_id="empty-publish-blank-draft",
        )
        assert saved.status_code == 200
        note = TeachingNote.objects.get(session=session, author=demo.teachers["0002"])
        updated_at = note.updated_at
        last_seq = SchoolSyncState.objects.get(school=demo.school).last_seq

        response = publish_note(
            client2,
            session,
            base_version=1,
            client_mutation_id="empty-publish-blank-publish",
        )

        assert response.status_code == 409
        assert response.data["code"] == "NOTHING_TO_PUBLISH"
        assert response.data["current"]["version"] == 1
        note.refresh_from_db()
        assert note.version == 1
        assert note.published_at is None
        assert note.updated_at == updated_at
        assert ClientMutation.objects.count() == 1
        assert ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count() == 1
        assert SchoolSyncState.objects.get(school=demo.school).last_seq == last_seq

    def test_publish_after_clearing_previously_published_drafts_is_409_and_keeps_snapshot(
        self,
        generated_demo,
    ) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "empty-publish-cleared@example.invalid")
        client2 = auth_client(user2)
        initial = save_note(
            client2,
            session,
            covered="已发布正文",
            unfinished="已发布未完成",
            next_plan="已发布计划",
            base_version=0,
            client_mutation_id="empty-publish-initial",
        )
        assert initial.status_code == 200
        published = publish_note(
            client2,
            session,
            base_version=1,
            client_mutation_id="empty-publish-first",
        )
        assert published.status_code == 200
        assert published.data["version"] == 2
        note = TeachingNote.objects.get(session=session, author=demo.teachers["0002"])
        published_at = note.published_at
        cleared = save_note(
            client2,
            session,
            covered="",
            unfinished=" ",
            next_plan=None,
            base_version=2,
            client_mutation_id="empty-publish-clear",
        )
        assert cleared.status_code == 200
        assert cleared.data["version"] == 3
        last_seq = SchoolSyncState.objects.get(school=demo.school).last_seq
        mutation_count = ClientMutation.objects.count()
        log_count = ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count()

        response = publish_note(
            client2,
            session,
            base_version=3,
            client_mutation_id="empty-publish-cleared",
        )

        assert response.status_code == 409
        assert response.data["code"] == "NOTHING_TO_PUBLISH"
        assert response.data["current"]["version"] == 3
        assert response.data["current"]["published"]["covered"] == "已发布正文"
        note.refresh_from_db()
        assert note.version == 3
        assert note.published_version == 2
        assert note.published_covered == "已发布正文"
        assert note.published_at == published_at
        assert ClientMutation.objects.count() == mutation_count
        assert ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count() == log_count
        assert SchoolSyncState.objects.get(school=demo.school).last_seq == last_seq


class TestA18VersionConflict:
    def test_second_device_conflict_returns_server_texts_and_keeps_them(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "a18-0002@example.invalid")
        client_a = auth_client(user2)
        client_b = auth_client(user2)

        initial = save_note(
            client_a,
            session,
            covered="共享基线",
            unfinished="基线未完成",
            next_plan="基线计划",
            base_version=0,
            client_mutation_id="a18-base",
        )
        assert initial.status_code == 200
        assert initial.data["version"] == 1

        device_a = save_note(
            client_a,
            session,
            covered="设备 A 新正文",
            unfinished="A 未完成",
            next_plan="A 计划",
            base_version=1,
            client_mutation_id="a18-device-a",
        )
        assert device_a.status_code == 200
        assert device_a.data["version"] == 2

        device_b = save_note(
            client_b,
            session,
            covered="设备 B 本地正文",
            unfinished="B 未完成",
            next_plan="B 计划",
            base_version=1,
            client_mutation_id="a18-device-b",
        )
        assert device_b.status_code == 409
        assert device_b.data["code"] == "NOTE_VERSION_CONFLICT"
        assert device_b.data["current"]["version"] == 2
        assert device_b.data["current"]["draft"] == {
            "covered": "设备 A 新正文",
            "unfinished": "A 未完成",
            "next_plan": "A 计划",
        }
        note = TeachingNote.objects.get(session=session, author=demo.teachers["0002"])
        assert note.version == 2
        assert note.draft_covered == "设备 A 新正文"
        assert not TeachingNote.objects.filter(draft_covered="设备 B 本地正文").exists()
        assert ClientMutation.objects.filter(client_mutation_id="a18-device-b").exists() is False


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_client_mutation_writes_once_and_returns_same_result(generated_demo) -> None:
    demo = generated_demo
    session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
    link_user(demo.teachers["0002"], "a23-concurrent@example.invalid")
    barrier = threading.Barrier(2)

    def worker() -> dict:
        close_old_connections()
        try:
            teacher = TeacherProfile.objects.select_related("user").get(pk=demo.teachers["0002"].pk)
            locked_session = ClassSession.objects.select_related(
                "offering__term",
                "offering__course",
            ).get(pk=session.pk)
            barrier.wait(timeout=10)
            return save_draft(
                teacher,
                locked_session,
                covered="并发同一请求",
                unfinished="未完成",
                next_plan="下次",
                base_version=0,
                client_mutation_id="a23-concurrent-key",
                now=FIXED_NOW,
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=30) for future in (executor.submit(worker), executor.submit(worker))]

    assert results[0] == results[1]
    assert results[0]["version"] == 1
    assert TeachingNote.objects.count() == 1
    assert ClientMutation.objects.count() == 1
    assert ChangeLog.objects.filter(entity_type=ChangeEntityType.NOTE).count() == 1


class TestPermissionsAndVisibility:
    def test_write_and_read_are_limited_to_actual_session_teachers_and_offering_teachers(
        self,
        generated_demo,
    ) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        data_only = _add_data_only_teacher(demo)
        user1 = link_user(demo.teachers["0001"], "permission-0001@example.invalid")
        user_data = link_user(data_only, "permission-data@example.invalid")

        write_denied = save_note(
            auth_client(user1),
            session,
            covered="不应写入",
            unfinished="",
            next_plan="",
            base_version=0,
            client_mutation_id="permission-write-denied",
        )
        assert write_denied.status_code == 403
        assert write_denied.data["code"] == "NOT_SESSION_TEACHER"
        assert write_denied.data["field_errors"] == {}

        read_denied = get_notes(auth_client(user_data), session)
        assert read_denied.status_code == 403
        assert read_denied.data["code"] == "NOT_OFFERING_TEACHER"

    def test_other_draft_is_absent_from_every_reading_path(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user1 = link_user(demo.teachers["0001"], "leak-0001@example.invalid")
        user2 = link_user(demo.teachers["0002"], "leak-0002@example.invalid")
        save_note(
            auth_client(user2),
            session,
            covered="他人的秘密草稿",
            unfinished="秘密未完成",
            next_plan="秘密计划",
            base_version=0,
            client_mutation_id="leak-draft",
        )

        response = get_notes(auth_client(user1), session)
        assert response.status_code == 200
        assert "他人的秘密草稿" not in json.dumps(response.data, ensure_ascii=False)
        assert "秘密未完成" not in json.dumps(response.data, ensure_ascii=False)

    def test_invisible_session_is_404_and_teacher_status_codes_are_preserved(
        self,
        generated_demo,
    ) -> None:
        demo = generated_demo
        outsider = factories.make_teacher(demo.school, "B20-OUTSIDE", "不可见课次教师")
        outside_offering = factories.make_offering(
            demo.term,
            outsider,
            offering_code="OFF-B20-OUTSIDE",
            course_code="C-B20-OUTSIDE",
            course_name="不可见课程",
            class_code="TC-B20-OUTSIDE",
            class_name="不可见教学班",
        )
        tz = demo.term.tzinfo
        outside_session = ClassSession.objects.create(
            offering=outside_offering,
            start_period=1,
            end_period=2,
            start_at=datetime(2026, 10, 27, 8, 0, tzinfo=tz),
            end_at=datetime(2026, 10, 27, 9, 40, tzinfo=tz),
            classroom=demo.classrooms["B201"],
            planned_hours=Decimal("2.0"),
        )
        SessionTeacher.objects.create(session=outside_session, teacher=outsider)
        user1 = link_user(demo.teachers["0001"], "invisible-0001@example.invalid")
        client = auth_client(user1)

        read = get_notes(client, outside_session)
        write = save_note(
            client,
            outside_session,
            covered="",
            unfinished="",
            next_plan="",
            base_version=0,
            client_mutation_id="invisible-write",
        )
        assert read.status_code == 404
        assert write.status_code == 404
        assert read.data["code"] == "NOT_FOUND"
        assert write.data["code"] == "NOT_FOUND"

        unlinked = get_user_model().objects.create_user(
            email="unlinked-note@example.invalid",
            password="test-pass-123",
        )
        unlinked_response = get_notes(auth_client(unlinked), outside_session)
        assert unlinked_response.status_code == 403
        assert unlinked_response.data["code"] == "TEACHER_NOT_LINKED"

        disabled_user = link_user(demo.teachers["0002"], "disabled-note@example.invalid")
        demo.teachers["0002"].is_active = False
        demo.teachers["0002"].teacher_status = "disabled"
        demo.teachers["0002"].save(update_fields=["is_active", "teacher_status", "updated_at"])
        disabled_response = get_notes(auth_client(disabled_user), outside_session)
        assert disabled_response.status_code == 403
        assert disabled_response.data["code"] == "TEACHER_DISABLED"


class TestWriteBoundariesAndValidation:
    def test_note_save_and_publish_do_not_change_session_completion_or_hours(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "session-unchanged@example.invalid")
        client = auth_client(user2)
        session.refresh_from_db()
        before = (session.status, session.actual_hours, session.version)

        saved = save_note(
            client,
            session,
            covered="只保存草稿",
            unfinished="",
            next_plan="",
            base_version=0,
            client_mutation_id="unchanged-draft",
        )
        assert saved.status_code == 200
        session.refresh_from_db()
        assert (session.status, session.actual_hours, session.version) == before

        published = publish_note(
            client,
            session,
            base_version=1,
            client_mutation_id="unchanged-publish",
        )
        assert published.status_code == 200
        session.refresh_from_db()
        assert (session.status, session.actual_hours, session.version) == before
        assert session.status == SessionStatus.SCHEDULED
        assert session.actual_hours is None
        assert session.completed_at is None

    def test_text_over_5000_is_400_and_exactly_5000_is_accepted(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user2 = link_user(demo.teachers["0002"], "length-note@example.invalid")
        client = auth_client(user2)

        too_long = save_note(
            client,
            session,
            covered="x" * 5001,
            unfinished="",
            next_plan="",
            base_version=0,
            client_mutation_id="too-long",
        )
        assert too_long.status_code == 400
        assert too_long.data["code"] == "VALIDATION_ERROR"
        assert "covered" in too_long.data["field_errors"]
        assert TeachingNote.objects.count() == 0

        boundary = save_note(
            client,
            session,
            covered="x" * 5000,
            unfinished="",
            next_plan="",
            base_version=0,
            client_mutation_id="exact-limit",
        )
        assert boundary.status_code == 200
        assert len(boundary.data["draft"]["covered"]) == 5000

        publish_without_key = client.post(
            f"/api/v1/sessions/{session.id}/notes/mine/publish/",
            {"base_version": 1},
            format="json",
        )
        assert publish_without_key.status_code == 400
        assert "client_mutation_id" in publish_without_key.data["field_errors"]


class TestReadQueryCounts:
    def test_previous_record_query_count_is_constant_as_authors_grow(self, plain_demo) -> None:
        demo = plain_demo
        session_1019 = session_for(demo, "R-CRIME-ODD", date(2026, 10, 19))
        session_1026 = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        complete_session(session_1019, demo.teachers["0001"])
        user1 = link_user(demo.teachers["0001"], "query-note-0001@example.invalid")
        client = auth_client(user1)
        _publish_with_note(client, session_1019, key="query-1019", covered="第一条")
        tz = demo.term.tzinfo

        def count_queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                response = get_notes(client, session_1026)
            assert response.status_code == 200
            return len(captured)

        before = count_queries()
        for index, teacher_no in enumerate(("B20-Q1", "B20-Q2", "B20-Q3"), start=1):
            teacher = factories.make_teacher(demo.school, teacher_no, f"查询数示例教师{index}")
            SessionTeacher.objects.create(session=session_1019, teacher=teacher)
            TeachingNote.objects.create(
                session=session_1019,
                author=teacher,
                published_covered=f"作者 {index}",
                published_at=datetime(2026, 10, 19, 12, 0, tzinfo=tz),
                published_version=1,
                version=1,
            )
        after = count_queries()
        response = get_notes(client, session_1026)
        assert len(response.data["previous"]["notes"]) == 4
        print(f"notes+previous GET queries: {before} -> {after}")
        assert before == after
