"""B20 teaching-note routes."""

from __future__ import annotations

from django.urls import path

from apps.teaching.views import MyNotePublishView, MyNoteView, SessionNotesView

urlpatterns = [
    path("sessions/<uuid:session_id>/notes/", SessionNotesView.as_view(), name="session-notes"),
    path("sessions/<uuid:session_id>/notes/mine/", MyNoteView.as_view(), name="session-note-mine"),
    path(
        "sessions/<uuid:session_id>/notes/mine/publish/",
        MyNotePublishView.as_view(),
        name="session-note-publish",
    ),
]
