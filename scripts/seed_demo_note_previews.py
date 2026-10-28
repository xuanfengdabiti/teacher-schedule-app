"""本地演示：为教师甲准备课表卡片上的简短“备注”预览。

用途
----
课表卡片显示的是同一开课中更早课次的本人备注摘要。本脚本只给
teacher_no=0001（t0001@example.invalid）在演示日 2026-10-28 当天课程的
更早同开课课次写入一两句“下次课计划”，让主录屏账号直接看到备注。

边界
----
- 只走现成备注写入 API（``PUT /sessions/{id}/notes/mine/``），复用版本与幂等规则；
- 只处理本人实际任课、非取消的课次；不新增/删除课次，不登记学时，不发布共享；
- 已有任何草稿或已发布内容的备注一律跳过，绝不覆盖演示者输入；
- 重复运行新增为 0。

运行
----
必须直接执行脚本（``manage.py shell`` 的 ``__name__`` 不是 ``__main__``，
读入后不会运行 ``main``）。容器内已验证方式::

    docker cp scripts/seed_demo_note_previews.py \
      teacher-schedule-demo-api-1:/tmp/seed_demo_note_previews.py
    docker exec teacher-schedule-demo-api-1 \
      python /tmp/seed_demo_note_previews.py --date 2026-10-28

本地直接运行（自动配置 Django，可带 --date）::

    cd backend
    uv run python ../scripts/seed_demo_note_previews.py --date 2026-10-28

也可用环境变量 ``DEMO_NOTE_DATE`` 指定日期；默认 2026-10-28。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

DEFAULT_DATE = date(2026, 10, 28)
SCHOOL_CODE = "DEMO-SCHOOL"
TEACHER_NO = "0001"
DEMO_TZ = "Asia/Shanghai"
NOTE_TEXTS = (
    "继续上次案例讨论，补充两道练习。",
    "下次带一个真实案例，安排分组讨论。",
)


def _local_day_range(day: date):
    """School-local day boundaries; the demo term runs on Asia/Shanghai."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(DEMO_TZ)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _configure_django() -> None:
    """Configure Django when run as a standalone ``python`` script."""
    try:
        from django.conf import settings

        if settings.configured:
            return
    except Exception:  # noqa: BLE001 - import below gives the real error
        pass

    candidates = (
        Path(__file__).resolve().parent.parent / "backend",
        Path.cwd(),
        Path("/app"),
    )
    for candidate in candidates:
        if (candidate / "manage.py").exists():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            break
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django

    django.setup()


def _demo_date() -> date:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--date", dest="date")
    args, _ = parser.parse_known_args()
    raw = args.date or os.environ.get("DEMO_NOTE_DATE")
    if not raw:
        return DEFAULT_DATE
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"日期格式应为 YYYY-MM-DD，实际：{raw}") from exc


def _has_content(note) -> bool:
    if note is None:
        return False
    for field in (
        "draft_covered",
        "draft_unfinished",
        "draft_next_plan",
        "published_covered",
        "published_unfinished",
        "published_next_plan",
    ):
        if (getattr(note, field) or "").strip():
            return True
    return False


def _pick_target_sessions(teacher, demo_date: date):
    """One non-cancelled session per offering that this teacher actually teaches."""
    from apps.scheduling.models import ClassSession, SessionStatus

    day_start, day_end = _local_day_range(demo_date)
    sessions = (
        ClassSession.objects.filter(
            offering__term__school__code=SCHOOL_CODE,
            session_teachers__teacher=teacher,
            start_at__gte=day_start,
            start_at__lt=day_end,
        )
        .exclude(status=SessionStatus.CANCELLED)
        .select_related("offering")
        .order_by("start_at", "offering__offering_code", "id")
        .distinct()
    )
    picked = []
    seen_offerings: set = set()
    for session in sessions:
        if session.offering_id in seen_offerings:
            continue
        seen_offerings.add(session.offering_id)
        picked.append(session)
        if len(picked) == 2:
            break
    return picked


def _earlier_session(teacher, target):
    """Nearest earlier non-cancelled session of the same offering taught by me."""
    from apps.scheduling.models import ClassSession, SessionStatus

    return (
        ClassSession.objects.filter(
            offering_id=target.offering_id,
            session_teachers__teacher=teacher,
            start_at__lt=target.start_at,
        )
        .exclude(status=SessionStatus.CANCELLED)
        .order_by("-start_at", "-id")
        .distinct()
        .first()
    )


def _save_preview(teacher, session, text: str) -> bool:
    """Write one own draft through the existing API; False when already filled."""
    from rest_framework.test import APIClient

    from apps.teaching.models import TeachingNote

    note = TeachingNote.objects.filter(session=session, author=teacher).first()
    if _has_content(note):
        return False

    client = APIClient()
    client.force_authenticate(user=teacher.user)
    response = client.put(
        f"/api/v1/sessions/{session.id}/notes/mine/",
        {
            "covered": "",
            "unfinished": "",
            "next_plan": text,
            "base_version": note.version if note is not None else 0,
            "client_mutation_id": f"demo-preview-{session.id}",
        },
        format="json",
    )
    if not 200 <= response.status_code < 300:
        body = getattr(response, "content", b"").decode("utf-8", "replace")
        raise SystemExit(f"保存演示备注失败：HTTP {response.status_code} {body[:500]}")
    return True


def main() -> None:
    _configure_django()

    from apps.academics.models import School, TeacherProfile, TeacherStatus

    demo_date = _demo_date()
    school = School.objects.filter(code=SCHOOL_CODE).first()
    if school is None:
        raise SystemExit("未找到演示学校，请先运行 seed_demo.py。")
    teacher = TeacherProfile.objects.filter(school=school, teacher_no=TEACHER_NO).first()
    if teacher is None or teacher.user_id is None:
        raise SystemExit(f"未找到教师 {TEACHER_NO} 或其登录账号，请先运行 seed_demo.py。")
    if teacher.teacher_status != TeacherStatus.ACTIVE:
        raise SystemExit(f"教师 {TEACHER_NO} 当前不是可登录状态，未写入备注。")

    targets = _pick_target_sessions(teacher, demo_date)
    if not targets:
        print(f"{demo_date} 没有教师 {TEACHER_NO} 的本人课程，未写入备注。")
        return

    created = 0
    skipped = 0
    for index, target in enumerate(targets):
        source = _earlier_session(teacher, target)
        if source is None:
            print(f"跳过 {target.offering.offering_code}：找不到更早的同开课课次。")
            continue
        text = NOTE_TEXTS[index % len(NOTE_TEXTS)]
        if _save_preview(teacher, source, text):
            created += 1
            print(f"已写入 {target.offering.offering_code} 的备注预览：{source.start_at:%Y-%m-%d %H:%M} -> {text}")
        else:
            skipped += 1
            print(f"跳过 {target.offering.offering_code}：更早课次已有备注内容。")

    print(f"演示日期 {demo_date}：目标课程 {len(targets)} 门，新增备注 {created} 条，跳过已有 {skipped} 条。")


if __name__ == "__main__":
    main()
