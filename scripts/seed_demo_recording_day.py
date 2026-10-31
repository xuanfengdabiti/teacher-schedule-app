"""把 2026-10-31（周六）临时排成“有课的一天”，供当天录屏演示。

从 ``--source-date``（默认 2026-10-27 周二）当天四人的全部真实课次出发（预计
9 节：甲 3、李 4、张 1、王 1），按来源的开课、教室、节次、计划学时与实际
授课教师，建立只覆盖目标教学周/星期的 ``ScheduleRule``，再调用
``generate_sessions_for_term`` 生成目标日新课次。原课次的主键、状态、学时和
备注不动，也不建校历例外。

随后只补演示内容：教师甲今日每门课在更早同开课课次上的“下次计划”预览、
今日第一门课的三段共享备注、缺失的教学计划、0 学时课程的一条历史完课，
以及今日第一门已结束课次的 2.0 学时完成记录。

规则用稳定 ``rule_code``，备注/完成按内容与状态判断；重复执行新增为 0，
不覆盖用户已编辑的计划、备注或学时。

容器内运行（脚本自动配置 Django）::

    docker exec teacher-schedule-demo-api-1 \
      python /tmp/seed_demo_recording_day.py --date 2026-10-31
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DATE = date(2026, 10, 31)
DEFAULT_SOURCE_DATE = date(2026, 10, 27)
SCHOOL_CODE = "DEMO-SCHOOL"
TERM_CODE = "2026-autumn"
TEACHER_NOS = ("0001", "0011", "0012", "0013")
MAIN_TEACHER_NO = "0001"
PLAN_ASPECTS = ("基础框架与关键概念", "典型案例分析", "专题研讨与实务演练", "综合复习与考核要点")
NOTE_TEMPLATES = {
    "刑事诉讼法": ("完成诉讼程序梳理和案例讨论。", "证据审查案例留到下次。", "下节继续证据链分析。"),
    "证据法": ("完成证据规则梳理和案例讨论。", "鉴定意见案例留到下次。", "下节继续证据链分析。"),
    "犯罪心理": ("完成犯罪动机分析与案例讨论。", "群体心理案例留到下次。", "下节继续案例分析。"),
}
FALLBACK_NOTE = ("完成本节内容梳理和案例讨论。", "课堂练习留到下次。", "下节继续案例分析。")


def fail(message: str) -> None:
    raise SystemExit(message)


def _configure_django() -> None:
    try:
        from django.conf import settings

        if settings.configured:
            return
    except Exception:  # noqa: BLE001 - 下面的 import 会给出真实错误
        pass
    for candidate in (Path(__file__).resolve().parent.parent / "backend", Path.cwd(), Path("/app")):
        if (candidate / "manage.py").exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            break
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成录制日临时课次与演示内容")
    parser.add_argument("--date", default=DEFAULT_DATE.isoformat(), help="目标日期 YYYY-MM-DD")
    parser.add_argument("--source-date", default=DEFAULT_SOURCE_DATE.isoformat(), help="来源日期 YYYY-MM-DD")
    args = parser.parse_args()
    try:
        args.date = date.fromisoformat(args.date)
        args.source_date = date.fromisoformat(args.source_date)
    except ValueError as exc:
        fail(f"日期格式应为 YYYY-MM-DD：{exc}")
    return args


def _day_bounds(day: date, tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tzinfo)
    return start, start + timedelta(days=1)


def _overlap(a1: int, a2: int, b1: int, b2: int) -> bool:
    return not (a2 < b1 or b2 < a1)


def _teachers(session) -> list:
    return [link.teacher for link in session.session_teachers.all()]


def _note_texts(course_name: str) -> tuple[str, str, str]:
    return next((texts for key, texts in NOTE_TEMPLATES.items() if key in course_name), FALLBACK_NOTE)


def _now():
    from django.utils import timezone

    return timezone.now()


def _plan_items(offering):
    from apps.teaching.models import PlanItem

    return list(PlanItem.objects.filter(plan__offering=offering, archived=False).order_by("position", "id"))


def _actual_hours(offering) -> Decimal:
    from apps.scheduling.models import ClassSession, SessionStatus
    from django.db.models import Sum

    total = ClassSession.objects.filter(
        offering=offering, status=SessionStatus.COMPLETED, actual_hours__isnull=False
    ).aggregate(total=Sum("actual_hours"))["total"]
    return Decimal(total or 0)


def _allocate(actual: Decimal, items) -> list[dict]:
    """Fill plan items in order; hours beyond the plan stay unallocated."""
    from apps.teaching.models import SessionProgress
    from django.db.models import Sum

    used = {
        row["plan_item_id"]: row["total"]
        for row in SessionProgress.objects.filter(plan_item__in=items)
        .values("plan_item_id")
        .annotate(total=Sum("actual_hours"))
    }
    remaining, allocations = actual, []
    for item in items:
        capacity = Decimal(item.planned_hours) - Decimal(used.get(item.id, 0))
        if remaining <= 0:
            break
        if capacity <= 0:
            continue
        take = min(capacity, remaining)
        allocations.append({"plan_item_id": str(item.id), "actual_hours": str(take)})
        remaining -= take
    return allocations


def _sessions_on(term, day_start: datetime, day_end: datetime, teachers):
    """All non-cancelled source-day sessions actually taught by the four accounts."""
    from apps.scheduling.models import ClassSession, SessionStatus

    return list(
        ClassSession.objects.filter(
            offering__term=term,
            session_teachers__teacher__in=teachers,
            start_at__gte=day_start,
            start_at__lt=day_end,
        )
        .exclude(status=SessionStatus.CANCELLED)
        .select_related("offering__course", "offering__term", "classroom")
        .prefetch_related("session_teachers__teacher")
        .order_by("start_at", "start_period", "id")
        .distinct()
    )


def _script_session_ids(term, start: datetime, end: datetime, prefix: str):
    """This script's own target-day sessions, identified by the REC rule code."""
    from apps.scheduling.models import ClassSession

    return ClassSession.objects.filter(
        offering__term=term,
        start_at__gte=start,
        start_at__lt=end,
        rule__rule_code__startswith=prefix,
    ).values("id")


def _target_busy(term, start: datetime, end: datetime, script_ids):
    """Target-day occupancy, ignoring this script's own REC sessions.

    Excluding the script's own sessions keeps the same source recipe selectable
    on a second run; the stable rule code then makes it a no-op. Any other
    session on the target day (imported or user-arranged) still counts.
    """
    from apps.scheduling.models import ClassSession, SessionStatus

    teacher_busy: dict = {}
    room_busy: dict = {}
    sessions = (
        ClassSession.objects.filter(offering__term=term, start_at__gte=start, start_at__lt=end)
        .exclude(status=SessionStatus.CANCELLED)
        .exclude(pk__in=script_ids)
        .prefetch_related("session_teachers__teacher")
    )
    for session in sessions:
        for teacher in _teachers(session):
            teacher_busy.setdefault(teacher.id, []).append((session.start_period, session.end_period))
        room_busy.setdefault(session.classroom_id, []).append((session.start_period, session.end_period))
    return teacher_busy, room_busy


def _reserve_if_free(session, teacher_busy, room_busy) -> bool:
    """False when the target slot collides; otherwise reserve it for later picks."""
    for teacher in _teachers(session):
        if any(_overlap(session.start_period, session.end_period, a, b) for a, b in teacher_busy.get(teacher.id, ())):
            return False
    if any(
        _overlap(session.start_period, session.end_period, a, b) for a, b in room_busy.get(session.classroom_id, ())
    ):
        return False
    for teacher in _teachers(session):
        teacher_busy.setdefault(teacher.id, []).append((session.start_period, session.end_period))
    room_busy.setdefault(session.classroom_id, []).append((session.start_period, session.end_period))
    return True


def _ensure_rule(term, source, target: date, week: int):
    from apps.scheduling.models import ScheduleRule

    code = f"REC-{target:%Y%m%d}-{source.offering.offering_code}-{source.start_period}"
    rule = ScheduleRule.objects.filter(offering__term=term, rule_code=code).first()
    if rule is not None:
        return rule, False
    rule = ScheduleRule.objects.create(
        offering=source.offering,
        rule_code=code,
        weekday=target.isoweekday(),
        start_period=source.start_period,
        end_period=source.end_period,
        weeks=[week],
        classroom=source.classroom,
        planned_hours=source.planned_hours,
        is_active=True,
    )
    rule.teachers.set(_teachers(source))
    return rule, True


def _ensure_plan(offering) -> bool:
    from apps.teaching.models import TeachingPlan
    from apps.teaching.plans import save_plan

    if TeachingPlan.objects.filter(offering=offering).exists():
        return False
    name = offering.course.name.replace("（示例）", "").strip() or offering.offering_code
    titles = [f"{name}：{aspect}" for aspect in PLAN_ASPECTS]
    total = Decimal(offering.target_hours)
    base = (total / len(titles)).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
    if base > 0:
        hours = [base] * (len(titles) - 1) + [total - base * (len(titles) - 1)]
    else:  # 极小目标学时：只建一个专题，避免出现 0 学时专题。
        titles, hours = titles[:1], [total]
    term = offering.term
    span = (term.end_date - term.start_date).days
    dues = [term.start_date + timedelta(days=round(span * share / 4)) for share in (1, 2, 3, 4)]
    items = [
        {"title": title, "planned_hours": hour, "due_date": due.isoformat()}
        for title, hour, due in zip(titles, hours, dues[: len(titles)], strict=True)
    ]
    save_plan(offering.lead_teacher, offering, items=items, base_version=0, now=_now())
    return True


def _complete(session, teacher, actual: Decimal, now) -> bool:
    from apps.scheduling.models import SessionStatus
    from apps.teaching.completion import record_completion

    if session.status == SessionStatus.COMPLETED or session.actual_hours is not None:
        return False
    record_completion(
        teacher,
        session,
        action="complete",
        actual_hours=actual,
        allocations=_allocate(actual, _plan_items(session.offering)),
        reason="",
        base_version=session.version,
        now=now,
    )
    return True


def _ensure_progress(offering, now, before) -> bool:
    """Only when the course still shows 0 hours: complete one ended past session."""
    from apps.scheduling.models import ClassSession, SessionStatus

    if _actual_hours(offering) > 0:
        return False
    session = (
        ClassSession.objects.filter(
            offering=offering,
            status=SessionStatus.SCHEDULED,
            actual_hours__isnull=True,
            end_at__lt=before,
        )
        .select_related("offering__term")
        .prefetch_related("session_teachers__teacher")
        .order_by("start_at", "id")
        .first()
    )
    teachers = _teachers(session) if session is not None else []
    return bool(teachers) and _complete(session, teachers[0], Decimal(session.planned_hours), now)


def _earlier_session(teacher, session):
    from apps.scheduling.models import ClassSession, SessionStatus

    return (
        ClassSession.objects.filter(
            offering_id=session.offering_id,
            session_teachers__teacher=teacher,
            start_at__lt=session.start_at,
        )
        .exclude(status=SessionStatus.CANCELLED)
        .order_by("-start_at", "-id")
        .distinct()
        .first()
    )


def _write_note(teacher, session, *, covered, unfinished, next_plan, tag, share=False) -> bool:
    from apps.teaching.models import TeachingNote
    from apps.teaching.services import publish, save_draft

    note = TeachingNote.objects.filter(session=session, author=teacher).first()
    fields = (
        "draft_covered",
        "draft_unfinished",
        "draft_next_plan",
        "published_covered",
        "published_unfinished",
        "published_next_plan",
    )
    if note is not None and any((getattr(note, field) or "").strip() for field in fields):
        return False
    result = save_draft(
        teacher,
        session,
        covered=covered,
        unfinished=unfinished,
        next_plan=next_plan,
        base_version=note.version if note is not None else 0,
        client_mutation_id=f"demo-recording-{tag}-{session.id}",
        now=_now(),
    )
    if share:
        publish(
            teacher,
            session,
            base_version=result["version"],
            client_mutation_id=f"demo-recording-publish-{session.id}",
            now=_now(),
        )
    return True


def main() -> None:
    args = _parse_args()
    _configure_django()

    from apps.academics.models import School, TeacherProfile, Term
    from apps.scheduling.calendar import teaching_week
    from apps.scheduling.models import ClassSession, ScheduleRule, SessionStatus
    from apps.scheduling.services import generate_sessions_for_term

    target, source_date = args.date, args.source_date
    school = School.objects.filter(code=SCHOOL_CODE).first()
    if school is None:
        fail(f"未找到演示学校 {SCHOOL_CODE}，请先运行 seed_demo.py。")
    term = Term.objects.filter(school=school, code=TERM_CODE).first()
    if term is None:
        fail(f"未找到学期 {TERM_CODE}。")
    week = teaching_week(term, target)
    if week is None:
        fail(f"目标日期 {target} 不在学期 {TERM_CODE} 的教学周内。")

    tz = ZoneInfo(term.timezone)
    target_start, target_end = _day_bounds(target, tz)
    source_start, source_end = _day_bounds(source_date, tz)
    teachers = {no: TeacherProfile.objects.filter(school=school, teacher_no=no).first() for no in TEACHER_NOS}
    missing = [no for no, teacher in teachers.items() if teacher is None]
    if missing:
        fail(f"未找到教师工号：{'、'.join(missing)}，请先运行演示种子。")
    main_teacher = teachers[MAIN_TEACHER_NO]

    prefix = f"REC-{target:%Y%m%d}-"
    script_ids = _script_session_ids(term, target_start, target_end, prefix)
    teacher_busy, room_busy = _target_busy(term, target_start, target_end, script_ids)
    existing_rules = list(
        ScheduleRule.objects.filter(offering__term=term, rule_code__startswith=prefix).select_related(
            "offering__course", "offering__term", "classroom"
        )
    )
    # Only non-script target-day sessions block an offering; REC sessions are
    # this script's own output and must stay selectable on a rerun.
    used_offerings = set(
        ClassSession.objects.filter(offering__term=term, start_at__gte=target_start, start_at__lt=target_end)
        .exclude(status=SessionStatus.CANCELLED)
        .exclude(pk__in=script_ids)
        .values_list("offering_id", flat=True)
    )
    chosen: list = []

    # 稳定选来源日当天四人的全部实际课次；同一天同一份 recipe 每次都能重选。
    for session in _sessions_on(term, source_start, source_end, [teachers[no] for no in TEACHER_NOS]):
        if session.offering_id in used_offerings:
            continue
        if _reserve_if_free(session, teacher_busy, room_busy):
            chosen.append(session)
            used_offerings.add(session.offering_id)

    rules = list(existing_rules)
    created_rules = 0
    for session in chosen:
        rule, created = _ensure_rule(term, session, target, week)
        if created:
            rules.append(rule)
        created_rules += int(created)

    missing_sessions = [
        rule
        for rule in rules
        if not ClassSession.objects.filter(
            rule=rule, origin_teaching_date=target, origin_period=rule.start_period
        ).exists()
    ]
    generation = generate_sessions_for_term(term) if created_rules or missing_sessions else None

    today_sessions = list(
        ClassSession.objects.filter(
            offering__term=term,
            session_teachers__teacher=main_teacher,
            start_at__gte=target_start,
            start_at__lt=target_end,
        )
        .exclude(status=SessionStatus.CANCELLED)
        .select_related("offering__course", "offering__term")
        .order_by("start_at", "start_period", "id")
        .distinct()
    )

    now = _now()
    demo_offerings = {rule.offering_id: rule.offering for rule in rules}
    demo_offerings.update({session.offering_id: session.offering for session in chosen})
    plans_created = sum(_ensure_plan(offering) for offering in demo_offerings.values())
    progress_created = sum(_ensure_progress(offering, now, target_start) for offering in demo_offerings.values())

    notes_created = notes_skipped = 0
    for index, session in enumerate(today_sessions):
        covered, unfinished, next_plan = _note_texts(session.offering.course.name)
        earlier = _earlier_session(main_teacher, session)
        if earlier is not None and _write_note(
            main_teacher,
            earlier,
            covered="",
            unfinished="",
            next_plan=next_plan,
            tag=f"preview-{index}",
        ):
            notes_created += 1
        else:
            notes_skipped += 1
        if index == 0 and _write_note(
            main_teacher,
            session,
            covered=covered,
            unfinished=unfinished,
            next_plan=next_plan,
            tag="today",
            share=True,
        ):
            notes_created += 1

    first = today_sessions[0] if today_sessions else None
    completed = bool(first is not None and first.end_at <= now and _complete(first, main_teacher, Decimal("2.0"), now))

    print(f"目标日期 {target}（第 {week} 教学周，星期 {target.isoweekday()}），来源 {source_date}")
    print(f"规则：新增 {created_rules} 条，已存在 {len(existing_rules)} 条")
    for rule in rules:
        if not ClassSession.objects.filter(
            rule=rule, origin_teaching_date=target, origin_period=rule.start_period
        ).exists():
            print(f"警告：规则 {rule.rule_code} 未生成目标课次，请检查节次与周次。")
    if generation is not None:
        print(
            f"课次生成：新增 {generation.created} 个，原有未变 {generation.unchanged} 个，"
            f"保留差异 {generation.mismatched_count} 个"
        )
    else:
        print("课次生成：目标课次已存在，未重复生成")
    print(f"教学计划：新增 {plans_created} 门，跳过已有 {len(demo_offerings) - plans_created} 门")
    print(f"历史学时：新增 {progress_created} 门，无需补 {len(demo_offerings) - progress_created} 门")
    print(f"备注：新增 {notes_created} 条，跳过已有 {notes_skipped} 条")
    print(f"今日第一门完成：{'新增 2.0 学时' if completed else '已存在或尚未结束，未新增'}")

    for teacher_no in TEACHER_NOS:
        count = (
            ClassSession.objects.filter(
                offering__term=term,
                session_teachers__teacher=teachers[teacher_no],
                start_at__gte=target_start,
                start_at__lt=target_end,
            )
            .exclude(status=SessionStatus.CANCELLED)
            .distinct()
            .count()
        )
        print(f"今天 {teacher_no} 的课次：{count} 节")
    for session in today_sessions:
        planned = sum((Decimal(item.planned_hours) for item in _plan_items(session.offering)), Decimal("0"))
        print(
            f"  教师甲 {session.start_at.astimezone(tz):%H:%M} {session.offering.course.name}"
            f"（{session.offering.offering_code}）计划 {planned} 学时 / 已完成 {_actual_hours(session.offering)} 学时"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 让执行者看到真实失败
        print(f"SEED_RECORDING_DAY_FAILED: {exc}", file=sys.stderr)
        raise
