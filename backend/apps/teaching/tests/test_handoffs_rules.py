"""B21 handoff rules: multi-recipient, eligibility, note binding and prefill."""

from __future__ import annotations

from datetime import date

import pytest

from apps.notifications.models import Notification, NotificationType
from apps.scheduling.models import ChangeLog, ClassSession, SessionStatus, SessionTeacher
from apps.teaching.models import ClientMutation, Handoff, TeachingNote
from apps.teaching.tests.handoff_helpers import (
    CRIME_NEXT_DAY,
    add_teacher,
    crime_source,
    next_scheduled_session,
    publish_own_note,
    send_handoff,
)
from apps.teaching.tests.helpers import auth_client, link_user, publish_note, save_note, session_for

pytestmark = pytest.mark.django_db


def _crime_next_session(demo):
    return session_for(demo, "R-CRIME-ODD", CRIME_NEXT_DAY)


def _handoff_counts() -> tuple[int, int, int]:
    return (
        Handoff.objects.count(),
        Notification.objects.filter(type=NotificationType.HANDOFF_RECEIVED).count(),
        Notification.objects.filter(type=NotificationType.HANDOFF_ACKNOWLEDGED).count(),
    )


def test_multiple_recipients_create_independent_handoffs_and_messages(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = _crime_next_session(flow.demo)
    third = add_teacher(flow.demo, "B21-003")

    response = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2, third],
        content="多接收人交接正文",
        source_session=source,
        target_session=next_scheduled_session(source),
    )
    assert response.status_code == 201, response.content
    results = response.json()
    assert len(results) == 2
    assert {item["recipient"]["id"] for item in results} == {str(flow.teacher2.id), str(third.id)}
    assert len({item["id"] for item in results}) == 2

    handoffs = list(Handoff.objects.order_by("created_at"))
    assert len(handoffs) == 2
    assert {handoff.recipient_id for handoff in handoffs} == {flow.teacher2.id, third.id}
    assert all(handoff.status == "pending" for handoff in handoffs)
    for handoff in handoffs:
        notice = Notification.objects.get(
            recipient_id=handoff.recipient_id,
            type=NotificationType.HANDOFF_RECEIVED,
            object_id=handoff.id,
        )
        assert "多接收人交接正文" in notice.body

    # Duplicate IDs are one recipient, not two rows.
    duplicate = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2, flow.teacher2],
        content="去重交接",
        source_session=source,
    )
    assert duplicate.status_code == 201, duplicate.content
    assert len(duplicate.json()) == 1
    assert Handoff.objects.filter(content_snapshot="去重交接").count() == 1


def test_recipient_without_an_account_still_gets_an_in_app_message(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = _crime_next_session(flow.demo)
    no_account = add_teacher(flow.demo, "B21-NO-ACCOUNT")

    response = send_handoff(
        flow.client1,
        crime,
        [no_account],
        content="无账号接收人",
        source_session=source,
    )
    assert response.status_code == 201, response.content
    notice = Notification.objects.get(
        recipient=no_account,
        type=NotificationType.HANDOFF_RECEIVED,
    )
    assert "无账号接收人" in notice.body
    assert notice.delivery_attempts.count() == 0


def test_one_invalid_recipient_aborts_the_whole_send(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = _crime_next_session(flow.demo)
    data_only = add_teacher(flow.demo, "B21-DATA-ONLY", offering_key="OFF-DATA-2026A")
    before = _handoff_counts()

    response = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2, data_only],
        content="不应创建",
        source_session=source,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RECIPIENT"
    assert "recipient_ids" in response.json()["field_errors"]
    assert _handoff_counts() == before

    self_send = send_handoff(
        flow.client1,
        crime,
        [flow.teacher1, flow.teacher2],
        content="不应创建",
        source_session=source,
    )
    assert self_send.status_code == 400
    assert self_send.json()["code"] == "INVALID_RECIPIENT"
    assert _handoff_counts() == before


def test_recipient_must_be_current_active_offering_teacher(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = _crime_next_session(flow.demo)

    same_class_other_course = add_teacher(flow.demo, "B21-SAME-CLASS", offering_key="OFF-DATA-2026A")
    response = send_handoff(
        flow.client1,
        crime,
        [same_class_other_course],
        content="同班不同课",
        source_session=source,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RECIPIENT"

    ended = add_teacher(flow.demo, "B21-ENDED", ended=True)
    response = send_handoff(
        flow.client1,
        crime,
        [ended],
        content="任课已结束",
        source_session=source,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RECIPIENT"

    active = add_teacher(flow.demo, "B21-ACTIVE")
    response = send_handoff(
        flow.client1,
        crime,
        [active],
        content="当前任课有效",
        source_session=source,
    )
    assert response.status_code == 201, response.content


def test_note_must_be_senders_published_note_on_the_source_session(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(flow.demo)  # taught by 0002
    sender_session = _crime_next_session(flow.demo)  # taught by 0001
    data_session = session_for(flow.demo, "R-DATA-MON", date(2026, 10, 12))

    other_note = publish_own_note(
        flow.client2,
        source,
        flow.teacher2,
        key="other-note",
        unfinished="他人备注",
        next_plan="他人计划",
    )
    response = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="关联他人备注",
        source_session=source,
        note=other_note,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTE"
    assert "note_id" in response.json()["field_errors"]

    draft = save_note(
        flow.client1,
        sender_session,
        covered="草稿已讲",
        unfinished="草稿未完成",
        next_plan="草稿计划",
        base_version=0,
        client_mutation_id="b21-draft",
    )
    assert draft.status_code == 200
    draft_note = TeachingNote.objects.get(session=sender_session, author=flow.teacher1)
    response = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="关联未发布备注",
        source_session=sender_session,
        note=draft_note,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTE"

    data_note = publish_own_note(
        flow.client1,
        data_session,
        flow.teacher1,
        key="data-note",
        unfinished="数据课未完成",
        next_plan="数据课计划",
    )
    response = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="关联其他开设备注",
        source_session=sender_session,
        note=data_note,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTE"

    response = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="缺少来源课次",
        note=data_note,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTE"

    # The only accepted combination is the sender's own published note on the
    # source session; the snapshot version is fixed at send time.
    published = publish_note(
        flow.client1,
        sender_session,
        base_version=draft_note.version,
        client_mutation_id="b21-publish",
    )
    assert published.status_code == 200, published.content
    sender_note = TeachingNote.objects.get(pk=draft_note.pk)
    valid = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="有效关联备注",
        source_session=sender_session,
        note=sender_note,
    )
    assert valid.status_code == 201, valid.content
    item = valid.json()[0]
    assert item["note_published_version"] == sender_note.published_version
    assert item["content_snapshot"] == "有效关联备注"


def test_source_and_target_sessions_must_belong_to_the_offering(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = _crime_next_session(flow.demo)
    data_session = session_for(flow.demo, "R-DATA-MON", date(2026, 10, 12))
    before = _handoff_counts()

    bad_source = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="跨开课来源",
        source_session=data_session,
    )
    assert bad_source.status_code == 400
    assert bad_source.json()["code"] == "INVALID_SESSION"
    assert "source_session_id" in bad_source.json()["field_errors"]

    bad_target = send_handoff(
        flow.client1,
        crime,
        [flow.teacher2],
        content="跨开课目标",
        source_session=source,
        target_session=data_session,
    )
    assert bad_target.status_code == 400
    assert bad_target.json()["code"] == "INVALID_SESSION"
    assert "target_session_id" in bad_target.json()["field_errors"]
    assert _handoff_counts() == before


def test_sender_must_teach_the_offering_or_source_session(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(flow.demo)
    outsider = add_teacher(flow.demo, "B21-SENDER-OUT", offering_key="OFF-DATA-2026A")
    outsider_client = auth_client(link_user(outsider, "b21-sender-out@example.invalid"))
    response = send_handoff(
        outsider_client,
        crime,
        [flow.teacher1],
        content="无任课关系",
        source_session=source,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OFFERING_TEACHER"


def test_content_is_required_and_limited(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = _crime_next_session(flow.demo)

    blank = send_handoff(flow.client1, crime, [flow.teacher2], content="   ", source_session=source)
    assert blank.status_code == 400
    assert blank.json()["code"] == "VALIDATION_ERROR"

    too_long = send_handoff(flow.client1, crime, [flow.teacher2], content="字" * 5001, source_session=source)
    assert too_long.status_code == 400
    assert too_long.json()["code"] == "VALIDATION_ERROR"

    at_limit = send_handoff(flow.client1, crime, [flow.teacher2], content="字" * 5000, source_session=source)
    assert at_limit.status_code == 201, at_limit.content


def test_prefill_suggests_next_session_teacher_and_published_content(handoff_flow):
    flow = handoff_flow
    source = crime_source(flow.demo)
    next_session = next_scheduled_session(source)
    note = publish_own_note(
        flow.client2,
        source,
        flow.teacher2,
        key="prefill",
        unfinished="预填未完成内容",
        next_plan="预填下次计划",
    )

    before = (
        Handoff.objects.count(),
        Notification.objects.count(),
        TeachingNote.objects.count(),
        ClientMutation.objects.count(),
        ChangeLog.objects.count(),
    )
    response = flow.client2.get(f"/api/v1/handoffs/prefill/?source_session_id={source.id}")
    assert response.status_code == 200, response.content
    body = response.json()

    assert body["recipients"] == [
        {
            "id": str(flow.teacher1.id),
            "name": "示例教师甲",
            "is_suggested": True,
        }
    ]
    assert body["suggested_target_session"] == {
        "id": str(next_session.id),
        "date": "2026-10-19",
        "start_period": 1,
        "end_period": 2,
    }
    assert body["suggested_target_teachers"] == [{"id": str(flow.teacher1.id), "name": "示例教师甲"}]
    assert body["content"] == "未完成内容：\n预填未完成内容\n\n下次计划：\n预填下次计划"
    assert body["note_id"] == str(note.id)
    assert body["note_published_version"] == note.published_version

    after = (
        Handoff.objects.count(),
        Notification.objects.count(),
        TeachingNote.objects.count(),
        ClientMutation.objects.count(),
        ChangeLog.objects.count(),
    )
    assert after == before
    assert Handoff.objects.count() == 0


def test_prefill_lists_the_complete_next_session_teacher_roster(handoff_flow):
    flow = handoff_flow
    source = crime_source(flow.demo)
    next_session = next_scheduled_session(source)
    third = add_teacher(flow.demo, "B21-NEXT-003")
    SessionTeacher.objects.create(session=next_session, teacher=third)
    SessionTeacher.objects.create(session=next_session, teacher=flow.teacher2)

    # teacher2 is the sender and is also the actual next-session teacher.
    response = flow.client2.get(f"/api/v1/handoffs/prefill/?source_session_id={source.id}")
    assert response.status_code == 200, response.content
    body = response.json()

    names = {row["name"] for row in body["suggested_target_teachers"]}
    ids = {row["id"] for row in body["suggested_target_teachers"]}
    assert names == {"示例教师甲", "示例教师乙", "示例教师B21-NEXT-003"}
    assert str(flow.teacher2.id) in ids
    # Recipients still exclude the sender even though the sender teaches next.
    assert {row["id"] for row in body["recipients"]} == {
        str(flow.teacher1.id),
        str(third.id),
    }
    assert all(row["is_suggested"] for row in body["recipients"])


def test_prefill_reports_no_next_session_without_invented_teachers(handoff_flow):
    flow = handoff_flow
    source = (
        ClassSession.objects.filter(
            offering=flow.demo.offerings["OFF-CRIME-2026A"],
            status=SessionStatus.SCHEDULED,
        )
        .order_by("-start_at", "-id")
        .first()
    )

    response = flow.client1.get(f"/api/v1/handoffs/prefill/?source_session_id={source.id}")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["suggested_target_session"] is None
    assert body["suggested_target_teachers"] == []


def test_prefill_without_published_note_is_empty(handoff_flow):
    flow = handoff_flow
    source = _crime_next_session(flow.demo)
    response = flow.client1.get(f"/api/v1/handoffs/prefill/?source_session_id={source.id}")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["content"] == ""
    assert body["note_id"] is None
    assert body["note_published_version"] is None
    assert body["recipients"] == [
        {
            "id": str(flow.teacher2.id),
            "name": "示例教师乙",
            "is_suggested": True,
        }
    ]


def test_prefill_ignores_a_private_draft(handoff_flow):
    flow = handoff_flow
    source = _crime_next_session(flow.demo)
    draft = save_note(
        flow.client1,
        source,
        covered="草稿内容",
        unfinished="仅草稿未完成",
        next_plan="仅草稿计划",
        base_version=0,
        client_mutation_id="prefill-draft",
    )
    assert draft.status_code == 200
    response = flow.client1.get(f"/api/v1/handoffs/prefill/?source_session_id={source.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == ""
    assert body["note_id"] is None
    assert body["note_published_version"] is None


def test_prefill_rejects_a_teacher_outside_the_offering(handoff_flow):
    flow = handoff_flow
    source = crime_source(flow.demo)
    outsider = add_teacher(flow.demo, "B21-PREFILL-OUT", offering_key="OFF-DATA-2026A")
    outsider_client = auth_client(link_user(outsider, "b21-prefill-out@example.invalid"))
    response = outsider_client.get(f"/api/v1/handoffs/prefill/?source_session_id={source.id}")
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OFFERING_TEACHER"
