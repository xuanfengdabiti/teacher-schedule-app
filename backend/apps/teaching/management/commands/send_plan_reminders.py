"""Send one daily in-app summary for each offering whose plan is behind."""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.scheduling.selectors import current_school_date
from apps.teaching.reminders import send_plan_reminders


class Command(BaseCommand):
    help = "按教学计划截止与实际分配计算落后情况，并给当前任课教师发每日站内提醒。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--date",
            dest="date",
            help="以学校本地日期 YYYY-MM-DD 判断到期；默认使用服务器当前学校日期。",
        )

    def handle(self, *args, **options) -> None:
        raw_date = options.get("date")
        if raw_date:
            try:
                today = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CommandError("--date 必须是 YYYY-MM-DD。") from exc
        else:
            today = current_school_date()

        stats = send_plan_reminders(today=today, now=timezone.now())
        self.stdout.write(self.style.SUCCESS(stats.as_line()))
