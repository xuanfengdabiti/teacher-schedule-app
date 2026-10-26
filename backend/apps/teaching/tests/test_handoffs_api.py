"""B21 handoff API acceptance tests: A14, status transitions and visibility."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.notifications.models import Notification, NotificationType
from apps.teaching.admin import HandoffAdmin
from apps.teaching.models import Handoff, HandoffStatus
from apps.teaching.tests.handoff_helpers import (
    add_teacher,
    crime_source,
    next_scheduled_session,
    publish_own_note,
    send_handoff,
)
from apps.teaching.tests.helpers import auth_client, link_user, publish_note, save_note

pytestmark = pytest.mark.django_db


def _handoff_response(response) -> dict:
    assert response.status_code == 201, response.content
    return response.json()[0]


def test_a14_read_does_not_acknowledge_and_snapshot_survives_republish(handoff_flow):
    """A14: read stays pending; acknowledgement is explicit; old snapshot kept."""
    flow = handoff_flow
    demo = flow.demo
    crime = demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(demo)
    note = publish_own_note(
        flow.client2,
        source,
        flow.teacher2,
        key="a14",
        unfinished="A14 发布时的未完成内容",
        next_plan="A14 发布时的下次计划",
    )
    assert note.published_version == 2

    body = _handoff_response(
        send_handoff(
            flow.client2,
            crime,
            [flow.teacher1],
            content="A14 交接正文快照",
            source_session=source,
            target_session=next_scheduled_session(source),
            note=note,
        )
    )
    handoff = Handoff.objects.get(pk=body["id"])
    assert handoff.status == HandoffStatus.PENDING
    assert handoff.note_published_version == 2
    assert handoff.content_snapshot == "A14 交接正文快照"

    received = Notification.objects.get(
        recipient=flow.teacher1,
        type=NotificationType.HANDOFF_RECEIVED,
        object_id=handoff.id,
    )
    assert "刑事侦查专题研究（示例）" in received.body
    assert "A14 交接正文快照" in received.body

    read = flow.client1.post(f"/api/v1/handoffs/{handoff.id}/read/")
    assert read.status_code == 200, read.content
    read_at = read.json()["read_at"]
    assert read_at is not None
    assert read.json()["status"] == "pending"
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.PENDING
    assert handoff.read_at is not None

    # GET is a pure read: it must not add another read timestamp or confirm.
    detail = flow.client1.get(f"/api/v1/handoffs/{handoff.id}/")
    assert detail.status_code == 200
    assert detail.json()["read_at"] == read_at
    assert detail.json()["status"] == "pending"
    assert detail.json()["can_acknowledge"] is True

    ack = flow.client1.post(f"/api/v1/handoffs/{handoff.id}/acknowledge/")
    assert ack.status_code == 200, ack.content
    acknowledged_at = ack.json()["acknowledged_at"]
    assert ack.json()["status"] == "acknowledged"
    assert acknowledged_at is not None
    assert ack.json()["read_at"] == read_at
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.ACKNOWLEDGED
    assert handoff.acknowledged_at is not None

    ack_notice = Notification.objects.get(
        recipient=flow.teacher2,
        type=NotificationType.HANDOFF_ACKNOWLEDGED,
        object_id=handoff.id,
    )
    assert "已确认" in ack_notice.body

    sent = flow.client2.get("/api/v1/handoffs/?box=sent")
    assert sent.status_code == 200
    sent_item = next(item for item in sent.json()["results"] if item["id"] == str(handoff.id))
    assert sent_item["acknowledged_at"] == acknowledged_at
    assert sent_item["can_withdraw"] is False

    # Editing and republishing the source note must not rewrite the handoff.
    saved = save_note(
        flow.client2,
        source,
        covered="A14 修改后的已讲内容",
        unfinished="A14 修改后的未完成内容",
        next_plan="A14 修改后的下次计划",
        base_version=note.version,
        client_mutation_id="a14-edit",
    )
    assert saved.status_code == 200, saved.content
    republished = publish_note(
        flow.client2,
        source,
        base_version=saved.json()["version"],
        client_mutation_id="a14-republish",
    )
    assert republished.status_code == 200, republished.content
    assert republished.json()["published_version"] == 4

    handoff.refresh_from_db()
    assert handoff.content_snapshot == "A14 交接正文快照"
    assert handoff.note_published_version == 2
    after = flow.client1.get(f"/api/v1/handoffs/{handoff.id}/")
    assert after.json()["content_snapshot"] == "A14 交接正文快照"
    assert after.json()["note_published_version"] == 2


def test_repeated_acknowledge_and_withdraw_are_idempotent(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(flow.demo)

    first = _handoff_response(
        send_handoff(flow.client2, crime, [flow.teacher1], content="第一次交接", source_session=source)
    )
    ack_one = flow.client1.post(f"/api/v1/handoffs/{first['id']}/acknowledge/")
    ack_two = flow.client1.post(f"/api/v1/handoffs/{first['id']}/acknowledge/")
    assert ack_one.status_code == 200
    assert ack_two.status_code == 200
    assert ack_one.json()["acknowledged_at"] == ack_two.json()["acknowledged_at"]
    assert (
        Notification.objects.filter(
            recipient=flow.teacher2,
            type=NotificationType.HANDOFF_ACKNOWLEDGED,
            object_id=first["id"],
        ).count()
        == 1
    )

    withdraw_conflict = flow.client2.post(f"/api/v1/handoffs/{first['id']}/withdraw/")
    assert withdraw_conflict.status_code == 409
    assert withdraw_conflict.json()["code"] == "HANDOFF_ALREADY_ACKNOWLEDGED"

    second = _handoff_response(
        send_handoff(flow.client2, crime, [flow.teacher1], content="第二次交接", source_session=source)
    )
    withdraw_one = flow.client2.post(f"/api/v1/handoffs/{second['id']}/withdraw/")
    withdraw_two = flow.client2.post(f"/api/v1/handoffs/{second['id']}/withdraw/")
    assert withdraw_one.status_code == 200
    assert withdraw_two.status_code == 200
    assert withdraw_one.json()["status"] == "withdrawn"
    assert withdraw_one.json()["withdrawn_at"] == withdraw_two.json()["withdrawn_at"]

    ack_withdrawn = flow.client1.post(f"/api/v1/handoffs/{second['id']}/acknowledge/")
    assert ack_withdrawn.status_code == 409
    assert ack_withdrawn.json()["code"] == "HANDOFF_WITHDRAWN"

    third = _handoff_response(
        send_handoff(flow.client2, crime, [flow.teacher1], content="第三次交接", source_session=source)
    )
    sender_ack = flow.client2.post(f"/api/v1/handoffs/{third['id']}/acknowledge/")
    assert sender_ack.status_code == 403
    assert sender_ack.json()["code"] == "NOT_HANDOFF_RECIPIENT"
    recipient_withdraw = flow.client1.post(f"/api/v1/handoffs/{third['id']}/withdraw/")
    assert recipient_withdraw.status_code == 403
    assert recipient_withdraw.json()["code"] == "NOT_HANDOFF_SENDER"


def test_inbox_sent_and_detail_visibility(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(flow.demo)
    third = add_teacher(flow.demo, "B21-DATA", offering_key="OFF-DATA-2026A")
    third_user = link_user(third, "b21-data@example.invalid")
    third_client = auth_client(third_user)

    body = _handoff_response(
        send_handoff(flow.client2, crime, [flow.teacher1], content="可见性交接", source_session=source)
    )
    handoff = Handoff.objects.get(pk=body["id"])

    assert flow.client2.get(f"/api/v1/handoffs/{handoff.id}/").status_code == 200
    assert flow.client1.get(f"/api/v1/handoffs/{handoff.id}/").status_code == 200
    third_detail = third_client.get(f"/api/v1/handoffs/{handoff.id}/")
    assert third_detail.status_code == 404
    assert third_detail.json()["code"] == "NOT_FOUND"
    handoff.refresh_from_db()
    assert handoff.read_at is None

    inbox_one = flow.client1.get("/api/v1/handoffs/?box=inbox")
    inbox_two = flow.client2.get("/api/v1/handoffs/?box=inbox")
    sent_one = flow.client1.get("/api/v1/handoffs/?box=sent")
    sent_two = flow.client2.get("/api/v1/handoffs/?box=sent")
    third_inbox = third_client.get("/api/v1/handoffs/?box=inbox")
    third_sent = third_client.get("/api/v1/handoffs/?box=sent")

    assert [item["id"] for item in inbox_one.json()["results"]] == [str(handoff.id)]
    assert inbox_two.json()["count"] == 0
    assert [item["id"] for item in sent_two.json()["results"]] == [str(handoff.id)]
    assert sent_one.json()["count"] == 0
    assert third_inbox.json()["count"] == 0
    assert third_sent.json()["count"] == 0

    item = inbox_one.json()["results"][0]
    assert item["offering"]["course_name"] == "刑事侦查专题研究（示例）"
    assert item["offering"]["teaching_class"]["name"] == "2026级示例教学班A"
    assert item["sender"]["id"] == str(flow.teacher2.id)
    assert item["recipient"]["id"] == str(flow.teacher1.id)
    assert item["source_session"]["id"] == str(source.id)
    assert item["can_acknowledge"] is True
    assert item["can_withdraw"] is False

    assert flow.client1.post(f"/api/v1/handoffs/{handoff.id}/acknowledge/").status_code == 200
    acknowledged = flow.client1.get("/api/v1/handoffs/?box=inbox&status=acknowledged")
    pending = flow.client1.get("/api/v1/handoffs/?box=inbox&status=pending")
    assert acknowledged.json()["count"] == 1
    assert pending.json()["count"] == 0


def test_read_requires_recipient_and_hides_third_party(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(flow.demo)
    body = _handoff_response(
        send_handoff(flow.client2, crime, [flow.teacher1], content="已读权限", source_session=source)
    )

    sender_read = flow.client2.post(f"/api/v1/handoffs/{body['id']}/read/")
    assert sender_read.status_code == 403
    assert sender_read.json()["code"] == "NOT_HANDOFF_RECIPIENT"

    third = add_teacher(flow.demo, "B21-OTHER", offering_key="OFF-DATA-2026A")
    third_client = auth_client(link_user(third, "b21-other@example.invalid"))
    third_read = third_client.post(f"/api/v1/handoffs/{body['id']}/read/")
    assert third_read.status_code == 404

    recipient_read = flow.client1.post(f"/api/v1/handoffs/{body['id']}/read/")
    assert recipient_read.status_code == 200
    assert recipient_read.json()["status"] == "pending"


def test_unlinked_and_disabled_teacher_use_existing_codes(handoff_flow):
    flow = handoff_flow
    source = crime_source(flow.demo)
    prefill_url = f"/api/v1/handoffs/prefill/?source_session_id={source.id}"

    unlinked = get_user_model().objects.create_user(
        email="b21-unlinked@example.invalid",
        password="test-pass-123",
    )
    unlinked_client = auth_client(unlinked)
    response = unlinked_client.get("/api/v1/handoffs/")
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_NOT_LINKED"
    response = unlinked_client.get(prefill_url)
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_NOT_LINKED"

    flow.teacher2.is_active = False
    flow.teacher2.save(update_fields=["is_active", "updated_at"])
    disabled = flow.client2.get("/api/v1/handoffs/")
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "TEACHER_DISABLED"
    disabled = flow.client2.get(prefill_url)
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "TEACHER_DISABLED"


def test_handoff_admin_is_read_only():
    model_admin = HandoffAdmin(Handoff, AdminSite())
    request = RequestFactory().get("/admin/teaching/handoff/")
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_status_filter_choices_are_validated(handoff_flow):
    flow = handoff_flow
    bad_box = flow.client1.get("/api/v1/handoffs/?box=elsewhere")
    assert bad_box.status_code == 400
    assert bad_box.json()["code"] == "VALIDATION_ERROR"
    bad_status = flow.client1.get("/api/v1/handoffs/?box=inbox&status=unknown")
    assert bad_status.status_code == 400
    assert bad_status.json()["code"] == "VALIDATION_ERROR"
