"""A16 inquiry flow: private contact, selected recipients, replies and close."""

from __future__ import annotations

import pytest

from apps.inquiries.models import Inquiry
from apps.inquiries.tests.helpers import (
    candidate_query,
    create_inquiry,
    linked_users,
    make_candidate_sessions,
    user_client,
)
from apps.notifications.models import Notification, NotificationType

pytestmark = pytest.mark.django_db


def test_a16_private_contact_selects_only_chosen_recipients(generated_demo):
    """A16: candidates remain reachable without public contact; only selected receive."""
    demo = generated_demo
    teachers, users = linked_users(demo)
    make_candidate_sessions(demo)
    for number in ("0002", "0003", "0004"):
        teacher = teachers[number]
        teacher.phone = f"1380000000{number[-1]}"
        teacher.phone_visibility = "private"
        teacher.wechat = ""
        teacher.wechat_visibility = "private"
        teacher.save(update_fields=["phone", "phone_visibility", "wechat", "wechat_visibility", "updated_at"])

    initiator_client = user_client(users, "0001")
    candidate_response = initiator_client.get(
        "/api/v1/inquiries/candidates/",
        candidate_query(demo.classrooms["A301"]),
    )
    assert candidate_response.status_code == 200
    candidates = candidate_response.json()["candidates"]
    assert {item["teacher"]["id"] for item in candidates} == {
        str(teachers[number].id) for number in ("0002", "0003", "0004")
    }
    assert all(len(item["related_session_ids"]) == 1 for item in candidates)
    assert all(item["course_names"] == ["公安大数据分析（示例）"] for item in candidates)

    # A16 explicitly needs no public phone: the contact endpoint hides it, but
    # the candidate list still lets the initiator choose the relevant teachers.
    contact = initiator_client.get(f"/api/v1/teachers/{teachers['0002'].id}/contact/")
    assert contact.json()["phone"] is None
    assert contact.json()["phone_status"] == "hidden"

    response = create_inquiry(
        initiator_client,
        demo.classrooms["A301"],
        [teachers["0002"].id, teachers["0003"].id],
    )
    assert response.status_code == 201
    payload = response.json()
    inquiry_id = payload["id"]
    assert payload["status"] == "open"
    assert {item["teacher"]["id"] for item in payload["recipients"]} == {
        str(teachers["0002"].id),
        str(teachers["0003"].id),
    }
    assert payload["notice"] == "排定课程记录，不代表实际到场或物品去向。"

    for number in ("0002", "0003"):
        assert (
            Notification.objects.filter(
                recipient=teachers[number],
                type=NotificationType.INQUIRY_RECEIVED,
                event_key=f"inquiry:{inquiry_id}:received",
            ).count()
            == 1
        )
    assert not Notification.objects.filter(
        recipient=teachers["0004"],
        type=NotificationType.INQUIRY_RECEIVED,
    ).exists()

    # GET detail does not write first_viewed_at.
    recipient_client = user_client(users, "0002")
    detail = recipient_client.get(f"/api/v1/inquiries/{inquiry_id}/")
    assert detail.status_code == 200
    recipient_row = next(
        item for item in detail.json()["recipients"] if item["teacher"]["id"] == str(teachers["0002"].id)
    )
    assert recipient_row["first_viewed_at"] is None

    read = recipient_client.post(f"/api/v1/inquiries/{inquiry_id}/read/")
    assert read.status_code == 200
    assert read.json()["status"] == "open"
    recipient_row = next(
        item for item in read.json()["recipients"] if item["teacher"]["id"] == str(teachers["0002"].id)
    )
    assert recipient_row["first_viewed_at"] is not None

    reply_one = recipient_client.post(
        f"/api/v1/inquiries/{inquiry_id}/replies/",
        {"result": "not_seen"},
        format="json",
    )
    assert reply_one.status_code == 201
    assert reply_one.json()["status"] == "replied"

    reply_two = user_client(users, "0003").post(
        f"/api/v1/inquiries/{inquiry_id}/replies/",
        {"result": "in_custody", "body": "已交到值班室。"},
        format="json",
    )
    assert reply_two.status_code == 201
    assert [reply["result"] for reply in reply_two.json()["replies"]] == ["not_seen", "in_custody"]

    replies = Notification.objects.filter(
        recipient=teachers["0001"],
        type=NotificationType.INQUIRY_REPLIED,
    )
    assert replies.count() == 2
    assert all(item.event_key.startswith("inquiry_reply:") for item in replies)

    close = initiator_client.post(
        f"/api/v1/inquiries/{inquiry_id}/close/",
        {"close_note": "已确认，感谢。"},
        format="json",
    )
    assert close.status_code == 200
    assert close.json()["status"] == "closed"
    assert close.json()["closed_at"] is not None

    late_reply = recipient_client.post(
        f"/api/v1/inquiries/{inquiry_id}/replies/",
        {"result": "not_seen"},
        format="json",
    )
    assert late_reply.status_code == 409
    assert late_reply.json()["code"] == "INQUIRY_CLOSED"
    assert Inquiry.objects.get(pk=inquiry_id).status == "closed"
