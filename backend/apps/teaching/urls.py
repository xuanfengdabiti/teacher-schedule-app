"""B20 teaching-note routes."""

from __future__ import annotations

from django.urls import path

from apps.teaching.handoff_views import (
    HandoffAcknowledgeView,
    HandoffDetailView,
    HandoffListCreateView,
    HandoffPrefillView,
    HandoffReadView,
    HandoffWithdrawView,
)
from apps.teaching.preparation_views import PreparationGenerateView, PreparationView
from apps.teaching.progress_views import OfferingPlanView, SessionCompletionView
from apps.teaching.views import MyNotePublishView, MyNoteView, SessionNotesView

urlpatterns = [
    path("offerings/<uuid:offering_id>/plan/", OfferingPlanView.as_view(), name="offering-plan"),
    path(
        "sessions/<uuid:session_id>/completion/",
        SessionCompletionView.as_view(),
        name="session-completion",
    ),
    path("sessions/<uuid:session_id>/notes/", SessionNotesView.as_view(), name="session-notes"),
    path("sessions/<uuid:session_id>/preparation/", PreparationView.as_view(), name="session-preparation"),
    path(
        "sessions/<uuid:session_id>/preparation/generate/",
        PreparationGenerateView.as_view(),
        name="session-preparation-generate",
    ),
    path("sessions/<uuid:session_id>/notes/mine/", MyNoteView.as_view(), name="session-note-mine"),
    path(
        "sessions/<uuid:session_id>/notes/mine/publish/",
        MyNotePublishView.as_view(),
        name="session-note-publish",
    ),
    path("handoffs/prefill/", HandoffPrefillView.as_view(), name="handoff-prefill"),
    path("handoffs/", HandoffListCreateView.as_view(), name="handoff-list"),
    path("handoffs/<uuid:handoff_id>/", HandoffDetailView.as_view(), name="handoff-detail"),
    path("handoffs/<uuid:handoff_id>/read/", HandoffReadView.as_view(), name="handoff-read"),
    path(
        "handoffs/<uuid:handoff_id>/acknowledge/",
        HandoffAcknowledgeView.as_view(),
        name="handoff-acknowledge",
    ),
    path("handoffs/<uuid:handoff_id>/withdraw/", HandoffWithdrawView.as_view(), name="handoff-withdraw"),
]
