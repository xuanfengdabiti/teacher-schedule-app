"""Tests for the health-check endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db.utils import OperationalError
from rest_framework.test import APIClient


class TestHealthEndpoint:
    @pytest.mark.django_db
    def test_returns_200_when_db_is_ok(self, api_client: APIClient) -> None:
        response = api_client.get("/api/v1/health/")
        assert response.status_code == 200
        assert response.data == {"status": "ok", "database": "ok"}  # type: ignore[attr-defined]

    def test_returns_503_when_db_is_down(self, api_client: APIClient) -> None:
        """A failing ``SELECT 1`` yields 503 without touching the real server."""
        with patch("django.db.backends.base.base.BaseDatabaseWrapper.cursor") as mock_cursor:
            mock_cursor.side_effect = OperationalError("connection refused")
            response = api_client.get("/api/v1/health/")
        assert response.status_code == 503
        assert response.data == {  # type: ignore[attr-defined]
            "status": "error",
            "database": "unavailable",
        }
