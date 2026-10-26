"""Tests for the Admin add/change forms of the custom User.

Django's stock ``UserCreationForm`` targets ``username``; these tests pin the
email-only replacement so the Admin add page keeps working.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite

from apps.accounts.admin import UserAdmin
from apps.accounts.forms import UserCreationForm
from apps.accounts.models import User


@pytest.mark.django_db
class TestAdminAddForm:
    def test_add_form_creates_user_with_normalized_email(self) -> None:
        form = UserCreationForm(
            data={
                "email": "Admin@Example.COM",
                "password1": "a-very-long-password-9x",
                "password2": "a-very-long-password-9x",
            }
        )
        assert form.is_valid(), form.errors
        user = form.save()
        assert user.email == "admin@example.com"
        assert user.check_password("a-very-long-password-9x")

    def test_add_form_rejects_case_only_duplicate(self) -> None:
        User.objects.create_user(email="taken@example.com", password="p")
        form = UserCreationForm(
            data={
                "email": "Taken@Example.COM",
                "password1": "a-very-long-password-9x",
                "password2": "a-very-long-password-9x",
            }
        )
        assert not form.is_valid()
        assert "email" in form.errors

    def test_admin_uses_email_creation_form(self) -> None:
        model_admin = UserAdmin(User, AdminSite())
        assert model_admin.add_form is UserCreationForm
        add_fieldsets = model_admin.get_fieldsets(request=None, obj=None)
        flattened = [field for _, opts in add_fieldsets for field in opts["fields"]]
        assert "email" in flattened
        assert "username" not in flattened
