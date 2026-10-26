"""Single server-side algorithm for offering teaching progress.

The mobile client never recomputes these totals.  Session and topic aggregates
are each one SQL statement, so the query count is constant regardless of how
many class sessions an offering contains.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.academics.models import CourseOffering
from apps.scheduling.models import ClassSession, SessionStatus
from apps.teaching.hours import hours_string, threshold_hours
from apps.teaching.models import TeachingPlan

ZERO_HOURS = Decimal("0.0")
HOURS_FIELD = DecimalField(max_digits=12, decimal_places=1)


def _threshold() -> Decimal:
    raw = getattr(settings, "PLAN_BEHIND_THRESHOLD_HOURS", "2.0")
    try:
        return threshold_hours(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from None


def _local_date(value, tzinfo) -> date:
    return value.astimezone(tzinfo).date()


def offering_progress(
    offering: CourseOffering,
    *,
    today: date,
    now: datetime,
) -> dict[str, Any]:
    """Return target/completed/remaining totals and per-topic due progress."""
    session_totals = ClassSession.objects.filter(offering=offering).aggregate(
        completed=Coalesce(
            Sum("actual_hours", filter=Q(status=SessionStatus.COMPLETED)),
            Value(ZERO_HOURS),
            output_field=HOURS_FIELD,
        ),
        remaining=Count("id", filter=Q(status=SessionStatus.SCHEDULED)),
        past_pending=Count(
            "id",
            filter=Q(status=SessionStatus.SCHEDULED, end_at__lt=now),
        ),
        future=Count(
            "id",
            filter=Q(status=SessionStatus.SCHEDULED, end_at__gte=now),
        ),
    )
    completed_hours = session_totals["completed"] or ZERO_HOURS
    target_hours = offering.target_hours
    remaining_hours = max(target_hours - completed_hours, ZERO_HOURS)
    over_hours = max(completed_hours - target_hours, ZERO_HOURS)

    plan = TeachingPlan.objects.filter(offering=offering).first()
    item_payloads: list[dict[str, Any]] = []
    allocated_total = ZERO_HOURS
    behind_hours = ZERO_HOURS

    if plan is not None:
        items = (
            plan.items.select_related("due_session")
            .annotate(
                allocated_hours=Coalesce(
                    Sum("session_progresses__actual_hours"),
                    Value(ZERO_HOURS),
                    output_field=HOURS_FIELD,
                )
            )
            .order_by("position", "id")
        )
        for item in items:
            actual_hours = item.allocated_hours or ZERO_HOURS
            allocated_total += actual_hours
            due = item.due_date
            if due is None and item.due_session_id is not None:
                due = _local_date(item.due_session.start_at, offering.term.tzinfo)
            is_due = due is not None and due <= today
            shortfall = max(item.planned_hours - actual_hours, ZERO_HOURS) if is_due else ZERO_HOURS
            if is_due and not item.archived:
                behind_hours += shortfall
            item_payloads.append(
                {
                    "plan_item_id": str(item.id),
                    "title": item.title,
                    "planned_hours": hours_string(item.planned_hours),
                    "actual_hours": hours_string(actual_hours),
                    "due": due.isoformat() if due is not None else None,
                    "is_due": is_due,
                    "shortfall": hours_string(shortfall),
                    "archived": item.archived,
                }
            )

    threshold = _threshold()
    if plan is None:
        plan_status = "not_set"
        behind_hours = ZERO_HOURS
    else:
        # A zero threshold must not turn a fully caught-up plan into a daily
        # reminder.  "Behind" always requires a positive shortfall.
        plan_status = "behind" if behind_hours > 0 and behind_hours >= threshold else "on_track"

    return {
        "target_hours": hours_string(target_hours),
        "completed_hours": hours_string(completed_hours),
        "remaining_hours": hours_string(remaining_hours),
        "over_hours": hours_string(over_hours),
        "remaining_sessions": session_totals["remaining"],
        "past_pending_sessions": session_totals["past_pending"],
        "future_sessions": session_totals["future"],
        "unallocated_hours": hours_string(completed_hours - allocated_total),
        "plan_status": plan_status,
        "behind_hours": hours_string(behind_hours),
        "behind_threshold_hours": hours_string(threshold),
        "items": item_payloads,
    }
