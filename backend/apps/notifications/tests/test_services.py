"""notify() deduplication and pending-delivery tests (B14)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.db import transaction

from apps.notifications.models import DeliveryAttempt, DeliveryStatus, DeviceRegistration, Notification
from apps.notifications.services import notify
from apps.notifications.tests.helpers import link_teacher
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db


def _notify(teacher, *, event_key: str = "event-1"):
    with transaction.atomic():
        return notify(
            teacher,
            type="schedule_changed",
            object_type="session",
            object_id=uuid4(),
            title="课表已更新",
            body="示例课程更换了教室。",
            event_key=event_key,
        )


class TestNotifyDeduplication:
    def test_same_event_key_creates_only_one_notification_and_no_extra_attempts(self, teacher) -> None:
        user = link_teacher(teacher, "notify-dedupe@example.invalid")
        DeviceRegistration.objects.create(
            user=user,
            installation_id="install-dedupe",
            provider="getui",
            platform="android",
        )

        first = _notify(teacher)
        second = _notify(teacher)

        assert first is not None
        assert second is None
        assert Notification.objects.filter(recipient=teacher).count() == 1
        assert DeliveryAttempt.objects.filter(notification=first).count() == 1

    def test_same_event_key_is_scoped_by_recipient(self, school, teacher) -> None:
        other = factories.make_teacher(school, "N0002", "消息教师乙")
        link_teacher(teacher, "notify-scope-a@example.invalid")
        link_teacher(other, "notify-scope-b@example.invalid")

        first = _notify(teacher)
        second = _notify(other)

        assert first is not None
        assert second is not None
        assert Notification.objects.count() == 2


class TestNotifyDeliveries:
    def test_creates_one_pending_attempt_for_each_enabled_device(self, teacher) -> None:
        user = link_teacher(teacher, "notify-devices@example.invalid")
        DeviceRegistration.objects.create(
            user=user,
            installation_id="install-getui",
            provider="getui",
            platform="android",
        )
        DeviceRegistration.objects.create(
            user=user,
            installation_id="install-apns",
            provider="apns",
            platform="ios",
        )
        DeviceRegistration.objects.create(
            user=user,
            installation_id="install-disabled",
            provider="getui",
            platform="android",
            enabled=False,
        )

        notification = _notify(teacher)

        assert notification is not None
        attempts = list(DeliveryAttempt.objects.filter(notification=notification).order_by("channel"))
        assert len(attempts) == 2
        assert {attempt.channel for attempt in attempts} == {"apns", "getui"}
        assert all(attempt.status == DeliveryStatus.PENDING for attempt in attempts)
        assert all(attempt.attempt_no == 1 for attempt in attempts)
        assert all(attempt.next_attempt_at is not None for attempt in attempts)
        assert all(attempt.provider_message_id == "" for attempt in attempts)
        assert all(attempt.provider_delivered_at is None for attempt in attempts)

    def test_inactive_account_with_enabled_device_gets_no_delivery_attempt(self, teacher) -> None:
        user = link_teacher(teacher, "notify-inactive@example.invalid")
        DeviceRegistration.objects.create(
            user=user,
            installation_id="install-inactive",
            provider="getui",
            platform="android",
        )
        user.is_active = False
        user.save(update_fields=["is_active"])

        notification = _notify(teacher)

        assert notification is not None
        assert DeviceRegistration.objects.get(installation_id="install-inactive").enabled is True
        assert not DeliveryAttempt.objects.exists()

    def test_teacher_without_account_gets_only_the_in_app_message(self, teacher) -> None:
        notification = _notify(teacher)

        assert notification is not None
        assert notification.recipient == teacher
        assert not DeliveryAttempt.objects.exists()

    def test_devices_of_another_account_are_never_targeted(self, school, teacher) -> None:
        other = factories.make_teacher(school, "N0003", "消息教师丙")
        other_user = link_teacher(other, "notify-other-device@example.invalid")
        DeviceRegistration.objects.create(
            user=other_user,
            installation_id="install-other",
            provider="getui",
            platform="android",
        )

        notification = _notify(teacher)

        assert notification is not None
        assert not DeliveryAttempt.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_notify_outside_transaction_raises_without_writing() -> None:
    school = factories.make_school(code="NOTIF-NO-TX", name="消息事务测试虚构学校")
    teacher = factories.make_teacher(school, "N0009", "消息事务教师")

    with pytest.raises(RuntimeError, match="事务"):
        notify(
            teacher,
            type="schedule_changed",
            object_type="session",
            object_id=uuid4(),
            title="不应落库",
            body="没有外层事务。",
            event_key="event-no-tx",
        )

    assert not Notification.objects.exists()
    assert not DeliveryAttempt.objects.exists()
