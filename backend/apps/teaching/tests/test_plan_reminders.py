"""B23 plan-behind calculation, date boundaries and daily reminder tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.academics.models import AssignmentRole, TeachingAssignment
from apps.notifications.models import Notification, NotificationType
from apps.scheduling.tests import factories
from apps.teaching.completion import record_completion
from apps.teaching.plans import save_plan
from apps.teaching.progress import offering_progress
from apps.teaching.tests.helpers import FIXED_NOW
from apps.teaching.tests.plan_helpers import (
    make_course,
    make_session,
    plan_item_payload,
)

pytestmark = pytest.mark.django_db


def _behind_offering(demo, key: str, *, class_code: str | None = None, allocated: str = "2.0"):
    lead, co_teacher = demo.teachers["0001"], demo.teachers["0002"]
    offering = make_course(
        demo,
        key,
        lead=lead,
        co_teachers=(co_teacher,),
        class_code=class_code,
    )
    plan = save_plan(
        lead,
        offering,
        items=[plan_item_payload("到期专题", "5.0", due_date=date(2026, 10, 20))],
        base_version=0,
        now=FIXED_NOW,
    )
    item = plan.items.get()
    session = make_session(
        offering,
        day=date(2026, 10, 19),
        teachers=(lead,),
        classroom=demo.classrooms["A301"],
    )
    record_completion(
        lead,
        session,
        action="complete",
        actual_hours=allocated,
        allocations=[{"plan_item_id": item.id, "actual_hours": allocated}] if Decimal(allocated) > 0 else [],
        reason="",
        base_version=1,
        now=FIXED_NOW,
    )
    return offering, plan, item, session, lead, co_teacher


class TestBehindCalculation:
    def test_no_plan_is_not_set_and_due_shortfall_uses_threshold(self, demo_term, settings) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        no_plan = make_course(demo, "NO-PLAN", lead=teacher)
        empty = offering_progress(no_plan, today=date(2026, 10, 26), now=FIXED_NOW)
        assert empty["plan_status"] == "not_set"
        assert empty["behind_hours"] == "0.0"
        assert empty["items"] == []

        behind, _plan, _item, _session, _lead, _co = _behind_offering(demo, "BEHIND")
        result = offering_progress(behind, today=date(2026, 10, 26), now=FIXED_NOW)
        assert result["plan_status"] == "behind"
        assert result["behind_hours"] == "3.0"
        assert result["items"][0]["shortfall"] == "3.0"
        assert result["unallocated_hours"] == "0.0"

        settings.PLAN_BEHIND_THRESHOLD_HOURS = "4.0"
        overridden = offering_progress(behind, today=date(2026, 10, 26), now=FIXED_NOW)
        assert overridden["behind_threshold_hours"] == "4.0"
        assert overridden["behind_hours"] == "3.0"
        assert overridden["plan_status"] == "on_track"

    def test_zero_threshold_requires_positive_shortfall_and_sends_nothing(self, demo_term, settings) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        settings.PLAN_BEHIND_THRESHOLD_HOURS = "0"
        offering = make_course(demo, "ZERO-THRESHOLD", lead=teacher)
        plan = save_plan(
            teacher,
            offering,
            items=[plan_item_payload("已完成无缺口", "2.0", due_date=date(2026, 10, 20))],
            base_version=0,
            now=FIXED_NOW,
        )
        item = plan.items.get()
        session = make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )
        record_completion(
            teacher,
            session,
            action="complete",
            actual_hours="2.0",
            allocations=[{"plan_item_id": item.id, "actual_hours": "2.0"}],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )

        progress = offering_progress(offering, today=date(2026, 10, 26), now=FIXED_NOW)
        assert progress["behind_threshold_hours"] == "0.0"
        assert progress["behind_hours"] == "0.0"
        assert progress["plan_status"] == "on_track"

        call_command("send_plan_reminders", "--date", "2026-10-26", verbosity=0)
        assert Notification.objects.count() == 0

    def test_date_boundary_and_archived_item_exclusion(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering, plan, item, _session, _lead, _co = _behind_offering(demo, "DATE")

        before_due = offering_progress(offering, today=date(2026, 10, 19), now=FIXED_NOW)
        on_due = offering_progress(offering, today=date(2026, 10, 20), now=FIXED_NOW)
        assert before_due["plan_status"] == "on_track"
        assert before_due["items"][0]["is_due"] is False
        assert before_due["items"][0]["shortfall"] == "0.0"
        assert on_due["plan_status"] == "behind"
        assert on_due["behind_hours"] == "3.0"

        save_plan(teacher, offering, items=[], base_version=1, now=FIXED_NOW)
        item.refresh_from_db()
        assert item.archived is True
        archived = offering_progress(offering, today=date(2026, 10, 26), now=FIXED_NOW)
        assert archived["plan_status"] == "on_track"
        assert archived["behind_hours"] == "0.0"
        assert archived["items"][0]["archived"] is True
        assert archived["items"][0]["shortfall"] == "3.0"


class TestReminderCommand:
    def test_behind_sends_one_daily_message_per_current_teacher_and_is_idempotent(self, demo_term) -> None:
        demo = demo_term
        first, _plan1, _item1, _session1, lead, co_teacher = _behind_offering(demo, "REM-1")
        second, _plan2, _item2, _session2, _lead2, _co2 = _behind_offering(
            demo,
            "REM-2",
            class_code="TC-T11-REM-1",
        )
        expired_teacher = factories.make_teacher(demo.school, "T11-REM-EXPIRED", "已到期任课教师")
        TeachingAssignment.objects.create(
            offering=first,
            teacher=expired_teacher,
            role=AssignmentRole.TEACHER,
            effective_from=demo.term.start_date,
            effective_until=date(2026, 10, 1),
        )

        call_command("send_plan_reminders", "--date", "2026-10-26", verbosity=0)

        messages = Notification.objects.filter(type=NotificationType.PLAN_BEHIND)
        assert messages.count() == 4
        for offering in (first, second):
            for teacher in (lead, co_teacher):
                message = messages.get(
                    recipient=teacher,
                    event_key=f"plan_behind:{offering.id}:2026-10-26",
                )
                assert message.object_type == "offering"
                assert message.object_id == offering.id
                assert "原计划 2026-10-20 前完成的专题还差 3.0 学时" in message.body

        before = Notification.objects.count()
        call_command("send_plan_reminders", "--date", "2026-10-26", verbosity=0)
        assert Notification.objects.count() == before
        assert not Notification.objects.filter(recipient=expired_teacher).exists()

    def test_on_track_and_not_set_send_nothing_and_filled_plan_stops_next_day(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        make_course(demo, "REM-NONE", lead=teacher)
        on_track = make_course(demo, "REM-ON", lead=teacher)
        plan = save_plan(
            teacher,
            on_track,
            items=[plan_item_payload("已完成专题", "2.0", due_date=date(2026, 10, 20))],
            base_version=0,
            now=FIXED_NOW,
        )
        item = plan.items.get()
        session = make_session(
            on_track,
            day=date(2026, 10, 19),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )
        record_completion(
            teacher,
            session,
            action="complete",
            actual_hours="2.0",
            allocations=[{"plan_item_id": item.id, "actual_hours": "2.0"}],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )

        call_command("send_plan_reminders", "--date", "2026-10-26", verbosity=0)
        assert Notification.objects.count() == 0

        behind, _plan, behind_item, _session, lead, _co = _behind_offering(demo, "REM-FILL")
        call_command("send_plan_reminders", "--date", "2026-10-26", verbosity=0)
        assert Notification.objects.filter(type=NotificationType.PLAN_BEHIND).count() == 2

        extra = make_session(
            behind,
            day=date(2026, 10, 20),
            teachers=(lead,),
            classroom=demo.classrooms["A301"],
        )
        record_completion(
            lead,
            extra,
            action="complete",
            actual_hours="3.0",
            allocations=[{"plan_item_id": behind_item.id, "actual_hours": "3.0"}],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )
        assert offering_progress(behind, today=date(2026, 10, 27), now=FIXED_NOW)["plan_status"] == "on_track"

        count = Notification.objects.count()
        call_command("send_plan_reminders", "--date", "2026-10-27", verbosity=0)
        assert Notification.objects.count() == count
