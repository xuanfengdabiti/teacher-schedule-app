"""Inquiry validation, authorization and recipient snapshot rules (B33)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from apps.inquiries.models import Inquiry, InquiryRecipient
from apps.inquiries.tests.helpers import (
    FIXED_NOW,
    candidate_query,
    create_inquiry,
    linked_users,
    make_candidate_sessions,
    user_client,
)
from apps.scheduling.changes import apply_session_change

pytestmark = pytest.mark.django_db


def test_invalid_recipient_creates_nothing(generated_demo):
    demo = generated_demo
    teachers, users = linked_users(demo)
    make_candidate_sessions(demo)
    response = create_inquiry(user_client(users, "0001"), demo.classrooms["A301"], [teachers["0005"].id])
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RECIPIENT"
    assert Inquiry.objects.count() == 0
    assert InquiryRecipient.objects.count() == 0


def test_non_recipient_roles_and_unrelated_view(generated_demo):
    demo = generated_demo
    teachers, users = linked_users(demo)
    make_candidate_sessions(demo)
    initiator = user_client(users, "0001")
    created = create_inquiry(initiator, demo.classrooms["A301"], [teachers["0002"].id])
    inquiry_id = created.json()["id"]

    initiator_reply = initiator.post(
        f"/api/v1/inquiries/{inquiry_id}/replies/",
        {"result": "not_seen"},
        format="json",
    )
    assert initiator_reply.status_code == 403
    assert initiator_reply.json()["code"] == "NOT_INQUIRY_RECIPIENT"

    initiator_read = initiator.post(f"/api/v1/inquiries/{inquiry_id}/read/")
    assert initiator_read.status_code == 403
    assert initiator_read.json()["code"] == "NOT_INQUIRY_RECIPIENT"

    recipient_close = user_client(users, "0002").post(f"/api/v1/inquiries/{inquiry_id}/close/")
    assert recipient_close.status_code == 403
    assert recipient_close.json()["code"] == "NOT_INQUIRY_INITIATOR"

    # Teacher 5 was never a recipient; the detail endpoint does not reveal the
    # inquiry's existence.
    unrelated_detail = user_client(users, "0005").get(f"/api/v1/inquiries/{inquiry_id}/")
    assert unrelated_detail.status_code == 404


def test_other_requires_body_and_range_is_limited_to_seven_days(generated_demo):
    demo = generated_demo
    teachers, users = linked_users(demo)
    make_candidate_sessions(demo)
    initiator = user_client(users, "0001")
    created = create_inquiry(initiator, demo.classrooms["A301"], [teachers["0002"].id])
    inquiry_id = created.json()["id"]

    missing_body = user_client(users, "0002").post(
        f"/api/v1/inquiries/{inquiry_id}/replies/",
        {"result": "other", "body": "   "},
        format="json",
    )
    assert missing_body.status_code == 400
    assert missing_body.json()["field_errors"]["body"]

    query = candidate_query(demo.classrooms["A301"])
    query["to_at"] = (datetime(2026, 10, 28, tzinfo=FIXED_NOW.tzinfo) + timedelta(days=8)).isoformat()
    too_long = initiator.get("/api/v1/inquiries/candidates/", query)
    assert too_long.status_code == 400
    assert too_long.json()["code"] == "INVALID_DATE_RANGE"

    query = candidate_query(demo.classrooms["A301"])
    query["to_at"] = query["from_at"]
    reversed_range = initiator.get("/api/v1/inquiries/candidates/", query)
    assert reversed_range.status_code == 400
    assert reversed_range.json()["code"] == "INVALID_DATE_RANGE"

    too_long_create = initiator.post(
        "/api/v1/inquiries/",
        {
            "classroom_id": str(demo.classrooms["A301"].id),
            "from_at": query["from_at"],
            "to_at": (datetime(2026, 10, 28, tzinfo=FIXED_NOW.tzinfo) + timedelta(days=8)).isoformat(),
            "item_description": "超过七天的协查",
            "recipient_ids": [str(teachers["0002"].id)],
        },
        format="json",
    )
    assert too_long_create.status_code == 400
    assert too_long_create.json()["code"] == "INVALID_DATE_RANGE"
    assert Inquiry.objects.count() == 1


def test_recipient_snapshot_is_not_changed_by_a_formal_move(generated_demo):
    demo = generated_demo
    teachers, users = linked_users(demo)
    session = make_candidate_sessions(demo, teacher_numbers=("0002",))[0]
    initiator = user_client(users, "0001")
    created = create_inquiry(initiator, demo.classrooms["A301"], [teachers["0002"].id])
    inquiry_id = created.json()["id"]
    recipient = InquiryRecipient.objects.get(inquiry_id=inquiry_id, teacher=teachers["0002"])
    original_ids = recipient.related_session_ids
    assert original_ids == [str(session.id)]

    apply_session_change(
        session.id,
        target_date=datetime(2026, 10, 30, tzinfo=FIXED_NOW.tzinfo).date(),
        start_period=5,
        end_period=6,
        classroom_id=demo.classrooms["A302"].id,
        actor=users["0001"],
        reason="B33 快照验证",
        approval_ref="SCHOOL-B33-001",
        forced=True,
        now=FIXED_NOW,
    )
    session.refresh_from_db()
    assert session.classroom_id == demo.classrooms["A302"].id

    recipient.refresh_from_db()
    assert recipient.related_session_ids == original_ids
    detail = initiator.get(f"/api/v1/inquiries/{inquiry_id}/")
    assert detail.json()["recipients"][0]["related_session_ids"] == original_ids
    assert detail.json()["recipients"][0]["teacher"]["id"] == str(teachers["0002"].id)


def test_list_boxes_status_and_pagination(generated_demo):
    demo = generated_demo
    teachers, users = linked_users(demo)
    make_candidate_sessions(demo)
    initiator = user_client(users, "0001")
    created = create_inquiry(initiator, demo.classrooms["A301"], [teachers["0002"].id])
    assert created.status_code == 201

    sent = initiator.get("/api/v1/inquiries/?box=sent")
    assert sent.status_code == 200
    assert sent.json()["count"] == 1
    assert {"count", "next", "previous", "results"} <= set(sent.json())

    received = user_client(users, "0002").get("/api/v1/inquiries/?box=received")
    assert received.json()["count"] == 1
    assert received.json()["results"][0]["id"] == created.json()["id"]

    unrelated = user_client(users, "0004").get("/api/v1/inquiries/?box=received")
    assert unrelated.json()["count"] == 0

    filtered = initiator.get("/api/v1/inquiries/?box=sent&status=open")
    assert filtered.json()["count"] == 1
    filtered = initiator.get("/api/v1/inquiries/?box=sent&status=closed")
    assert filtered.json()["count"] == 0


def test_inquiry_new_endpoints_keep_existing_403_codes(demo_term):
    from django.contrib.auth import get_user_model

    demo = demo_term
    from apps.teaching.tests.helpers import auth_client

    unlinked = get_user_model().objects.create_user(email="inquiry-unlinked@example.invalid", password="test-pass-123")
    response = auth_client(unlinked).get(
        "/api/v1/inquiries/candidates/",
        candidate_query(demo.classrooms["A301"]),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_NOT_LINKED"

    teachers, users = linked_users(demo)
    teachers["0001"].is_active = False
    teachers["0001"].save(update_fields=["is_active", "updated_at"])
    disabled = user_client(users, "0001").get(
        "/api/v1/inquiries/candidates/",
        candidate_query(demo.classrooms["A301"]),
    )
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "TEACHER_DISABLED"
