"""B33 concurrent reply and close must produce one consistent order."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.academics.models import TeacherProfile
from apps.inquiries.exceptions import InquiryError
from apps.inquiries.models import Inquiry, InquiryReply
from apps.inquiries.services import close_inquiry, create_inquiry, reply_to_inquiry
from apps.inquiries.tests.helpers import (
    FROM_AT,
    TO_AT,
    linked_users,
    make_candidate_sessions,
)
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests.demo_data import build_demo_term


@pytest.mark.django_db(transaction=True)
def test_concurrent_reply_and_close_are_ordered_by_the_inquiry_row_lock():
    demo = build_demo_term()
    generate_sessions_for_term(demo.term)
    teachers, users = linked_users(demo)
    make_candidate_sessions(demo, teacher_numbers=("0002",))
    inquiry = create_inquiry(
        teachers["0001"],
        demo.classrooms["A301"],
        from_at=FROM_AT,
        to_at=TO_AT,
        item_description="并发测试物品",
        recipient_ids=[teachers["0002"].id],
    )

    barrier = threading.Barrier(2)

    def run(action: str) -> dict:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            current = Inquiry.objects.get(pk=inquiry.id)
            if action == "reply":
                teacher = TeacherProfile.objects.get(pk=teachers["0002"].id)
                reply_to_inquiry(
                    teacher,
                    current,
                    result="not_seen",
                    body="",
                    now=timezone.now(),
                )
            else:
                teacher = TeacherProfile.objects.get(pk=teachers["0001"].id)
                close_inquiry(teacher, current, close_note="并发结案", now=timezone.now())
            return {"action": action, "ok": True}
        except InquiryError as exc:
            return {"action": action, "ok": False, "code": exc.code, "status": exc.status_code}
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("reply", "close")))

    close_result = next(item for item in results if item["action"] == "close")
    reply_result = next(item for item in results if item["action"] == "reply")
    assert close_result["ok"] is True

    final = Inquiry.objects.get(pk=inquiry.id)
    assert final.status == "closed"
    assert final.closed_at is not None
    if reply_result["ok"]:
        assert InquiryReply.objects.filter(inquiry=final).count() == 1
    else:
        assert reply_result["status"] == 409
        assert reply_result["code"] == "INQUIRY_CLOSED"
        assert InquiryReply.objects.filter(inquiry=final).count() == 0
