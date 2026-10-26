"""Notification and device routes under ``/api/v1/``."""

from __future__ import annotations

from django.urls import path

from apps.notifications.views import DeviceRegistrationView, NotificationListView, NotificationReadView

urlpatterns = [
    path("notifications/", NotificationListView.as_view(), name="notification-list"),
    path(
        "notifications/<uuid:notification_id>/read/",
        NotificationReadView.as_view(),
        name="notification-read",
    ),
    path(
        "devices/<str:installation_id>/",
        DeviceRegistrationView.as_view(),
        name="device-registration",
    ),
]
