"""
URL configuration for the teacher schedule backend.

Prefix ``/api/v1/`` for all API endpoints; ``/admin/`` for Django Admin.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_urls")),
]
