"""API v1 URL configuration – all routes under ``/api/v1/``."""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.permissions import AllowAny

from config.views import health

urlpatterns = [
    path("health/", health, name="api-health"),
    path(
        "schema/",
        SpectacularAPIView.as_view(
            authentication_classes=[],
            permission_classes=[AllowAny],
        ),
        name="api-schema",
    ),
    path("", include("apps.accounts.urls")),
    path("", include("apps.scheduling.urls")),
]
