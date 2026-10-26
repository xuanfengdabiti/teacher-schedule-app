"""B22 plan editing, revision history, permissions and progress tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.academics.models import TeacherProfile
from apps.scheduling.tests import factories
from apps.teaching.models import PlanRevision, SessionProgress, TeachingPlan
from apps.teaching.plans import save_plan
from apps.teaching.progress import offering_progress
from apps.teaching.tests.helpers import FIXED_NOW, auth_client, link_user
from apps.teaching.tests.plan_helpers import (
    completion_api,
    make_course,
    make_session,
    plan_item_payload,
    save_plan_api,
)

pytestmark = pytest.mark.django_db


def _client(teacher: TeacherProfile, key: str):
    return auth_client(link_user(teacher, f"t11-plan-{key}@example.invalid"))


class TestPlanEditing:
    def test_non_owner_cannot_save_and_version_conflict_is_409(self, demo_term) -> None:
        demo = demo_term
        lead, co_teacher = demo.teachers["0001"], demo.teachers["0002"]
        offering = make_course(demo, "PLAN-PERM", lead=lead, co_teachers=(co_teacher,))
        lead_client = _client(lead, "owner")
        co_client = _client(co_teacher, "co")

        denied = save_plan_api(co_client, offering, [plan_item_payload("无权专题", "4.0")])
        assert denied.status_code == 403
        assert denied.data["code"] == "NOT_PLAN_OWNER"
        assert not TeachingPlan.objects.exists()

        created = save_plan_api(lead_client, offering, [plan_item_payload("第一版", "4.0")])
        assert created.status_code == 200, created.data
        assert created.data["plan"]["version"] == 1
        assert created.data["can_edit"] is True
        assert not co_client.get(f"/api/v1/offerings/{offering.id}/plan/").data["can_edit"]

        conflict = save_plan_api(
            lead_client,
            offering,
            [plan_item_payload("基于旧版本", "4.0")],
            base_version=0,
        )
        assert conflict.status_code == 409
        assert conflict.data["code"] == "PLAN_VERSION_CONFLICT"
        assert conflict.data["current"]["version"] == 1
        assert conflict.data["current"]["items"][0]["title"] == "第一版"

    def test_item_ids_stay_stable_and_every_save_appends_a_revision(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "PLAN-REV", lead=teacher)
        client = _client(teacher, "revision")

        created = save_plan_api(client, offering, [plan_item_payload("稳定专题", "4.0")])
        assert created.status_code == 200, created.data
        item_id = created.data["plan"]["items"][0]["id"]

        updated = save_plan_api(
            client,
            offering,
            [plan_item_payload("稳定专题（更名）", "5.0", item_id=item_id)],
            base_version=1,
        )
        assert updated.status_code == 200, updated.data
        assert updated.data["plan"]["version"] == 2
        assert [item["id"] for item in updated.data["plan"]["items"]] == [item_id]
        assert updated.data["plan"]["items"][0]["planned_hours"] == "5.0"

        revisions = list(PlanRevision.objects.filter(plan__offering=offering).order_by("version"))
        assert [revision.version for revision in revisions] == [1, 2]
        assert revisions[0].items_json[0]["title"] == "稳定专题"
        assert revisions[1].items_json[0]["title"] == "稳定专题（更名）"
        assert revisions[0].items_json[0]["planned_hours"] == "4.0"

    def test_missing_item_with_progress_is_archived_and_keeps_session_progress(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "PLAN-ARCHIVE", lead=teacher)
        client = _client(teacher, "archive")
        created = save_plan_api(
            client,
            offering,
            [plan_item_payload("有历史分配", "6.0"), plan_item_payload("保留专题", "4.0")],
        )
        assert created.status_code == 200
        first, second = created.data["plan"]["items"]
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
            allocations=[{"plan_item_id": first["id"], "actual_hours": "1.0"}],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )

        trimmed = save_plan_api(
            client,
            offering,
            [plan_item_payload("保留专题", "4.0", item_id=second["id"])],
            base_version=1,
        )
        assert trimmed.status_code == 200, trimmed.data
        items = {item["id"]: item for item in trimmed.data["plan"]["items"]}
        assert items[first["id"]]["archived"] is True
        assert items[first["id"]]["position"] == 2
        assert items[second["id"]]["archived"] is False
        assert SessionProgress.objects.filter(plan_item_id=first["id"]).count() == 1

        denied = completion_api(
            client,
            session,
            action="correct",
            actual_hours="2.0",
            allocations=[{"plan_item_id": first["id"], "actual_hours": "1.0"}],
            reason="尝试给归档专题重新分配",
            base_version=2,
        )
        assert denied.status_code == 400
        assert denied.data["code"] == "INVALID_ALLOCATION"

        progress = client.get(f"/api/v1/offerings/{offering.id}/plan/")
        archived_progress = next(
            row for row in progress.data["progress"]["items"] if row["plan_item_id"] == first["id"]
        )
        assert archived_progress["archived"] is True

    def test_missing_item_without_progress_is_deleted_and_revisions_remain(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "PLAN-DELETE", lead=teacher)
        client = _client(teacher, "delete")
        created = save_plan_api(
            client,
            offering,
            [plan_item_payload("保留", "4.0"), plan_item_payload("删除", "3.0")],
        )
        assert created.status_code == 200
        keep, remove = created.data["plan"]["items"]

        updated = save_plan_api(
            client,
            offering,
            [plan_item_payload("保留", "4.0", item_id=keep["id"])],
            base_version=1,
        )
        assert updated.status_code == 200
        assert [item["id"] for item in updated.data["plan"]["items"]] == [keep["id"]]
        assert not SessionProgress.objects.filter(plan_item_id=remove["id"]).exists()
        assert PlanRevision.objects.filter(plan__offering=offering).count() == 2
        assert len(PlanRevision.objects.get(plan__offering=offering, version=1).items_json) == 2

    def test_over_hours_are_reported_separately(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "OVER", lead=teacher, target_hours=2)
        client = _client(teacher, "over")
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
            actual_hours="5.0",
            allocations=[],
            reason="",
            base_version=1,
            now=FIXED_NOW,
        )

        response = client.get(f"/api/v1/offerings/{offering.id}/plan/")
        assert response.status_code == 200
        progress = response.data["progress"]
        assert progress["target_hours"] == "2.0"
        assert progress["completed_hours"] == "5.0"
        assert progress["remaining_hours"] == "0.0"
        assert progress["over_hours"] == "3.0"
        assert progress["plan_status"] == "not_set"


class TestProgressQueryCount:
    def test_query_count_is_constant_when_session_count_grows_fivefold(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "QUERY", lead=teacher)
        save_plan(teacher, offering, items=[plan_item_payload("查询专题", "10.0")], base_version=0, now=FIXED_NOW)
        make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(teacher,),
            classroom=demo.classrooms["A301"],
        )

        def count_queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                result = offering_progress(offering, today=date(2026, 10, 26), now=FIXED_NOW)
            assert result["remaining_sessions"] >= 1
            return len(captured)

        before = count_queries()
        for offset in range(4):
            make_session(
                offering,
                day=date(2026, 10, 20) + timedelta(days=offset),
                teachers=(teacher,),
                classroom=demo.classrooms["A301"],
            )
        after = count_queries()
        print(f"offering_progress queries: {before} -> {after}")
        assert before == after == 3


class TestPermissions:
    def test_unlinked_disabled_same_class_and_invisible_access_codes(self, demo_term) -> None:
        demo = demo_term
        teacher = demo.teachers["0001"]
        offering = make_course(demo, "ACCESS", lead=teacher)
        same_class_teacher = factories.make_teacher(demo.school, "T11-ACCESS", "同班其他课程教师")
        other_offering = make_course(
            demo,
            "ACCESS-OTHER",
            lead=same_class_teacher,
            class_code="TC-T11-ACCESS",
        )
        session = make_session(
            other_offering,
            day=date(2026, 10, 19),
            teachers=(same_class_teacher,),
            classroom=demo.classrooms["A301"],
        )
        teacher_client = _client(teacher, "access")

        same_class = teacher_client.get(f"/api/v1/offerings/{other_offering.id}/plan/")
        assert same_class.status_code == 403
        assert same_class.data["code"] == "NOT_OFFERING_TEACHER"
        same_class_completion = teacher_client.get(f"/api/v1/sessions/{session.id}/completion/")
        assert same_class_completion.status_code == 403
        assert same_class_completion.data["code"] == "NOT_OFFERING_TEACHER"

        outsider = factories.make_teacher(demo.school, "T11-OUTSIDE", "不可见课程教师")
        outsider_offering = make_course(demo, "ACCESS-OUTSIDE", lead=outsider)
        invisible = teacher_client.get(f"/api/v1/offerings/{outsider_offering.id}/plan/")
        assert invisible.status_code == 404
        assert invisible.data["code"] == "NOT_FOUND"

        unlinked = get_user_model().objects.create_user(
            email="t11-unlinked@example.invalid",
            password="test-pass-123",
        )
        unlinked_response = auth_client(unlinked).get(f"/api/v1/offerings/{offering.id}/plan/")
        assert unlinked_response.status_code == 403
        assert unlinked_response.data["code"] == "TEACHER_NOT_LINKED"

        disabled_teacher = demo.teachers["0002"]
        disabled_client = _client(disabled_teacher, "disabled")
        disabled_teacher.is_active = False
        disabled_teacher.save(update_fields=["is_active", "updated_at"])
        disabled = disabled_client.get(f"/api/v1/offerings/{offering.id}/plan/")
        assert disabled.status_code == 403
        assert disabled.data["code"] == "TEACHER_DISABLED"

    def test_offering_teacher_who_is_not_session_teacher_cannot_complete(self, demo_term) -> None:
        demo = demo_term
        lead, co_teacher = demo.teachers["0001"], demo.teachers["0002"]
        offering = make_course(demo, "NOT-SESSION", lead=lead, co_teachers=(co_teacher,))
        session = make_session(
            offering,
            day=date(2026, 10, 19),
            teachers=(lead,),
            classroom=demo.classrooms["A301"],
        )
        co_client = _client(co_teacher, "not-session")
        response = completion_api(
            co_client,
            session,
            action="complete",
            actual_hours="2.0",
            base_version=1,
        )
        assert response.status_code == 403
        assert response.data["code"] == "NOT_SESSION_TEACHER"
