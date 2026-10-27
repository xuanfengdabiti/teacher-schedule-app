"""Schedule-card "previous note" preview contract (B13 read extension).

The preview is a convenience reminder: it does not require the earlier class to
be completed and never replaces the B20 ``previous_completed_session`` record.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import TeacherStatus
from apps.scheduling.models import ClassSession, SessionStatus
from apps.scheduling.services import generate_sessions_for_term
from apps.teaching.models import TeachingNote

pytestmark = pytest.mark.django_db


@pytest.fixture
def generated_demo(demo_term):
    generate_sessions_for_term(demo_term.term)
    return demo_term


def _client_for(teacher) -> APIClient:
    user = get_user_model().objects.create_user(
        email=f"{teacher.teacher_no}-preview@example.invalid",
        password="pass-12345",
    )
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def _session(demo, rule_code: str, origin: date) -> ClassSession:
    return ClassSession.objects.get(rule=demo.rules[rule_code], origin_teaching_date=origin)


def _week(client: APIClient, *, date_from: date, date_to: date, scope: str = "mine"):
    return client.get(
        "/api/v1/sessions/",
        {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "scope": scope,
        },
    )


def _by_course(body, course_code: str):
    return next(item for item in body["results"] if item["course_code"] == course_code)


def _note(session: ClassSession, author, **values) -> TeachingNote:
    return TeachingNote.objects.create(session=session, author=author, **values)


class TestOwnDraftPreview:
    def test_saved_draft_appears_on_next_session_of_same_offering(self, generated_demo) -> None:
        teacher = generated_demo.teachers["0001"]
        earlier = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        _note(
            earlier,
            teacher,
            draft_unfinished="继续讲解现场勘查记录。",
            draft_next_plan="下次讲\n\n  痕迹提取与比对。  ",
            version=1,
        )
        client = _client_for(teacher)

        response = _week(client, date_from=date(2026, 11, 2), date_to=date(2026, 11, 9))

        assert response.status_code == 200
        session = _by_course(response.data, "C-CRIME")
        assert session["previous_note_preview"] == "下次讲 痕迹提取与比对。"
        assert "version" in session and "object_id" not in session

    def test_next_plan_wins_over_unfinished_and_covered(self, generated_demo) -> None:
        teacher = generated_demo.teachers["0001"]
        earlier = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        _note(
            earlier,
            teacher,
            draft_covered="已讲内容甲",
            draft_unfinished="未完成内容乙",
            draft_next_plan="下次计划丙",
            version=1,
        )
        client = _client_for(teacher)

        body = _week(client, date_from=date(2026, 11, 2), date_to=date(2026, 11, 9)).data

        assert _by_course(body, "C-CRIME")["previous_note_preview"] == "下次计划丙"

    def test_long_text_is_shortened_but_full_text_is_kept(self, generated_demo) -> None:
        teacher = generated_demo.teachers["0001"]
        earlier = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        long_text = "痕迹" * 100
        note = _note(earlier, teacher, draft_next_plan=long_text, version=1)
        client = _client_for(teacher)

        body = _week(client, date_from=date(2026, 11, 2), date_to=date(2026, 11, 9)).data
        preview = _by_course(body, "C-CRIME")["previous_note_preview"]

        assert len(preview) <= 81
        assert preview.endswith("…")
        note.refresh_from_db()
        assert note.draft_next_plan == long_text

    def test_detail_summary_has_same_preview_and_unchanged_times(self, generated_demo) -> None:
        teacher = generated_demo.teachers["0001"]
        earlier = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        target = _session(generated_demo, "R-CRIME-ODD", date(2026, 11, 2))
        _note(earlier, teacher, draft_next_plan="下次带案例材料", version=1)
        client = _client_for(teacher)

        response = client.get(f"/api/v1/sessions/{target.id}/")
        body = _week(client, date_from=date(2026, 11, 2), date_to=date(2026, 11, 9)).data
        listed = _by_course(body, "C-CRIME")

        assert response.status_code == 200
        assert response.data["previous_note_preview"] == "下次带案例材料"
        assert listed["previous_note_preview"] == "下次带案例材料"
        assert listed["start_at"] == "2026-11-02T08:00:00+08:00"
        assert listed["end_at"] == "2026-11-02T09:40:00+08:00"


class TestSharedAndIsolation:
    def test_other_authors_draft_is_hidden_until_published(self, generated_demo) -> None:
        author = generated_demo.teachers["0001"]
        viewer = generated_demo.teachers["0002"]
        earlier = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        note = _note(earlier, author, draft_next_plan="别人的内部草稿", version=1)
        client = _client_for(viewer)

        before = _week(client, date_from=date(2026, 10, 26), date_to=date(2026, 11, 2)).data
        assert _by_course(before, "C-CRIME")["previous_note_preview"] == ""

        note.published_next_plan = "已共享的下次计划"
        note.published_version = 2
        note.published_at = note.created_at
        note.save(update_fields=["published_next_plan", "published_version", "published_at", "updated_at"])

        after = _week(client, date_from=date(2026, 10, 26), date_to=date(2026, 11, 2)).data
        assert _by_course(after, "C-CRIME")["previous_note_preview"] == "已共享的下次计划"

    def test_cancelled_future_and_other_offering_notes_do_not_leak(self, generated_demo) -> None:
        teacher = generated_demo.teachers["0001"]
        cancelled = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 5))
        future = _session(generated_demo, "R-CRIME-ODD", date(2026, 11, 2))
        data_session = _session(generated_demo, "R-DATA-MON", date(2026, 10, 12))
        cancelled.status = SessionStatus.CANCELLED
        cancelled.actual_hours = Decimal("0")
        cancelled.save(update_fields=["status", "actual_hours", "updated_at"])
        _note(cancelled, teacher, draft_next_plan="取消课不应出现", version=1)
        _note(future, teacher, draft_next_plan="未来课不应倒灌", version=1)
        _note(data_session, teacher, draft_next_plan="别的课程", version=1)
        client = _client_for(teacher)

        body = _week(client, date_from=date(2026, 10, 19), date_to=date(2026, 10, 26)).data

        assert _by_course(body, "C-CRIME")["previous_note_preview"] == ""
        assert _by_course(body, "C-DATA")["previous_note_preview"] == "别的课程"

    def test_no_notes_returns_empty_string_by_default(self, generated_demo) -> None:
        client = _client_for(generated_demo.teachers["0001"])

        body = _week(client, date_from=date(2026, 10, 26), date_to=date(2026, 11, 2)).data

        assert all(item["previous_note_preview"] == "" for item in body["results"])

    def test_preview_does_not_require_completed_source(self, generated_demo) -> None:
        teacher = generated_demo.teachers["0001"]
        earlier = _session(generated_demo, "R-CRIME-ODD", date(2026, 10, 19))
        assert earlier.status == SessionStatus.SCHEDULED
        _note(earlier, teacher, draft_covered="尚未确认完成也能给下次看", version=1)
        client = _client_for(teacher)

        body = _week(client, date_from=date(2026, 11, 2), date_to=date(2026, 11, 9)).data

        assert _by_course(body, "C-CRIME")["previous_note_preview"] == "尚未确认完成也能给下次看"


def test_list_query_count_does_not_grow_with_note_count(generated_demo, django_assert_max_num_queries) -> None:
    teacher = generated_demo.teachers["0001"]
    client = _client_for(teacher)
    sessions = list(ClassSession.objects.filter(session_teachers__teacher=teacher).order_by("start_at")[:6])
    assert len(sessions) == 6

    with django_assert_max_num_queries(8) as without_notes:
        first = _week(client, date_from=date(2026, 10, 26), date_to=date(2026, 11, 2))
    for index, session in enumerate(sessions):
        _note(session, teacher, draft_next_plan=f"批量摘要 {index}", version=1)
    with django_assert_max_num_queries(8) as with_notes:
        second = _week(client, date_from=date(2026, 10, 26), date_to=date(2026, 11, 2))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(without_notes.captured_queries) == len(with_notes.captured_queries)
    print(
        "\nprevious_note_preview query counts: "
        f"without notes={len(without_notes.captured_queries)}, "
        f"with notes={len(with_notes.captured_queries)}"
    )
