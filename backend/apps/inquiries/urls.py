"""B33 inquiry routes."""

from __future__ import annotations

from django.urls import path

from apps.inquiries.views import (
    InquiryCandidatesView,
    InquiryCloseView,
    InquiryDetailView,
    InquiryListCreateView,
    InquiryReadView,
    InquiryReplyCreateView,
)

urlpatterns = [
    path("inquiries/candidates/", InquiryCandidatesView.as_view(), name="inquiry-candidates"),
    path("inquiries/", InquiryListCreateView.as_view(), name="inquiry-list"),
    path("inquiries/<uuid:inquiry_id>/", InquiryDetailView.as_view(), name="inquiry-detail"),
    path("inquiries/<uuid:inquiry_id>/read/", InquiryReadView.as_view(), name="inquiry-read"),
    path(
        "inquiries/<uuid:inquiry_id>/replies/",
        InquiryReplyCreateView.as_view(),
        name="inquiry-reply-create",
    ),
    path("inquiries/<uuid:inquiry_id>/close/", InquiryCloseView.as_view(), name="inquiry-close"),
]
