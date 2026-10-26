"""allauth adapter for mobile App links and Chinese email templates."""

from __future__ import annotations

from urllib.parse import quote

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.http import HttpResponse


class AccountAdapter(DefaultAccountAdapter):
    """Expose both App deep links and copyable raw parameters in emails."""

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        context = {**context, "app_link_base": settings.APP_LINK_BASE}
        return super().send_mail(template_prefix, email, context)

    def get_email_confirmation_url(self, request, emailconfirmation) -> str:
        key = quote(emailconfirmation.key, safe="")
        return f"{settings.APP_LINK_BASE}verify-email?key={key}"

    def respond_email_verification_sent(self, request, user) -> HttpResponse:
        """API-only flow: no allauth web page or redirect is mounted."""
        return HttpResponse(status=204)

    def respond_user_inactive(self, request, user) -> HttpResponse:
        """Avoid reversing allauth's unmounted web login page."""
        return HttpResponse(status=403)

    def get_reset_password_from_key_url(self, key: str) -> str:
        # allauth passes an opaque ``uid-token`` value to this adapter hook.
        uid, separator, token = key.partition("-")
        if not separator:
            return super().get_reset_password_from_key_url(key)
        return f"{settings.APP_LINK_BASE}reset-password?uid={quote(uid, safe='')}&token={quote(token, safe='')}"
