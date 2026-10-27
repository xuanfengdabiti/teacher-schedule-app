"""Mapping from business notification types to the account's push switches.

B40's dispatcher must call :func:`allows_remote_push` at send time, not only
when the message is enqueued: a teacher may turn remote push off after the
in-app message and pending DeliveryAttempt rows already exist.  In-app
notifications are never deleted or suppressed by these switches.
"""

from __future__ import annotations

from apps.accounts.preferences import preferences_for
from apps.notifications.models import NotificationType

# Which overall switch plus category switch each type honours.  Types not
# listed are in-app only (no remote push) and must not be sent.
PUSH_CATEGORY_FIELDS: dict[str, str] = {
    NotificationType.SCHEDULE_CHANGED: "push_schedule_changed",
    NotificationType.RESCHEDULE_RESULT: "push_schedule_changed",
    NotificationType.HANDOFF_RECEIVED: "push_handoff",
    NotificationType.HANDOFF_ACKNOWLEDGED: "push_handoff",
    NotificationType.HANDOFF_CHECK: "push_handoff",
    NotificationType.INQUIRY_RECEIVED: "push_inquiry",
    NotificationType.INQUIRY_REPLIED: "push_inquiry",
    NotificationType.PLAN_BEHIND: "push_plan",
}


def push_category_field(notification_type: str) -> str | None:
    """Return the preference column for a notification type, or ``None``."""
    return PUSH_CATEGORY_FIELDS.get(notification_type)


def allows_remote_push(user, notification_type: str) -> bool:
    """Return whether this account currently wants a remote push for ``type``.

    Both the global switch and the category switch must be on.  Unknown types
    return ``False`` so a newly added message type cannot silently bypass the
    teacher's settings.
    """
    field = push_category_field(notification_type)
    if field is None:
        return False
    preferences = preferences_for(user)
    return bool(preferences.push_enabled and getattr(preferences, field))
