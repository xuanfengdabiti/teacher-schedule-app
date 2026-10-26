"""Notification creation service.

Business event owners (B21/B23/B31/B33) call :func:`notify` inside their own
transaction.  This keeps the in-app message and the initial pending delivery
attempts committed together with the business change.  The service never marks
an attempt sent: that requires a real provider adapter and the B40 delivery
command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.academics.models import TeacherProfile
from apps.notifications.models import (
    DeliveryAttempt,
    DeliveryStatus,
    DeviceRegistration,
    Notification,
    NotificationType,
)

if TYPE_CHECKING:
    from apps.accounts.models import User


def disable_device_bindings(user: User, *, installation_id: str | None = None) -> int:
    """Disable one or all push bindings for an account and clear its token.

    Logout/deactivation and ``DELETE devices/{installation_id}/`` share this
    operation: a disabled device keeps its history rows (DeliveryAttempt uses
    PROTECT) but can never be selected for a new pending attempt.  Account
    deactivation passes no installation id so every device of the account is
    unbound, matching implementation-plan 5.1 item 7.  The caller controls the
    transaction; deactivation calls it inside its existing atomic block.
    """
    now = timezone.now()
    devices = DeviceRegistration.objects.filter(user=user)
    if installation_id is not None:
        devices = devices.filter(installation_id=installation_id)
    return devices.update(
        enabled=False,
        provider_client_id=None,
        last_seen_at=now,
        updated_at=now,
    )


def notify(
    recipient: TeacherProfile,
    *,
    type: str,
    object_type: str,
    object_id: UUID | None,
    title: str,
    body: str,
    event_key: str,
) -> Notification | None:
    """Create one in-app message and pending attempts, or return ``None``.

    The recipient/event key unique constraint is the concurrency authority:
    ``get_or_create`` lets PostgreSQL resolve two simultaneous producers, and
    the loser returns ``None`` without creating duplicate attempts.  A caller
    must already have an open transaction so the message cannot outlive a
    rolled-back business change.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("notify 必须在已有数据库事务中调用。")

    notification_type = NotificationType(type)
    notification, created = Notification.objects.get_or_create(
        recipient=recipient,
        event_key=event_key,
        defaults={
            "type": notification_type,
            "object_type": object_type,
            "object_id": object_id,
            "title": title,
            "body": body,
        },
    )
    if not created:
        return None

    # A teacher profile without an account still receives the in-app message;
    # there is no device owner to address, so no delivery row is invented.
    # An inactive account is treated the same way even if a stale device row is
    # still enabled: deactivation normally disables it, but defense in depth
    # avoids creating a pending push for an account that can no longer log in.
    if recipient.user_id is None or not recipient.user.is_active:
        return notification

    now = timezone.now()
    devices = DeviceRegistration.objects.filter(user_id=recipient.user_id, enabled=True)
    DeliveryAttempt.objects.bulk_create(
        [
            DeliveryAttempt(
                notification=notification,
                device=device,
                channel=device.provider,
                attempt_no=1,
                status=DeliveryStatus.PENDING,
                next_attempt_at=now,
            )
            for device in devices
        ]
    )
    return notification
