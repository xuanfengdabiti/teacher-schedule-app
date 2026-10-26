"""Account lifecycle hooks."""

from __future__ import annotations

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.dispatch import receiver

from apps.accounts.services import match_teacher_claim


@receiver(email_confirmed, sender=EmailAddress)
def link_teacher_claim_after_email_confirmation(sender, request, email_address, **kwargs) -> None:
    """Try the exact roster match only after the address is verified."""
    if not email_address.verified:
        return
    match_teacher_claim(email_address.user)
