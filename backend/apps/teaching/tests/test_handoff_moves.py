"""B21/scheduling 5.2: moving a target session asks the sender to re-check.

The demo term has no DataCoverage declarations, so the move is registered as a
formal school-forced arrangement to pass the target evaluation.  The handoff
hook is independent of that flag.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model

from apps.notifications.models import Notification, NotificationType
from apps.scheduling.changes import apply_session_change
from apps.teaching.handoffs import notify_pending_handoffs_for_moved_session
from apps.teaching.models import Handoff
from apps.teaching.tests.handoff_helpers import CRIME_NEXT_DAY, crime_source, send_handoff
from apps.teaching.tests.helpers import FIXED_NOW, session_for

pytestmark = pytest.mark.django_db


def test_moved_target_session_notifies_only_the_sender_once(handoff_flow):
    flow = handoff_flow
    crime = flow.demo.offerings["OFF-CRIME-2026A"]
    source = crime_source(flow.demo)
    target = session_for(flow.demo, "R-CRIME-ODD", CRIME_NEXT_DAY)
    body = send_handoff(
        flow.client2,
        crime,
        [flow.teacher1],
        content="调课提示交接",
        source_session=source,
        target_session=target,
    )
    assert body.status_code == 201, body.content
    handoff_id = body.json()[0]["id"]

    actor = get_user_model().objects.create_user(
        email="b21-academic@example.invalid",
        password="test-pass-123",
    )
    change = apply_session_change(
        target.id,
        target_date=date(2026, 11, 10),
        start_period=1,
        end_period=2,
        classroom_id=flow.demo.classrooms["A301"].id,
        actor=actor,
        reason="B21 调课提示测试",
        approval_ref="TEST-FORCED",
        forced=True,
        now=FIXED_NOW,
    )

    checks = Notification.objects.filter(
        recipient=flow.teacher2,
        type=NotificationType.HANDOFF_CHECK,
        object_id=handoff_id,
    )
    assert checks.count() == 1
    notice = checks.get()
    assert "2026-11-10" in notice.body
    assert "核对接收人" in notice.body
    assert "不会自动更改接收人或转交" in notice.body
    assert not Notification.objects.filter(
        recipient=flow.teacher1,
        type=NotificationType.HANDOFF_CHECK,
    ).exists()

    handoff = Handoff.objects.get(pk=handoff_id)
    assert handoff.status == "pending"
    assert handoff.recipient_id == flow.teacher1.id

    # Replaying the same formal change (same change id) is deduplicated by the
    # notification event key.
    target.refresh_from_db()
    notify_pending_handoffs_for_moved_session(target, change)
    assert checks.count() == 1
    assert Notification.objects.filter(type=NotificationType.HANDOFF_CHECK).count() == 1
