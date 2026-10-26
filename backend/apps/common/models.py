"""Abstract model base shared by all business tables."""

from __future__ import annotations

import uuid

from django.db import models


class TimeStampedUUIDModel(models.Model):
    """UUID primary key plus creation/update timestamps.

    Business tables use UUID primary keys so identifiers can be minted by the
    server (and referenced by clients) without exposing row counts.  Domain
    tables that are pure association tables or counters may opt out, as
    allowed by ``docs/domain-design.md`` section 2.1.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        abstract = True
