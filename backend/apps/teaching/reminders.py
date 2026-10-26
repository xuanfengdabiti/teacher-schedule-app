"""Daily plan-behind reminder service backing the management command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone as django_timezone

from apps.academics.models import CourseOffering
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.teaching.progress import offering_progress
from apps.teaching.selectors import active_offering_teachers


@dataclass
class ReminderStats:
    processed: int = 0
    behind: int = 0
    created: int = 0
    skipped_existing: int = 0
    no_recipients: int = 0

    def as_line(self) -> str:
        return (
            f"处理课程开设 {self.processed} 个；落后 {self.behind} 个；"
            f"创建站内消息 {self.created} 条；同日已存在而跳过 {self.skipped_existing} 条；"
            f"无当前任课教师 {self.no_recipients} 个。"
        )


def _body(offering: CourseOffering, progress: dict, *, today: date) -> str:
    due_rows = [
        row for row in progress["items"] if not row["archived"] and row["is_due"] and Decimal(row["shortfall"]) > 0
    ]
    due_rows.sort(key=lambda row: (row["due"] or "", row["plan_item_id"]))
    course_name = offering.course.name
    if due_rows:
        first = due_rows[0]
        if len(due_rows) == 1:
            return (
                f"《{course_name}》教学计划落后：原计划 {first['due']} 前完成的专题还差 "
                f"{first['shortfall']} 学时。请到课程计划查看。"
            )
        return (
            f"《{course_name}》教学计划落后：原计划 {first['due']} 前完成的专题还差 "
            f"{first['shortfall']} 学时；截至 {today.isoformat()}，已到截止日期的专题合计还差 "
            f"{progress['behind_hours']} 学时。请到课程计划查看。"
        )
    return (
        f"《{course_name}》教学计划已到落后阈值，截至 {today.isoformat()} 合计还差 "
        f"{progress['behind_hours']} 学时。请到课程计划查看。"
    )


def send_plan_reminders(*, today: date, now: datetime) -> ReminderStats:
    """Create one ``plan_behind`` message per current offering teacher per day."""
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")

    stats = ReminderStats()
    offering_ids = list(CourseOffering.objects.filter(teaching_plan__isnull=False).values_list("id", flat=True))
    for offering_id in offering_ids:
        with transaction.atomic():
            offering = (
                CourseOffering.objects.select_for_update(of=("self",))
                .select_related("term", "course", "teaching_class")
                .get(pk=offering_id)
            )
            progress = offering_progress(offering, today=today, now=now)
            stats.processed += 1
            if progress["plan_status"] != "behind":
                continue
            stats.behind += 1
            recipients = active_offering_teachers(offering, on_date=today)
            if not recipients:
                stats.no_recipients += 1
                continue
            body = _body(offering, progress, today=today)
            event_key = f"plan_behind:{offering.id}:{today.isoformat()}"
            for teacher in recipients:
                notification = notify(
                    teacher,
                    type=NotificationType.PLAN_BEHIND,
                    object_type="offering",
                    object_id=offering.id,
                    title="教学计划落后",
                    body=body,
                    event_key=event_key,
                )
                if notification is None:
                    stats.skipped_existing += 1
                else:
                    stats.created += 1
    return stats
