"""Minimal views shared at the config level (health-check)."""

from __future__ import annotations

from django.db import DatabaseError, connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response


@api_view(["GET"])
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
