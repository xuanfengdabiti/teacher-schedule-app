"""Compatible chat-completions client for the AI lesson-preparation feature.

Only the Python standard library is used: one JSON POST to a configurable
OpenAI-compatible endpoint.  The client never logs or returns request headers
or the API key, and it makes no network call when configured with an empty key.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

from apps.teaching.exceptions import (
    lesson_ai_invalid_response,
    lesson_ai_not_configured,
    lesson_ai_request_failed,
    lesson_ai_timeout,
    lesson_ai_unavailable,
)

USER_AGENT = "TeacherScheduleDemo/1.0"

SECTION_KEYS = ("outline", "case_study", "discussion_questions", "preparation")
MAX_SECTION_CHARS = 2000

_SYSTEM_PROMPT = """你是一位研究生课程的任课教师助手，请按本次课给定的授课分钟数准备课堂。
请只输出一个 JSON 对象，不要输出 Markdown 代码块或任何额外说明。
JSON 必须包含且仅包含以下四个字符串字段，字段值使用简体中文：
{
  "outline": "授课提纲，约 3 条，用换行或序号分隔",
  "case_study": "一个明确标注为虚构的教学案例，2-4 句话",
  "discussion_questions": "约 2 个课堂讨论问题",
  "preparation": "课前准备，短条目，1-3 条"
}
内容合计控制在 800 字以内，简短可读。课堂案例必须是虚构的教学情境，不要编造真实判例、真实文献编号或真实人名。
备课内容供教师参考和修改，不替代教师本人的专业判断。"""


def _api_key() -> str:
    return str(getattr(settings, "TEACHING_AI_API_KEY", "") or "").strip()


def _base_url() -> str:
    return str(getattr(settings, "TEACHING_AI_API_BASE_URL", "") or "").strip().rstrip("/")


def _model() -> str:
    return str(getattr(settings, "TEACHING_AI_MODEL", "") or "").strip()


def _timeout() -> float:
    try:
        value = float(getattr(settings, "TEACHING_AI_TIMEOUT_SECONDS", 45))
    except (TypeError, ValueError):
        return 45.0
    return value if value > 0 else 45.0


def _endpoint() -> str:
    base = _base_url()
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _is_deepseek_official() -> bool:
    """Only the documented provider gets its ``thinking`` extension."""
    host = urllib.parse.urlparse(_base_url()).hostname or ""
    return host == "api.deepseek.com" or host.endswith(".deepseek.com")


def build_user_prompt(context: dict[str, Any], topic: str) -> str:
    """Render the fixed context contract into the user message.

    ``context`` is the shape returned by ``lesson_context``: ``plan_items`` is a
    list of ``{title, planned_hours}`` and the previous note is
    ``previous_note``.  Both must actually reach the model.
    """
    plan_items = context.get("plan_items") or []
    plan = (
        "、".join(
            str(item.get("title", "")).strip()
            for item in plan_items
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        )
        or "（暂无教学计划）"
    )
    previous = str(context.get("previous_note") or "").strip() or "（暂无上次备注）"
    return "\n".join(
        [
            f"课程：{context.get('course_name', '')}",
            f"本次课：{context.get('date', '')} 第{context.get('start_period')}-"
            f"{context.get('end_period')}节，授课 {context.get('teaching_minutes')} 分钟",
            f"教学计划专题：{plan}",
            f"上次备注：{previous}",
            f"本节主题：{topic}",
            "请按系统消息要求返回 JSON。",
        ]
    )


def _request_json(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _endpoint(),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {_api_key()}",
            # Some compatible gateways reject urllib's default agent; identify
            # the real application instead of impersonating a browser.
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:  # noqa: S310 - configured HTTPS endpoint
        raw = response.read().decode("utf-8", "replace")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise lesson_ai_invalid_response() from exc
    if not isinstance(parsed, dict):
        raise lesson_ai_invalid_response()
    return parsed


def _extract_sections(parsed: dict[str, Any]) -> dict[str, str]:
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise lesson_ai_invalid_response()
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise lesson_ai_invalid_response()
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise lesson_ai_invalid_response() from exc
    if not isinstance(data, dict):
        raise lesson_ai_invalid_response()

    sections: dict[str, str] = {}
    for key in SECTION_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise lesson_ai_invalid_response()
        sections[key] = value.strip()[:MAX_SECTION_CHARS]
    return sections


def generate_lesson_sections(context: dict[str, Any], topic: str) -> tuple[dict[str, str], str]:
    """Return ``({four sections}, model_name)`` or raise a stable error."""
    if not _api_key() or not _base_url() or not _model():
        raise lesson_ai_not_configured()

    payload: dict[str, Any] = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(context, topic)},
        ],
        "stream": False,
        "max_tokens": 2200,
        "response_format": {"type": "json_object"},
    }
    if _is_deepseek_official():
        # Documented DeepSeek extension: disable thinking to keep latency low.
        payload["thinking"] = {"type": "disabled"}

    try:
        parsed = _request_json(payload)
    except TimeoutError as exc:
        raise lesson_ai_timeout() from exc
    except urllib.error.HTTPError as exc:
        raise lesson_ai_request_failed() from exc
    except (urllib.error.URLError, OSError) as exc:
        # URLError wraps socket timeouts on some platforms.
        if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
            raise lesson_ai_timeout() from exc
        raise lesson_ai_unavailable() from exc

    return _extract_sections(parsed), _model()
