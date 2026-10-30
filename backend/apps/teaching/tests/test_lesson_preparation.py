"""AI lesson-preparation API: context, generation contract, draft persistence."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest import mock

import pytest

from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching import lesson_ai
from apps.teaching.exceptions import (
    LessonPreparationError,
    lesson_ai_invalid_response,
)
from apps.teaching.models import CompletionRecord, LessonPreparation, TeachingNote
from apps.teaching.tests.helpers import auth_client, link_user, note_payload, save_note
from apps.teaching.tests.plan_helpers import save_plan_api

SECTION_KEYS = ("outline", "case_study", "discussion_questions", "preparation")


def _session_for(demo, teacher) -> ClassSession:
    """A later session so an earlier same-offering session always exists."""
    sessions = list(
        ClassSession.objects.filter(
            offering__term=demo.term,
            session_teachers__teacher=teacher,
            status=SessionStatus.SCHEDULED,
        ).order_by("start_at")
    )
    if len(sessions) < 3:
        pytest.skip("演示数据缺少足够的同开课课次")
    return sessions[2]


def _earlier_session(demo, session: ClassSession, teacher) -> ClassSession:
    earlier = (
        ClassSession.objects.filter(
            offering=session.offering,
            session_teachers__teacher=teacher,
            start_at__lt=session.start_at,
        )
        .order_by("-start_at")
        .first()
    )
    if earlier is None:
        pytest.skip("演示数据缺少更早的同开课课次")
    return earlier


def _use_40_minute_hours(session: ClassSession) -> ClassSession:
    """The demo term uses 45-minute hours; the fixed example is 2×40=80."""
    session.offering.minutes_per_hour = 40
    session.offering.save(update_fields=["minutes_per_hour", "updated_at"])
    return session


def _completion(session: ClassSession) -> BytesIO:
    """Minimal fake HTTP response for urllib.request.urlopen."""
    body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "outline": "一、回顾\n二、案例讨论\n三、总结",
                            "case_study": "虚构案例：某仓库监控记录缺失，需推断证据链。",
                            "discussion_questions": "1. 如何补强证据？\n2. 如何排除合理怀疑？",
                            "preparation": "打印虚构案例材料。",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    return BytesIO(json.dumps(body, ensure_ascii=False).encode("utf-8"))


@pytest.fixture(autouse=True)
def _ai_settings(settings):
    settings.TEACHING_AI_API_BASE_URL = "https://ai.example.invalid/v1"
    settings.TEACHING_AI_MODEL = "test-model"
    settings.TEACHING_AI_API_KEY = "test-key"
    settings.TEACHING_AI_TIMEOUT_SECONDS = 45
    return settings


def test_context_reports_80_minutes_and_private_previous_note(generated_demo):
    demo = generated_demo
    teacher = demo.teachers["0001"]
    user = link_user(teacher, "prep-context@example.invalid")
    target = _use_40_minute_hours(_session_for(demo, teacher))
    earlier = _earlier_session(demo, target, teacher)
    save_note(
        auth_client(user),
        earlier,
        **note_payload(
            next_plan="继续上次案例讨论，补充两道练习。",
            client_mutation_id="prep-context-note",
        ),
    )

    response = auth_client(user).get(f"/api/v1/sessions/{target.id}/preparation/")

    assert response.status_code == 200
    body = response.json()
    assert body["draft"] is None
    assert body["ai_available"] is True
    context = body["context"]
    assert context["teaching_minutes"] == 80
    assert context["previous_note"] == "继续上次案例讨论，补充两道练习。"
    assert context["plan_items"] == []


def test_generation_sends_context_to_the_real_client_path(generated_demo, settings):
    demo = generated_demo
    teacher = demo.teachers["0001"]
    user = link_user(teacher, "prep-generate@example.invalid")
    target = _use_40_minute_hours(_session_for(demo, teacher))
    earlier = _earlier_session(demo, target, teacher)
    client = auth_client(user)
    save_note(
        client,
        earlier,
        **note_payload(
            next_plan="上次讲到证据链审查，带一个案例。",
            client_mutation_id="prep-generate-note",
        ),
    )
    save_plan_api(
        client,
        target.offering,
        [{"title": "虚构专题：证据链审查", "planned_hours": "4.0"}],
    )

    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        captured["user_agent"] = request.get_header("User-agent")
        return _completion(target)

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        response = client.post(
            f"/api/v1/sessions/{target.id}/preparation/generate/",
            {"topic": "证据链审查"},
            format="json",
        )

    assert response.status_code == 200
    body = response.json()
    assert set(SECTION_KEYS) <= body.keys()
    assert body["generated_model"] == "test-model"

    payload = captured["payload"]
    assert captured["url"] == "https://ai.example.invalid/v1/chat/completions"
    assert captured["timeout"] == 45
    assert captured["user_agent"] == "TeacherScheduleDemo/1.0", "真实应用标识随请求发出"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    assert "thinking" not in payload, "非 DeepSeek 官方域不塞供应商扩展字段"
    user_message = payload["messages"][1]["content"]
    assert "上次讲到证据链审查，带一个案例。" in user_message
    assert "虚构专题：证据链审查" in user_message
    assert "80 分钟" in user_message

    # Generation never persists a draft.
    assert LessonPreparation.objects.count() == 0


def test_generation_rejects_empty_or_missing_sections(generated_demo):
    demo = generated_demo
    teacher = demo.teachers["0001"]
    user = link_user(teacher, "prep-empty@example.invalid")
    target = _use_40_minute_hours(_session_for(demo, teacher))

    body = {"choices": [{"message": {"content": '{"outline": "只有提纲"}'}}]}
    with mock.patch(
        "urllib.request.urlopen",
        lambda request, timeout=None: BytesIO(json.dumps(body).encode("utf-8")),
    ):
        response = auth_client(user).post(
            f"/api/v1/sessions/{target.id}/preparation/generate/",
            {"topic": "证据链审查"},
            format="json",
        )

    assert response.status_code == 502
    assert response.json()["code"] == "AI_INVALID_RESPONSE"
    assert LessonPreparation.objects.count() == 0


def test_generation_without_key_does_not_claim_success(generated_demo, settings):
    demo = generated_demo
    teacher = demo.teachers["0001"]
    user = link_user(teacher, "prep-nokey@example.invalid")
    target = _use_40_minute_hours(_session_for(demo, teacher))
    settings.TEACHING_AI_API_KEY = ""

    def explode(*args, **kwargs):
        raise AssertionError("缺少密钥时不得发起网络请求")

    with mock.patch("urllib.request.urlopen", explode):
        response = auth_client(user).post(
            f"/api/v1/sessions/{target.id}/preparation/generate/",
            {"topic": "证据链审查"},
            format="json",
        )

    assert response.status_code == 503
    assert response.json()["code"] == "AI_NOT_CONFIGURED"
    assert LessonPreparation.objects.count() == 0


def test_client_timeout_and_http_and_bad_json_are_stable_errors(generated_demo):
    context = {
        "course_name": "刑事证据审查实务（示例）",
        "date": "2026-10-28",
        "start_period": 1,
        "end_period": 2,
        "teaching_minutes": 80,
        "previous_note": "继续上次案例讨论。",
        "plan_items": [{"title": "证据链审查", "planned_hours": "4.0"}],
    }

    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(LessonPreparationError) as timeout_error:
            lesson_ai.generate_lesson_sections(context, "证据链")
    assert timeout_error.value.code == "AI_TIMEOUT"

    http_error = urllib.error.HTTPError(
        "https://ai.example.invalid/v1/chat/completions",
        500,
        "boom",
        {},
        None,
    )
    with mock.patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(LessonPreparationError) as http_failure:
            lesson_ai.generate_lesson_sections(context, "证据链")
    assert http_failure.value.code == "AI_REQUEST_FAILED"

    with mock.patch(
        "urllib.request.urlopen",
        lambda request, timeout=None: BytesIO(b"not json at all"),
    ):
        with pytest.raises(LessonPreparationError) as bad_json:
            lesson_ai.generate_lesson_sections(context, "证据链")
    assert bad_json.value.code == "AI_INVALID_RESPONSE"


def test_save_and_reread_keeps_draft_private_and_does_not_touch_notes_or_hours(generated_demo):
    demo = generated_demo
    teacher = demo.teachers["0001"]
    other = demo.teachers["0002"]
    user = link_user(teacher, "prep-save@example.invalid")
    other_user = link_user(other, "prep-save-other@example.invalid")
    target = _use_40_minute_hours(_session_for(demo, teacher))
    earlier = _earlier_session(demo, target, teacher)
    client = auth_client(user)
    save_note(
        client,
        earlier,
        **note_payload(
            next_plan="原有备注保持不动。",
            client_mutation_id="prep-save-note",
        ),
    )

    note_before = TeachingNote.objects.get(session=earlier, author=teacher)
    session_before = ClassSession.objects.get(pk=target.pk)
    completions_before = CompletionRecord.objects.count()

    payload = {
        "base_version": 0,
        "topic": "证据链审查",
        "outline": "一、回顾\n二、练习",
        "case_study": "虚构案例：仓库监控缺失。",
        "discussion_questions": "1. 如何补强？",
        "preparation": "准备虚构材料。",
        "generated_model": "test-model",
        "generated_at": "2026-10-28T09:00:00+08:00",
    }
    saved = client.put(
        f"/api/v1/sessions/{target.id}/preparation/",
        payload,
        format="json",
    )
    assert saved.status_code == 200
    draft = saved.json()
    assert draft["topic"] == "证据链审查"
    assert draft["version"] == 1
    assert "context" not in draft, "PUT 直接返回完整草稿"

    reread = client.get(f"/api/v1/sessions/{target.id}/preparation/").json()
    assert reread["draft"]["outline"] == "一、回顾\n二、练习"
    assert reread["draft"]["generated_model"] == "test-model"

    # Another session teacher does not see my draft.
    other_view = auth_client(other_user).get(f"/api/v1/sessions/{target.id}/preparation/")
    if other_view.status_code == 200:
        assert other_view.json()["draft"] is None
    else:
        assert other_view.status_code in {403, 404}

    note_after = TeachingNote.objects.get(session=earlier, author=teacher)
    assert note_after.version == note_before.version
    assert note_after.draft_next_plan == note_before.draft_next_plan
    session_after = ClassSession.objects.get(pk=target.pk)
    assert session_after.status == session_before.status
    assert session_after.actual_hours == session_before.actual_hours
    assert session_after.version == session_before.version
    assert CompletionRecord.objects.count() == completions_before


def test_stale_base_version_conflicts_and_keeps_saved_draft(generated_demo):
    demo = generated_demo
    teacher = demo.teachers["0001"]
    user = link_user(teacher, "prep-conflict@example.invalid")
    target = _use_40_minute_hours(_session_for(demo, teacher))
    client = auth_client(user)
    base = {
        "base_version": 0,
        "topic": "第一次",
        "outline": "提纲一",
        "case_study": "案例一",
        "discussion_questions": "问题一",
        "preparation": "准备一",
    }
    assert client.put(f"/api/v1/sessions/{target.id}/preparation/", base, format="json").status_code == 200

    stale = dict(base, base_version=0, topic="覆盖尝试")
    conflict = client.put(f"/api/v1/sessions/{target.id}/preparation/", stale, format="json")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "PREPARATION_VERSION_CONFLICT"
    assert conflict.json()["current"]["topic"] == "第一次"

    still = client.get(f"/api/v1/sessions/{target.id}/preparation/").json()["draft"]
    assert still["topic"] == "第一次"


def test_lesson_ai_rejects_missing_sections_directly():
    payload = {"choices": [{"message": {"content": '{"outline": "", "case_study": "x"}'}}]}
    with mock.patch(
        "urllib.request.urlopen",
        lambda request, timeout=None: BytesIO(json.dumps(payload).encode("utf-8")),
    ):
        with pytest.raises(LessonPreparationError) as error:
            lesson_ai.generate_lesson_sections(
                {
                    "course_name": "课程",
                    "date": "2026-10-28",
                    "start_period": 1,
                    "end_period": 2,
                    "teaching_minutes": 80,
                    "previous_note": "",
                    "plan_items": [],
                },
                "主题",
            )
    assert error.value.code == "AI_INVALID_RESPONSE"
    assert error.value is not None
    assert lesson_ai_invalid_response().code == "AI_INVALID_RESPONSE"
