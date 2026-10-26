"""B21 concurrent acknowledge/withdraw must produce one winning transition."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.academics.models import TeacherProfile
from apps.notifications.models import Notification, NotificationType
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests.demo_data import build_demo_term
from apps.teaching.exceptions import HandoffError
from apps.teaching.handoffs import acknowledge, create_handoffs, withdraw
from apps.teaching.models import Handoff
from apps.teaching.tests.handoff_helpers import crime_source, make_handoff_flow


@pytest.mark.django_db(transaction=True)
def test_concurrent_acknowledge_and_withdraw_leave_one_result():
    demo = build_demo_term()
    generate_sessions_for_term(demo.term)
    flow = make_handoff_flow(demo)
    crime = demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(demo)
    handoff = create_handoffs(
        flow.teacher2,
        crime,
        [flow.teacher1],
        "并发确认与撤回",
        source,
        None,
        None,
        timezone.now(),
    )[0]

    barrier = threading.Barrier(2)

    def run(action: str) -> dict:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            handoff_row = Handoff.objects.get(pk=handoff.id)
            if action == "acknowledge":
                teacher = TeacherProfile.objects.get(pk=flow.teacher1.id)
                acknowledge(teacher, handoff_row, timezone.now())
            else:
                teacher = TeacherProfile.objects.get(pk=flow.teacher2.id)
                withdraw(teacher, handoff_row, timezone.now())
            return {"action": action, "ok": True}
        except HandoffError as exc:
            return {"action": action, "ok": False, "code": exc.code, "status": exc.status_code}
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("acknowledge", "withdraw")))

    winners = [result for result in results if result["ok"]]
    losers = [result for result in results if not result["ok"]]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0]["status"] == 409

    final = Handoff.objects.get(pk=handoff.id)
    if winners[0]["action"] == "acknowledge":
        assert final.status == "acknowledged"
        assert final.acknowledged_at is not None
        assert losers[0]["code"] == "HANDOFF_ALREADY_ACKNOWLEDGED"
        assert (
            Notification.objects.filter(
                recipient=flow.teacher2,
                type=NotificationType.HANDOFF_ACKNOWLEDGED,
                object_id=handoff.id,
            ).count()
            == 1
        )
    else:
        assert final.status == "withdrawn"
        assert final.withdrawn_at is not None
        assert losers[0]["code"] == "HANDOFF_WITHDRAWN"
        assert not Notification.objects.filter(
            recipient=flow.teacher2,
            type=NotificationType.HANDOFF_ACKNOWLEDGED,
            object_id=handoff.id,
        ).exists()
