from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TeachingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.teaching"
    verbose_name = _("教学记录")
