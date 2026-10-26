"""Notification-centre and device-registration API tests (B14)."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.contrib import admin
from django.test import RequestFactory
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import TeacherStatus
from apps.accounts.models import TeacherClaim
from apps.notifications.admin import DeliveryAttemptAdmin, DeviceRegistrationAdmin, NotificationAdmin
from apps.notifications.models import (
    DeliveryAttempt,
    DeliveryStatus,
    DeviceRegistration,
    Notification,
)
from apps.notifications.tests.helpers import link_teacher, make_user
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db

TZ = ZoneInfo("Asia/Shanghai")
BASE_TIME = datetime(2026, 10, 1, 8, 0, tzinfo=TZ)


def _authenticate(client: APIClient, user) -> APIClient:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def _make_notification(teacher, index: int, *, read: bool = False) -> Notification:
    notification = Notification.objects.create(
        recipient=teacher,
        type="schedule_changed",
        object_type="session",
        object_id=uuid4(),
        title=f"示例消息 {index}",
        body=f"示例消息正文 {index}",
        event_key=f"notification-test-{index}",
    )
    Notification.objects.filter(pk=notification.pk).update(
        created_at=BASE_TIME + timedelta(minutes=index),
        read_at=BASE_TIME if read else None,
    )
    notification.refresh_from_db()
    return notification


def _device_payload(
    *,
    provider: str = "getui",
    provider_client_id: str | None = "client-1",
    platform: str = "android",
    enabled: bool = True,
) -> dict:
    return {
        "provider": provider,
        "provider_client_id": provider_client_id,
        "platform": platform,
        "enabled": enabled,
    }


class TestNotificationList:
    def test_cursor_pagination_has_no_duplicates_or_omissions(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "notif-list@example.invalid")
        _authenticate(api_client, user)
        notifications = [_make_notification(teacher, index) for index in range(45)]
        expected = [str(item.id) for item in reversed(notifications)]

        cursor = None
        seen: list[str] = []
        page_sizes: list[int] = []
        for _ in range(3):
            params = {"limit": 20}
            if cursor:
                params["cursor"] = cursor
            response = api_client.get("/api/v1/notifications/", params)
            assert response.status_code == 200
            page_sizes.append(len(response.data["results"]))
            seen.extend(item["id"] for item in response.data["results"])
            assert response.data["unread_count"] == 45
            cursor = response.data["next_cursor"]

        assert page_sizes == [20, 20, 5]
        assert cursor is None
        assert seen == expected
        assert len(set(seen)) == 45

    def test_unread_count_and_read_are_idempotent(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "notif-read@example.invalid")
        _authenticate(api_client, user)
        notification = _make_notification(teacher, 1)
        _make_notification(teacher, 2, read=True)

        listing = api_client.get("/api/v1/notifications/")
        assert listing.status_code == 200
        assert listing.data["unread_count"] == 1

        first = api_client.post(f"/api/v1/notifications/{notification.id}/read/")
        second = api_client.post(f"/api/v1/notifications/{notification.id}/read/")

        assert first.status_code == second.status_code == 200
        assert first.data["read_at"] == second.data["read_at"]
        assert first.data["is_read"] is True
        assert api_client.get("/api/v1/notifications/").data["unread_count"] == 0

    def test_other_teachers_notification_is_404(self, api_client, teacher, school) -> None:
        user = link_teacher(teacher, "notif-owner@example.invalid")
        other = factories.make_teacher(school, "N0002", "消息教师乙")
        link_teacher(other, "notif-other@example.invalid")
        other_notification = _make_notification(other, 1)
        _authenticate(api_client, user)

        response = api_client.post(f"/api/v1/notifications/{other_notification.id}/read/")

        assert response.status_code == 404

    def test_invalid_cursor_returns_stable_400(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "notif-bad-cursor@example.invalid")
        _authenticate(api_client, user)
        _make_notification(teacher, 1)

        response = api_client.get("/api/v1/notifications/", {"cursor": "not-a-cursor"})

        assert response.status_code == 400
        assert response.data["code"] == "INVALID_CURSOR"


class TestNotificationAndDevicePermissions:
    def test_unlinked_account_is_403_for_notifications_and_devices(self, api_client) -> None:
        _authenticate(api_client, make_user("notif-unlinked@example.invalid"))

        list_response = api_client.get("/api/v1/notifications/")
        device_response = api_client.put(
            "/api/v1/devices/install-unlinked/",
            _device_payload(),
            format="json",
        )

        assert list_response.status_code == 403
        assert list_response.data["code"] == "TEACHER_NOT_LINKED"
        assert device_response.status_code == 403
        assert device_response.data["code"] == "TEACHER_NOT_LINKED"

    def test_disabled_teacher_is_403_for_notifications_and_devices(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "notif-disabled@example.invalid")
        teacher.is_active = False
        teacher.teacher_status = TeacherStatus.DISABLED
        teacher.save(update_fields=["is_active", "teacher_status", "updated_at"])
        _authenticate(api_client, user)

        list_response = api_client.get("/api/v1/notifications/")
        device_response = api_client.put(
            "/api/v1/devices/install-disabled/",
            _device_payload(),
            format="json",
        )

        assert list_response.status_code == 403
        assert list_response.data["code"] == "TEACHER_DISABLED"
        assert device_response.status_code == 403
        assert device_response.data["code"] == "TEACHER_DISABLED"


class TestDeviceRegistration:
    def test_put_creates_then_updates_one_device(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "device-update@example.invalid")
        _authenticate(api_client, user)

        created = api_client.put(
            "/api/v1/devices/install-update/",
            _device_payload(),
            format="json",
        )
        assert created.status_code == 200
        DeviceRegistration.objects.filter(installation_id="install-update").update(last_seen_at=BASE_TIME)
        device = DeviceRegistration.objects.get(installation_id="install-update")
        assert device.user == user
        assert device.enabled is True
        old_seen = device.last_seen_at

        updated = api_client.put(
            "/api/v1/devices/install-update/",
            _device_payload(
                provider="apns",
                provider_client_id="client-2",
                platform="ios",
                enabled=False,
            ),
            format="json",
        )

        assert updated.status_code == 200
        device.refresh_from_db()
        assert DeviceRegistration.objects.filter(installation_id="install-update").count() == 1
        assert device.provider == "apns"
        assert device.provider_client_id == "client-2"
        assert device.platform == "ios"
        assert device.enabled is False
        assert device.last_seen_at > old_seen

    def test_same_installation_transfers_to_the_current_account(self, api_client, teacher, school) -> None:
        first_user = link_teacher(teacher, "device-transfer-a@example.invalid")
        other = factories.make_teacher(school, "N0002", "设备教师乙")
        second_user = link_teacher(other, "device-transfer-b@example.invalid")
        _authenticate(api_client, first_user)
        assert api_client.put("/api/v1/devices/install-transfer/", _device_payload(), format="json").status_code == 200

        second_client = _authenticate(APIClient(), second_user)
        response = second_client.put(
            "/api/v1/devices/install-transfer/",
            _device_payload(provider="apns", platform="ios"),
            format="json",
        )

        assert response.status_code == 200
        assert DeviceRegistration.objects.filter(installation_id="install-transfer").count() == 1
        device = DeviceRegistration.objects.get(installation_id="install-transfer")
        assert device.user == second_user
        assert device.provider == "apns"
        assert device.platform == "ios"

    def test_delete_disables_own_device_clears_client_id_and_is_idempotent(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "device-delete@example.invalid")
        _authenticate(api_client, user)
        device = DeviceRegistration.objects.create(
            user=user,
            installation_id="install-delete",
            provider="getui",
            provider_client_id="client-delete",
            platform="android",
        )
        notification = _make_notification(teacher, 1)
        attempt = DeliveryAttempt.objects.create(
            notification=notification,
            device=device,
            channel="getui",
            status=DeliveryStatus.PENDING,
        )

        first = api_client.delete("/api/v1/devices/install-delete/")
        second = api_client.delete("/api/v1/devices/install-delete/")

        assert first.status_code == second.status_code == 204
        device.refresh_from_db()
        assert device.enabled is False
        assert device.provider_client_id is None
        assert DeliveryAttempt.objects.filter(pk=attempt.pk).exists()

    def test_delete_absent_or_other_account_device_returns_204_without_touching_it(
        self,
        api_client,
        teacher,
        school,
    ) -> None:
        user = link_teacher(teacher, "device-delete-owner@example.invalid")
        other = factories.make_teacher(school, "N0002", "设备教师乙")
        other_user = link_teacher(other, "device-delete-other@example.invalid")
        other_device = DeviceRegistration.objects.create(
            user=other_user,
            installation_id="install-other-owner",
            provider="apns",
            provider_client_id="other-client",
            platform="ios",
        )
        _authenticate(api_client, user)

        missing = api_client.delete("/api/v1/devices/does-not-exist/")
        foreign = api_client.delete("/api/v1/devices/install-other-owner/")

        assert missing.status_code == 204
        assert foreign.status_code == 204
        other_device.refresh_from_db()
        assert other_device.user == other_user
        assert other_device.enabled is True
        assert other_device.provider_client_id == "other-client"

    def test_disabled_teacher_can_deregister_device_with_login_only(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "device-disabled-delete@example.invalid")
        teacher.is_active = False
        teacher.teacher_status = TeacherStatus.DISABLED
        teacher.save(update_fields=["is_active", "teacher_status", "updated_at"])
        device = DeviceRegistration.objects.create(
            user=user,
            installation_id="install-disabled-delete",
            provider="getui",
            provider_client_id="disabled-client",
            platform="android",
        )
        _authenticate(api_client, user)

        response = api_client.delete("/api/v1/devices/install-disabled-delete/")

        assert response.status_code == 204
        device.refresh_from_db()
        assert device.enabled is False
        assert device.provider_client_id is None

    def test_pending_account_can_deregister_device_with_login_only(self, api_client) -> None:
        user = make_user("device-pending-delete@example.invalid")
        TeacherClaim.objects.create(
            user=user,
            claimed_formal_name="待确认教师",
            claimed_teacher_no="PENDING-001",
        )
        device = DeviceRegistration.objects.create(
            user=user,
            installation_id="install-pending-delete",
            provider="apns",
            provider_client_id="pending-client",
            platform="ios",
        )
        _authenticate(api_client, user)

        response = api_client.delete("/api/v1/devices/install-pending-delete/")

        assert response.status_code == 204
        device.refresh_from_db()
        assert device.enabled is False
        assert device.provider_client_id is None

    def test_put_missing_provider_client_id_stores_null(self, api_client, teacher) -> None:
        user = link_teacher(teacher, "device-null-client@example.invalid")
        _authenticate(api_client, user)
        payload = _device_payload()
        payload.pop("provider_client_id")

        response = api_client.put("/api/v1/devices/install-null/", payload, format="json")

        assert response.status_code == 200
        assert DeviceRegistration.objects.get(installation_id="install-null").provider_client_id is None


class TestNotificationAdminIsReadOnly:
    def test_all_three_admin_classes_refuse_writes(self) -> None:
        request = RequestFactory().get("/admin/")
        request.user = make_user("admin-readonly@example.invalid")
        request.user.is_staff = True
        request.user.is_superuser = True

        for model, model_admin in (
            (Notification, NotificationAdmin),
            (DeviceRegistration, DeviceRegistrationAdmin),
            (DeliveryAttempt, DeliveryAttemptAdmin),
        ):
            instance = model_admin(model, admin.site)
            assert instance.has_add_permission(request) is False
            assert instance.has_change_permission(request) is False
            assert instance.has_delete_permission(request) is False
