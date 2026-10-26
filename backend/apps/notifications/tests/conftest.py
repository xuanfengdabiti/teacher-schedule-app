"""Shared fixtures for notification service/API tests."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.scheduling.tests import factories


@pytest.fixture
def school(db):
    return factories.make_school(code="NOTIF-SCHOOL", name="消息测试虚构学校")


@pytest.fixture
def teacher(school):
    return factories.make_teacher(school, "N0001", "消息教师甲")


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()
