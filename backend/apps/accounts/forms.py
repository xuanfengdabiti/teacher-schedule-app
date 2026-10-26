"""Admin/model forms for the custom email-based User."""

from __future__ import annotations

from django.contrib.auth.forms import BaseUserCreationForm
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm

from .models import User, normalize_user_email


class UserCreationForm(BaseUserCreationForm):
    """Creation form for the email-only User.

    Django's stock ``UserCreationForm`` declares ``fields = ("username",)`` and
    therefore cannot be used with this model.
    """

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("email",)

    def clean_email(self) -> str:
        """Normalize before the uniqueness check so casing cannot slip through."""
        email = normalize_user_email(self.cleaned_data["email"])
        if User.objects.filter(email=email).exists():
            raise self.instance.unique_error_message(User, ["email"])
        return email


class UserChangeForm(DjangoUserChangeForm):
    """Change form bound to the custom User model."""

    class Meta(DjangoUserChangeForm.Meta):
        model = User
        fields = "__all__"

    def clean_email(self) -> str:
        return normalize_user_email(self.cleaned_data["email"])
