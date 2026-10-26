"""Account URL routes under ``/api/v1/``."""

from __future__ import annotations

from dj_rest_auth.registration.views import RegisterView, ResendEmailVerificationView, VerifyEmailView
from dj_rest_auth.views import LoginView, PasswordResetConfirmView, PasswordResetView
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.views import DeactivationView, LogoutAPIView, MeView, PasswordChangeAPIView

urlpatterns = [
    path(
        "auth/registration/",
        RegisterView.as_view(permission_classes=[AllowAny]),
        name="account-register",
    ),
    path(
        "auth/registration/verify-email/",
        VerifyEmailView.as_view(permission_classes=[AllowAny]),
        name="account-verify-email",
    ),
    path(
        "auth/registration/resend-email/",
        ResendEmailVerificationView.as_view(permission_classes=[AllowAny]),
        name="account-resend-email",
    ),
    path(
        "auth/login/",
        LoginView.as_view(permission_classes=[AllowAny]),
        name="account-login",
    ),
    path("auth/logout/", LogoutAPIView.as_view(), name="account-logout"),
    path(
        "auth/token/refresh/",
        TokenRefreshView.as_view(permission_classes=[AllowAny]),
        name="account-token-refresh",
    ),
    path(
        "auth/password/reset/",
        PasswordResetView.as_view(permission_classes=[AllowAny]),
        name="account-password-reset",
    ),
    path(
        "auth/password/reset/confirm/",
        PasswordResetConfirmView.as_view(permission_classes=[AllowAny]),
        name="account-password-reset-confirm",
    ),
    path("auth/password/change/", PasswordChangeAPIView.as_view(), name="account-password-change"),
    path("me/", MeView.as_view(), name="account-me"),
    path("me/deactivation/", DeactivationView.as_view(), name="account-deactivation"),
]
