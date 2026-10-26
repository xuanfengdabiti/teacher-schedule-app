"""API v1 URL configuration – all routes under ``/api/v1/``."""

from django.urls import path
from drf_spectacular.views import SpectacularAPIView

from config.views import health

urlpatterns = [
    path("health/", health, name="api-health"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
]
