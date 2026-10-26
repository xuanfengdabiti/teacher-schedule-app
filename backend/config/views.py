"""Minimal views shared at the config level (health-check)."""

from __future__ import annotations

from django.db import DatabaseError, connection
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("ok", "error"))
    database = serializers.ChoiceField(choices=("ok", "unavailable"))


@extend_schema(
    summary="健康检查",
    responses={200: HealthSerializer, 503: HealthSerializer},
    auth=[],
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request: Request) -> Response:
    """Health-check endpoint: runs a trivial query to prove the DB is usable.

    Opening a cursor alone can succeed without contacting the server, so the
    check executes ``SELECT 1`` and lets the cursor close via the context
    manager. ``DatabaseError`` covers ``OperationalError`` (server down) and
    ``InterfaceError`` (connection already dropped).
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return Response({"status": "error", "database": "unavailable"}, status=503)
    return Response({"status": "ok", "database": "ok"})
