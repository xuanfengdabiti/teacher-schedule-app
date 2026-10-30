"""本地演示数据补充：教学计划、历史完课、少量教师活动样例。

只填充 DEMO-SCHOOL / 2026-autumn 的虚构数据，固定演示日 2026-10-28。
全部通过现有业务服务写入；重复执行新增为 0，不覆盖演示者已编辑的数据。

用法（容器内，直接执行脚本；``manage.py shell`` 的 ``__name__`` 不是
``__main__``，所以本脚本在模块末尾直接调用 ``main()``）::

    python manage.py shell < /tmp/seed_demo_activity.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from apps.academics.models import (
    CourseOffering,
    School,
    TeacherProfile,
    TeachingAssignment,
)
from apps.classrooms.models import Classroom
from apps.inquiries.models import Inquiry, InquiryReplyResult
from apps.inquiries.services import create_inquiry, inquiry_candidates, reply_to_inquiry
from apps.scheduling.models import ClassSession, RescheduleRequest, SessionStatus
from apps.scheduling.requests import RescheduleRequestError, create_request
from apps.scheduling.reschedule_options import (
    ELIGIBILITY_AVAILABLE,
    OptionQuery,
    find_reschedule_options,
)
from apps.teaching.completion import record_completion
from apps.teaching.handoffs import create_handoffs
from apps.teaching.models import (
    Handoff,
    PlanItem,
    SessionProgress,
    TeachingNote,
    TeachingPlan,
)
from apps.teaching.plans import save_plan
from apps.teaching.services import publish, save_draft
from django.db import models
from django.db.models import Sum

SCHOOL_CODE = "DEMO-SCHOOL"
TERM_CODE = "2026-autumn"
DEMO_DATE = date(2026, 10, 28)
TZ = ZoneInfo("Asia/Shanghai")
DEMO_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
CUTOFF = datetime.combine(DEMO_DATE, time.min, tzinfo=TZ)
FUTURE_FROM = datetime.combine(DEMO_DATE + timedelta(days=1), time.min, tzinfo=TZ)
DEMO_TEACHER_NOS = ("0001", "0011", "0012", "0013")
NOTE_MARKER = "【演示活动】"
HANDOFF_MARKER = "【演示活动】交接："
REQUEST_MARKER = "【演示活动】调课申请："
INQUIRY_MARKER = "【演示活动】遗失的蓝色笔记本"
RESERVED_UNALLOCATED = Decimal("1.0")

PLAN_ASPECTS = (
    "基础框架与关键概念",
    "典型案例分析",
    "专题研讨与实务演练",
    "综合复习与考核要点",
)


def fail(message: str) -> None:
    raise RuntimeError(message)


def stats() -> dict[str, int]:
    return {"created": 0, "skipped": 0, "total": 0}


def _teacher_by_no(school, teacher_no: str) -> TeacherProfile | None:
    return TeacherProfile.objects.filter(school=school, teacher_no=teacher_no).first()


def _active_assignment_ids(teacher: TeacherProfile):
    """Active assignments on the fixed demo date (not the wall clock)."""
    return (
        TeachingAssignment.objects.filter(
            teacher=teacher,
            effective_from__lte=DEMO_DATE,
        )
        .filter(models.Q(effective_until__isnull=True) | models.Q(effective_until__gt=DEMO_DATE))
        .values("offering_id")
    )


def _shared_offering(school, first: TeacherProfile, second: TeacherProfile) -> CourseOffering | None:
    return (
        CourseOffering.objects.filter(
            term__school=school,
            term__code=TERM_CODE,
            id__in=_active_assignment_ids(first),
        )
        .filter(id__in=_active_assignment_ids(second))
        .order_by("offering_code")
        .first()
    )


def _cycle_pairs(school):
    """One sender→recipient pair per demo account, preferring a fresh recipient."""
    accounts = [(_teacher_by_no(school, no), no) for no in DEMO_TEACHER_NOS]
    accounts = [(teacher, no) for teacher, no in accounts if teacher is not None]
    if len(accounts) < 2:
        return []
    pairs = []
    used_recipients: set = set()
    for index, (sender, _no) in enumerate(accounts):
        for offset in range(1, len(accounts)):
            recipient, _recipient_no = accounts[(index + offset) % len(accounts)]
            if recipient.id in used_recipients:
                continue
            offering = _shared_offering(school, sender, recipient)
            if offering is None:
                continue
            used_recipients.add(recipient.id)
            pairs.append((sender, recipient, offering))
            break
    return pairs


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def _plan_titles(offering: CourseOffering) -> list[str]:
    name = offering.course.name.replace("（示例）", "").strip() or offering.offering_code
    return [f"{name}：{aspect}" for aspect in PLAN_ASPECTS]


def _split_hours(total: Decimal, parts: int) -> list[Decimal]:
    base = (total / parts).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
    if base <= 0:
        return [total]
    return [base] * (parts - 1) + [total - base * (parts - 1)]


def _stage_dates(offering: CourseOffering) -> list[date]:
    """Four due dates spread across the term."""
    term = offering.term
    span = (term.end_date - term.start_date).days
    return [term.start_date + timedelta(days=round(span * share / 4)) for share in (1, 2, 3, 4)]


def seed_plans(school) -> dict[str, int]:
    counters = stats()
    offerings = CourseOffering.objects.filter(
        term__school=school,
        term__code=TERM_CODE,
    ).select_related("course", "term", "lead_teacher")
    for offering in offerings.order_by("offering_code", "id"):
        counters["total"] += 1
        if TeachingPlan.objects.filter(offering=offering).exists():
            counters["skipped"] += 1
            continue
        titles = _plan_titles(offering)
        hours = _split_hours(offering.target_hours, len(titles))
        due_dates = _stage_dates(offering)
        items = [
            {
                "title": title,
                "planned_hours": hour,
                "due_date": due.isoformat(),
            }
            for title, hour, due in zip(titles, hours, due_dates, strict=True)
        ]
        save_plan(offering.lead_teacher, offering, items=items, base_version=0, now=DEMO_NOW)
        counters["created"] += 1
    return counters


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


def _past_sessions(offering: CourseOffering) -> list[ClassSession]:
    """Every non-cancelled session that has ended before the demo day."""
    return list(
        ClassSession.objects.filter(offering=offering, end_at__lt=CUTOFF)
        .exclude(status=SessionStatus.CANCELLED)
        .order_by("start_at", "id")
        .prefetch_related("session_teachers__teacher")
    )


def _session_teacher(session: ClassSession) -> TeacherProfile:
    link = session.session_teachers.all().order_by("teacher__teacher_no").first()
    if link is None:
        fail(f"课次 {session.id} 没有授课教师。")
    return link.teacher


def _target_count(past_count: int) -> int:
    """Earliest ~75%, always leaving 1-2 recent past sessions unconfirmed."""
    if past_count < 2:
        return 0
    reserved = 2 if past_count >= 6 else 1
    return min(max(1, round(past_count * 0.75)), past_count - reserved)


def _unallocated_hours(offering: CourseOffering) -> Decimal:
    completed = ClassSession.objects.filter(
        offering=offering,
        status=SessionStatus.COMPLETED,
        actual_hours__isnull=False,
    )
    total_actual = completed.aggregate(total=Sum("actual_hours"))["total"] or Decimal("0.0")
    total_allocated = SessionProgress.objects.filter(
        session__offering=offering,
    ).aggregate(total=Sum("actual_hours"))["total"] or Decimal("0.0")
    return Decimal(total_actual) - Decimal(total_allocated)


def _capacity_by_item(items: list[PlanItem]) -> dict:
    used = {
        row["plan_item_id"]: row["total"]
        for row in SessionProgress.objects.filter(plan_item__in=items)
        .values("plan_item_id")
        .annotate(total=Sum("actual_hours"))
    }
    return {item.id: max(Decimal("0.0"), Decimal(item.planned_hours) - Decimal(used.get(item.id, 0))) for item in items}


def _allocate(actual: Decimal, items: list[PlanItem], capacity: dict) -> list[dict]:
    """Fill plan items in order; the remaining tail stays unallocated."""
    remaining = actual
    allocations: list[dict] = []
    for item in items:
        if remaining <= 0:
            break
        available = capacity[item.id]
        if available <= 0:
            continue
        take = min(available, remaining)
        allocations.append({"plan_item_id": str(item.id), "actual_hours": str(take)})
        capacity[item.id] = available - take
        remaining -= take
    return allocations


def seed_completions(school) -> dict[str, int]:
    counters = stats()
    offerings = CourseOffering.objects.filter(term__school=school, term__code=TERM_CODE)
    for offering in offerings.order_by("offering_code", "id"):
        past = _past_sessions(offering)
        target_count = _target_count(len(past))
        if target_count == 0:
            continue
        items = list(PlanItem.objects.filter(plan__offering=offering, archived=False).order_by("position", "id"))
        capacity = _capacity_by_item(items)
        # At most one session per offering keeps 1.0h outside a topic, and only
        # when the offering has no unallocated hours yet.
        reserve_tail = _unallocated_hours(offering) <= 0
        for session in past[:target_count]:
            counters["total"] += 1
            if session.status == SessionStatus.COMPLETED or session.actual_hours is not None:
                counters["skipped"] += 1
                continue
            actual = Decimal(session.planned_hours)
            allocatable = actual
            if reserve_tail and actual > RESERVED_UNALLOCATED:
                allocatable = actual - RESERVED_UNALLOCATED
                reserve_tail = False
            allocations = _allocate(allocatable, items, capacity)
            record_completion(
                _session_teacher(session),
                session,
                action="complete",
                actual_hours=actual,
                allocations=allocations,
                reason="",
                base_version=session.version,
                now=DEMO_NOW,
            )
            counters["created"] += 1
    return counters


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


def _ensure_note(teacher: TeacherProfile, *, want_shared: bool) -> str:
    """Add one short note on a completed session the teacher owns, if missing."""
    existing = TeachingNote.objects.filter(author=teacher)
    if existing.count() >= 2:
        return "skipped"
    if existing.filter(draft_covered__startswith=NOTE_MARKER).exists():
        # This script already added its one note for this author.
        return "skipped"
    if want_shared and existing.filter(published_at__isnull=False).exists():
        return "skipped"
    sessions = (
        ClassSession.objects.filter(
            session_teachers__teacher=teacher,
            status=SessionStatus.COMPLETED,
            end_at__lt=CUTOFF,
        )
        .order_by("-start_at", "-id")
        .distinct()
    )
    for session in sessions[:6]:
        note = TeachingNote.objects.filter(session=session, author=teacher).first()
        if note is not None:
            continue
        draft = save_draft(
            teacher,
            session,
            covered=f"{NOTE_MARKER}用虚构案例梳理了本节重点，并请同组补充记录。",
            unfinished="",
            next_plan="下次用 15 分钟复盘，再进入新的案例。",
            base_version=0,
            client_mutation_id=f"demo-activity-note-{session.id}-{teacher.teacher_no}",
            now=DEMO_NOW,
        )
        if want_shared:
            publish(
                teacher,
                session,
                base_version=draft["version"],
                client_mutation_id=f"demo-activity-publish-{session.id}-{teacher.teacher_no}",
                now=DEMO_NOW,
            )
        return "created"
    return "skipped"


def seed_notes(school) -> dict[str, int]:
    counters = stats()
    shared_flags = {"0001": True, "0011": True, "0012": True, "0013": False}
    for teacher_no in DEMO_TEACHER_NOS:
        teacher = _teacher_by_no(school, teacher_no)
        if teacher is None:
            continue
        counters["total"] += 1
        result = _ensure_note(teacher, want_shared=shared_flags[teacher_no])
        counters["created" if result == "created" else "skipped"] += 1
    return counters


# ---------------------------------------------------------------------------
# handoffs
# ---------------------------------------------------------------------------


def _ensure_handoff(sender: TeacherProfile, recipient: TeacherProfile, offering) -> str:
    if Handoff.objects.filter(sender=sender, recipient=recipient).exists():
        return "skipped"
    source = (
        ClassSession.objects.filter(
            offering=offering,
            session_teachers__teacher=sender,
            status=SessionStatus.COMPLETED,
            end_at__lt=CUTOFF,
        )
        .order_by("-start_at")
        .first()
    )
    target = (
        ClassSession.objects.filter(
            offering=offering,
            session_teachers__teacher=recipient,
            status=SessionStatus.SCHEDULED,
            start_at__gte=FUTURE_FROM,
        )
        .order_by("start_at")
        .first()
    )
    create_handoffs(
        sender,
        offering,
        [recipient],
        HANDOFF_MARKER + "下次课前请核对虚构案例记录，重点看未完成的问题二。",
        source,
        target,
        None,
        DEMO_NOW,
    )
    return "created"


def seed_handoffs(school) -> dict[str, int]:
    counters = stats()
    for sender, recipient, offering in _cycle_pairs(school):
        counters["total"] += 1
        result = _ensure_handoff(sender, recipient, offering)
        counters["created" if result == "created" else "skipped"] += 1
    return counters


# ---------------------------------------------------------------------------
# reschedule requests
# ---------------------------------------------------------------------------


def _ensure_request(teacher: TeacherProfile) -> str:
    if RescheduleRequest.objects.filter(
        applicant=teacher,
        status__in=["submitted", "approved"],
    ).exists():
        return "skipped"
    sessions = ClassSession.objects.filter(
        session_teachers__teacher=teacher,
        status=SessionStatus.SCHEDULED,
        start_at__gte=FUTURE_FROM,
    ).order_by("start_at")[:5]
    for session in sessions:
        term = session.offering.term
        date_from = session.start_at.date()
        date_until = min(term.end_date + timedelta(days=1), date_from + timedelta(days=14))
        options = find_reschedule_options(
            session,
            OptionQuery(date_from=date_from, date_until=date_until),
            now=DEMO_NOW,
        )
        for option in options.options:
            if option.eligibility != ELIGIBILITY_AVAILABLE:
                continue
            classroom = Classroom.objects.filter(pk=option.classroom_id).first()
            if classroom is None:
                continue
            try:
                create_request(
                    teacher,
                    session,
                    target_date=option.start_at.date(),
                    target_start_period=option.start_period,
                    target_end_period=option.end_period,
                    target_classroom=classroom,
                    reason=REQUEST_MARKER + "与虚构的教研活动时间冲突，申请调整到本周内。",
                    submit=True,
                    now=DEMO_NOW,
                )
            except RescheduleRequestError:
                continue
            return "created"
    return "skipped"


def seed_requests(school) -> dict[str, int]:
    counters = stats()
    for teacher_no in DEMO_TEACHER_NOS:
        teacher = _teacher_by_no(school, teacher_no)
        if teacher is None:
            continue
        counters["total"] += 1
        result = _ensure_request(teacher)
        counters["created" if result == "created" else "skipped"] += 1
    return counters


# ---------------------------------------------------------------------------
# lost-item inquiries
# ---------------------------------------------------------------------------


def _ensure_inquiry(sender: TeacherProfile, recipient: TeacherProfile, *, reply: bool) -> str:
    if Inquiry.objects.filter(
        initiator=sender,
        item_description__startswith=INQUIRY_MARKER,
    ).exists():
        return "skipped"
    # Ask about the recipient's own past class, so they are a real candidate.
    session = (
        ClassSession.objects.filter(
            session_teachers__teacher=recipient,
            end_at__lt=CUTOFF,
        )
        .order_by("-start_at")
        .first()
    )
    if session is None:
        return "skipped"
    candidates = inquiry_candidates(sender, session.classroom, session.start_at, session.end_at)
    if recipient.id not in {entry.teacher.id for entry in candidates.candidates}:
        return "skipped"
    inquiry = create_inquiry(
        sender,
        session.classroom,
        from_at=session.start_at,
        to_at=session.end_at,
        item_description=INQUIRY_MARKER + "（虚构物品，仅用于演示）。",
        recipient_ids=[recipient.id],
    )
    if reply:
        reply_to_inquiry(
            recipient,
            inquiry,
            result=InquiryReplyResult.IN_CUSTODY,
            body="看到一本蓝色笔记本，已放在值班室，可来取。",
            now=DEMO_NOW,
        )
    return "created"


def seed_inquiries(school) -> dict[str, int]:
    counters = stats()
    for index, (sender, recipient, _offering) in enumerate(_cycle_pairs(school)):
        counters["total"] += 1
        # Leave the first one open for the recipient to answer, reply to others.
        result = _ensure_inquiry(sender, recipient, reply=index % 2 == 1)
        counters["created" if result == "created" else "skipped"] += 1
    return counters


def main() -> None:
    school = School.objects.filter(code=SCHOOL_CODE).first()
    if school is None:
        fail(f"未找到演示学校 {SCHOOL_CODE}，请先运行 seed_demo.py。")

    results = {
        "plans": seed_plans(school),
        "completions": seed_completions(school),
        "notes": seed_notes(school),
        "handoffs": seed_handoffs(school),
        "requests": seed_requests(school),
        "inquiries": seed_inquiries(school),
    }
    for name, counters in results.items():
        print(f"{name}: 新增 {counters['created']}，跳过 {counters['skipped']}，检查 {counters['total']}")
    print("演示活动数据补充完成。")


try:
    main()
except Exception as exc:
    print(f"SEED_ACTIVITY_FAILED: {exc}", file=sys.stderr)
    raise
