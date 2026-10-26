"""Schedule read endpoints (B13) and reschedule options (B30)."""

from __future__ import annotations

from django.urls import path

from apps.scheduling.views import (
    OfferingListView,
    SessionDetailView,
    SessionListView,
    TermCalendarView,
    TermListView,
    reschedule_options,
)

urlpatterns = [
    path("terms/", TermListView.as_view(), name="term-list"),
    path("terms/<uuid:term_id>/calendar/", TermCalendarView.as_view(), name="term-calendar"),
    path("sessions/", SessionListView.as_view(), name="session-list"),
    path("sessions/<uuid:session_id>/", SessionDetailView.as_view(), name="session-detail"),
    path(
        "sessions/<uuid:session_id>/reschedule-options/",
        reschedule_options,
        name="session-reschedule-options",
    ),
    path("offerings/", OfferingListView.as_view(), name="offering-list"),
]
