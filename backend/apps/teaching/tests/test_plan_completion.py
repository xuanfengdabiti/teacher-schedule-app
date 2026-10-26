"""B22 completion, allocation, correction and concurrency acceptance tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.academics.models import TeacherProfile
from apps.scheduling.models import ChangeEntityType, ChangeLog, ClassSession, SessionStatus
from apps.teaching.exceptions import TeachingProgressError
from apps.teaching.models import CompletionRecord, SessionProgress, TeachingPlan
from apps.teaching.plans import save_plan
from apps.teaching.progress import offering_progress
from apps.teaching.tests.helpers import (
    FIXED_NOW,
    auth_client,
    link_user,
    note_payload,
    save_note,
)
from apps.teaching.tests.plan_helpers import (
    completion_api,
    make_course,
    make_session,
    plan_item_payload,
)

pytestmark = pytest.mark.django_db


def _linked_client(teacher: TeacherProfile, key: str):
    return auth_client(link_user(teacher, f"t11-{key}@example.invalid"))


def _make_plan(demo, offering, teacher, items):
    return save_plan(teacher, offering, items=items, base_version=0, now=FIXED_NOW)


class TestA11OneCompletedSessionCountsOnce:
    def test_two_teachers_notes_do_not_add_hours_and_second_completion_is_rejected(self, demo_term) -> None:
        demo = demo_term
        teacher1, teacher2 = demo.teachers["0001"], demo.teachers["0002"]
        offering = make_course(demo, "A11", lead=teacher1, co_teachers=(teacher2,))
        session = make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(teacher1, teacher2),
            classroom=demo.classrooms["A301"],
        )
        client1 = _linked_client(teacher1, "a11-1")
        client2 = _linked_client(teacher2, "a11-2")

        for index, client in enumerate((client1, client2), start=1):
            response = save_note(
                client,
                session,
                covered=f"教师 {index} 的独立记录",
                unfinished="未完成",
                next_plan="下次计划",
                base_version=0,
                client_mutation_id=f"a11-note-{index}",
            )
            assert response.status_code == 200, response.data

        session.refresh_from_db()
        assert session.status == SessionStatus.SCHEDULED
        assert session.actual_hours is None
        assert session.version == 1

        completed = completion_api(
            client1,
            session,
            action="complete",
            actual_hours="2.0",
            base_version=1,
        )
        assert completed.status_code == 200, completed.data
        assert completed.data["actual_hours"] == "2.0"
        assert completed.data["version"] == 2

        progress = client1.get(f"/api/v1/offerings/{offering.id}/plan/")
        assert progress.status_code == 200
        assert progress.data["progress"]["completed_hours"] == "2.0"
        detail = client2.get(f"/api/v1/sessions/{session.id}/completion/")
        assert detail.status_code == 200
        assert detail.data["status"] == SessionStatus.COMPLETED
        assert detail.data["actual_hours"] == "2.0"
        assert detail.data["allocations"] == []
        assert detail.data["available_items"] == []
        assert len(detail.data["history"]) == 1
        assert detail.data["history"][0]["action"] == "complete"

        repeated = completion_api(
            client2,
            session,
            action="complete",
            actual_hours="2.0",
            base_version=completed.data["version"],
        )
        assert repeated.status_code == 409
        assert repeated.data["code"] == "INVALID_COMPLETION_TRANSITION"

        session.refresh_from_db()
        assert session.actual_hours == 2
        after = client2.get(f"/api/v1/offerings/{offering.id}/plan/")
        assert after.data["progress"]["completed_hours"] == "2.0"
        assert CompletionRecord.objects.filter(session=session).count() == 1


class TestA12AndA24WaitingSessionsAndCancellation:
    def test_past_scheduled_is_pending_cancelled_is_zero_and_progress_counts_match(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "A24", lead=teacher, target_hours=40)
        past = make_session(
            offering,
            day=date(2026, 10, 26),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )
        future = [
            make_session(
                offering,
                day=date(2026, 10, 27) + timedelta(days=offset),
                teachers=(teacher,),
                classroom=demo.classrooms["A301"],
            )
            for offset in range(3)
        ]
        cancelled = make_session(
            offering,
            day=date(2026, 10, 20),
            teachers=(teacher,),
            status=SessionStatus.CANCELLED,
            classroom=demo.classrooms["A301"],
        )

        progress = offering_progress(offering, today=date(2026, 10, 26), now=FIXED_NOW)

        assert progress["completed_hours"] == "0.0"
        assert progress["remaining_sessions"] == 4
        assert progress["past_pending_sessions"] == 1
        assert progress["future_sessions"] == 3
        past.refresh_from_db()
        assert past.status == SessionStatus.SCHEDULED
        assert past.actual_hours is None
        cancelled.refresh_from_db()
        assert cancelled.status == SessionStatus.CANCELLED
        assert cancelled.actual_hours == 0

        from apps.teaching.completion import record_completion

        with pytest.raises(TeachingProgressError) as exc:
            record_completion(
                teacher,
                future[0],
                action="complete",
                actual_hours="2.0",
                allocations=[],
                reason="",
                base_version=1,
                now=FIXED_NOW,
            )
        assert exc.value.status_code == 409
        assert exc.value.code == "INVALID_COMPLETION_TRANSITION"
        future[0].refresh_from_db()
        assert future[0].status == SessionStatus.SCHEDULED
        assert future[0].actual_hours is None

        with pytest.raises(TeachingProgressError) as cancelled_exc:
            record_completion(
                teacher,
                cancelled,
                action="complete",
                actual_hours="2.0",
                allocations=[],
                reason="",
                base_version=cancelled.version,
                now=FIXED_NOW,
            )
        assert cancelled_exc.value.status_code == 409
        assert cancelled_exc.value.code == "INVALID_COMPLETION_TRANSITION"


class TestA13TopicAllocationLimit:
    def test_over_allocation_is_400_and_partial_allocation_leaves_unallocated_hours(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "A13", lead=teacher, target_hours=10)
        plan = _make_plan(
            demo,
            offering,
            teacher,
            [plan_item_payload("专题一", "8.0")],
        )
        item = plan.items.get()
        session = make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )

        from apps.teaching.completion import record_completion

        with pytest.raises(TeachingProgressError) as exc:
            record_completion(
                teacher,
                session,
                action="complete",
                actual_hours="2.0",
                allocations=[{"plan_item_id": item.id, "actual_hours": "2.5"}],
                reason="",
                base_version=1,
                now=FIXED_NOW,
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "ALLOCATION_EXCEEDS_SESSION_HOURS"
        assert exc.value.extra["allocation_total"] == "2.5"
        assert exc.value.extra["session_actual_hours"] == "2.0"
        session.refresh_from_db()
        assert session.status == SessionStatus.SCHEDULED
        assert session.version == 1
        assert not SessionProgress.objects.exists()

        result = record_completion(
            teacher,
            session,
            action="complete",
            actual_hours="2.0",
            allocations=[{"plan_item_id": item.id, "actual_hours": "1.5"}],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )
        assert result.status == SessionStatus.COMPLETED
        progress = offering_progress(offering, today=date(2026, 10, 26), now=FIXED_NOW)
        assert progress["completed_hours"] == "2.0"
        assert progress["unallocated_hours"] == "0.5"
        assert progress["items"][0]["actual_hours"] == "1.5"
        assert progress["items"][0]["shortfall"] == "0.0"


class TestCorrectionAndRevert:
    def test_correct_requires_reason_and_revert_clears_completion_and_allocations(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "CORRECT", lead=teacher, target_hours=20)
        plan = _make_plan(demo, offering, teacher, [plan_item_payload("专题更正", "10.0")])
        item = plan.items.get()
        session = make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )

        from apps.teaching.completion import record_completion

        record_completion(
            teacher,
            session,
            action="complete",
            actual_hours="2.0",
            allocations=[{"plan_item_id": item.id, "actual_hours": "1.0"}],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )

        with pytest.raises(TeachingProgressError) as exc:
            record_completion(
                teacher,
                session,
                action="correct",
                actual_hours="1.5",
                allocations=[{"plan_item_id": item.id, "actual_hours": "1.0"}],
                reason="   ",
                base_version=2,
                now=FIXED_NOW,
            )
        assert exc.value.status_code == 400
        assert "reason" in exc.value.field_errors

        corrected = record_completion(
            teacher,
            session,
            action="correct",
            actual_hours="1.5",
            allocations=[{"plan_item_id": item.id, "actual_hours": "1.0"}],
            reason="核对后更正实际学时",
            base_version=2,
            now=FIXED_NOW,
        )
        assert corrected.version == 3
        assert corrected.actual_hours == 1.5
        assert CompletionRecord.objects.filter(session=session).count() == 2

        reverted = record_completion(
            teacher,
            session,
            action="revert",
            actual_hours=None,
            allocations=None,
            reason="误确认，撤销后重新登记",
            base_version=3,
            now=FIXED_NOW,
        )
        assert reverted.status == SessionStatus.SCHEDULED
        assert reverted.actual_hours is None
        assert reverted.completed_at is None
        assert reverted.completed_by_teacher_id is None
        assert reverted.version == 4
        assert SessionProgress.objects.filter(session=session).count() == 0
        assert CompletionRecord.objects.filter(session=session).count() == 3

        progress = offering_progress(offering, today=date(2026, 10, 26), now=FIXED_NOW)
        assert progress["completed_hours"] == "0.0"
        assert progress["items"][0]["actual_hours"] == "0.0"
        assert progress["unallocated_hours"] == "0.0"


class TestVersionsChangeLogAndNotes:
    def test_version_conflict_changelog_and_note_save_boundary(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "VERSION", lead=teacher)
        session = make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )
        teacher_client = _linked_client(teacher, "version")

        from apps.teaching.completion import record_completion

        with pytest.raises(TeachingProgressError) as stale:
            record_completion(
                teacher,
                session,
                action="complete",
                actual_hours="2.0",
                allocations=[],
                reason="",
                base_version=9,
                now=FIXED_NOW,
            )
        assert stale.value.status_code == 409
        assert stale.value.code == "SESSION_VERSION_CONFLICT"
        assert stale.value.extra["current"]["version"] == 1

        completed = record_completion(
            teacher,
            session,
            action="complete",
            actual_hours="2.0",
            allocations=[],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )
        assert completed.version == 2
        log = ChangeLog.objects.get(
            entity_type=ChangeEntityType.SESSION,
            entity_id=session.id,
        )
        assert log.version == 2
        assert log.offering_id == offering.id

        saved = save_note(
            teacher_client,
            session,
            **note_payload(base_version=0, client_mutation_id="t11-note-version"),
        )
        assert saved.status_code == 200, saved.data
        session.refresh_from_db()
        assert session.version == 2
        assert session.status == SessionStatus.COMPLETED
        assert session.actual_hours == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_has_exactly_one_winner(demo_term) -> None:
    demo = demo_term
    teacher1, teacher2 = demo.teachers["0001"], demo.teachers["0002"]
    offering = make_course(demo, "CONCURRENT", lead=teacher1, co_teachers=(teacher2,))
    session = make_session(
        offering,
        day=date(2026, 10, 19),
        teachers=(teacher1, teacher2),
        classroom=demo.classrooms["A301"],
    )
    barrier = threading.Barrier(2)

    def worker(teacher_id) -> dict:
        close_old_connections()
        try:
            teacher = TeacherProfile.objects.get(pk=teacher_id)
            locked = ClassSession.objects.select_related("offering__term", "offering__course").get(pk=session.pk)
            barrier.wait(timeout=10)
            from apps.teaching.completion import record_completion

            record_completion(
                teacher,
                locked,
                action="complete",
                actual_hours="2.0",
                allocations=[],
                reason="",
                base_version=1,
                now=timezone.now(),
            )
            return {"ok": True}
        except TeachingProgressError as exc:
            return {"ok": False, "status": exc.status_code, "code": exc.code}
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (teacher1.id, teacher2.id)))

    assert sum(1 for result in results if result["ok"]) == 1
    loser = next(result for result in results if not result["ok"])
    assert loser["status"] == 409
    assert loser["code"] == "SESSION_VERSION_CONFLICT"
    final = ClassSession.objects.get(pk=session.pk)
    assert final.status == SessionStatus.COMPLETED
    assert final.actual_hours == 2
    assert final.version == 2
    assert CompletionRecord.objects.filter(session=final).count() == 1
    assert TeachingPlan.objects.count() == 0
