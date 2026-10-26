"""HTTP endpoints for the notification centre and push-device binding."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import require_active_teacher
from apps.notifications.models import DeviceRegistration, Notification
from apps.notifications.serializers import (
    DeviceInputSerializer,
    DeviceSerializer,
    NotificationListResponseSerializer,
    NotificationQuerySerializer,
    NotificationSerializer,
)
from apps.notifications.services import disable_device_bindings


class InvalidNotificationCursorError(ValueError):
    """Raised when the opaque pagination cursor cannot be decoded."""


def encode_notification_cursor(notification: Notification) -> str:
    """Encode the keyset (created_at, id) without exposing an offset.

    The value is intentionally opaque.  Base64 is not encryption; it only
    prevents clients from treating the timestamp as a contract and lets the
    server change the internal cursor representation later.
    """
    raw = f"{notification.created_at.isoformat()}|{notification.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_notification_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        created_at_text, identifier = raw.rsplit("|", 1)
        created_at = datetime.fromisoformat(created_at_text)
        notification_id = UUID(identifier)
    except (ValueError, UnicodeDecodeError, binascii.Error, TypeError) as exc:
        raise InvalidNotificationCursorError from exc
    if created_at.tzinfo is None:
        raise InvalidNotificationCursorError
    return created_at, notification_id


def _invalid_cursor_response() -> Response:
    return Response(
        {
            "code": "INVALID_CURSOR",
            "detail": "消息游标无效，请从第一页重新加载。",
            "field_errors": {},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class NotificationListView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="本人消息",
        description=("按 created_at、id 倒序，使用不透明游标分页；limit 默认 20、最大 50。同时返回当前未读总数。"),
        parameters=[NotificationQuerySerializer],
        responses={200: NotificationListResponseSerializer, 400: None, 403: None},
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = NotificationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        limit = query.validated_data["limit"]

        notifications = Notification.objects.filter(recipient=teacher)
        raw_cursor = query.validated_data.get("cursor")
        if raw_cursor:
            try:
                created_at, notification_id = decode_notification_cursor(raw_cursor)
            except InvalidNotificationCursorError:
                return _invalid_cursor_response()
            notifications = notifications.filter(
                Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=notification_id)
            )

        page = list(notifications.order_by("-created_at", "-id")[: limit + 1])
        has_more = len(page) > limit
        page = page[:limit]
        next_cursor = encode_notification_cursor(page[-1]) if has_more and page else None
        unread_count = Notification.objects.filter(recipient=teacher, read_at__isnull=True).count()

        return Response(
            NotificationListResponseSerializer(
                {
                    "results": page,
                    "next_cursor": next_cursor,
                    "unread_count": unread_count,
                }
            ).data
        )


class NotificationReadView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="标记消息已读",
        description="重复调用返回同一 read_at；他人消息返回 404。",
        request=None,
        responses={200: NotificationSerializer, 403: None, 404: None},
    )
    def post(self, request: Request, notification_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        with transaction.atomic():
            notification = get_object_or_404(
                Notification.objects.select_for_update(),
                pk=notification_id,
                recipient=teacher,
            )
            if notification.read_at is None:
                notification.read_at = timezone.now()
                notification.save(update_fields=["read_at", "updated_at"])
        return Response(NotificationSerializer(notification).data)


class DeviceRegistrationView(APIView):
    permission_classes = (IsAuthenticated,)

    def _validate_installation_id(self, installation_id: str) -> None:
        if not installation_id or len(installation_id) > 255:
            raise ValidationError({"installation_id": ["安装 ID 必须为 1–255 个字符。"]})

    @extend_schema(
        summary="注册或更新本机推送设备",
        request=DeviceInputSerializer,
        responses={200: DeviceSerializer, 400: None, 403: None},
    )
    def put(self, request: Request, installation_id: str) -> Response:
        require_active_teacher(request.user)
        self._validate_installation_id(installation_id)
        payload = DeviceInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        values = payload.validated_data

        now = timezone.now()
        with transaction.atomic():
            device, created = DeviceRegistration.objects.get_or_create(
                installation_id=installation_id,
                defaults={
                    "user": request.user,
                    "provider": values["provider"],
                    "provider_client_id": values.get("provider_client_id"),
                    "platform": values["platform"],
                    "enabled": values["enabled"],
                    "last_seen_at": now,
                },
            )
            if not created:
                # One physical installation has one current account.  If the
                # same phone was used by another teacher and then logs in as
                # this one, the binding MUST move to the current account;
                # keeping the old owner would deliver that teacher's messages
                # to the new user's phone.  Pending attempts are historical
                # rows until B40 runs; the future sender must re-check that the
                # device still belongs to the notification recipient.
                device = DeviceRegistration.objects.select_for_update().get(pk=device.pk)
                device.user = request.user
                device.provider = values["provider"]
                device.provider_client_id = values.get("provider_client_id")
                device.platform = values["platform"]
                device.enabled = values["enabled"]
                device.last_seen_at = now
                device.save(
                    update_fields=[
                        "user",
                        "provider",
                        "provider_client_id",
                        "platform",
                        "enabled",
                        "last_seen_at",
                        "updated_at",
                    ]
                )
        return Response(DeviceSerializer(device).data)

    @extend_schema(
        summary="注销本机推送设备",
        description=(
            "只要求登录，不要求启用中的教师档案：设备属于账号，退出时即使档案停用或身份待确认也必须能解绑。"
            "幂等：设备不存在或属于他人时同样返回 204，不泄露安装 ID 的归属。"
            "实现上保留设备行并置 enabled=false、清空 provider_client_id，"
            "因为历史 DeliveryAttempt 使用 PROTECT 且需要保留投递审计。"
        ),
        responses={204: None, 401: None},
    )
    def delete(self, request: Request, installation_id: str) -> Response:
        disable_device_bindings(request.user, installation_id=installation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
