"""Tests for the custom User model and UserManager."""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import User, normalize_user_email


@pytest.mark.django_db
class TestUserModel:
    def test_create_user(self) -> None:
        user = User.objects.create_user(email="test@example.com", password="testpass123")
        assert user.pk is not None
        assert isinstance(user.pk, uuid.UUID)
        assert user.email == "test@example.com"
        assert user.is_staff is False
        assert user.is_superuser is False
        assert user.check_password("testpass123")

    def test_create_superuser(self) -> None:
        user = User.objects.create_superuser(email="admin@example.com", password="adminpass")
        assert user.is_staff is True
        assert user.is_superuser is True

    def test_email_normalized(self) -> None:
        """Email is lowercased entirely (domain + local part)."""
        user = User.objects.create_user(email="Test@Example.COM", password="p")
        assert user.email == "test@example.com"

    def test_email_unique(self) -> None:
        User.objects.create_user(email="dup@example.com", password="p1")
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(email="dup@example.com", password="p2")

    def test_email_stripped_of_spaces(self) -> None:
        """Django's normalize_email strips whitespace."""
        user = User.objects.create_user(email="  spaced@example.com  ", password="p")
        assert user.email == "spaced@example.com"

    def test_uuid_primary_key(self) -> None:
        user1 = User.objects.create_user(email="a@example.com", password="p")
        user2 = User.objects.create_user(email="b@example.com", password="p")
        assert user1.pk != user2.pk
        assert isinstance(user1.pk, uuid.UUID)
        assert isinstance(user2.pk, uuid.UUID)

    def test_username_field_is_email(self) -> None:
        assert User.USERNAME_FIELD == "email"
        assert User.REQUIRED_FIELDS == []

    def test_create_user_without_email_raises(self) -> None:
        with pytest.raises(ValueError) as exc:
            User.objects.create_user(email="", password="p")
        assert "邮箱" in str(exc.value)

    def test_create_superuser_without_is_staff_raises(self) -> None:
        with pytest.raises(ValueError):
            User.objects.create_superuser(email="s@e.com", password="p", is_staff=False)

    def test_create_superuser_without_is_superuser_raises(self) -> None:
        with pytest.raises(ValueError):
            User.objects.create_superuser(email="s@e.com", password="p", is_superuser=False)


@pytest.mark.django_db
class TestUserSaveNormalization:
    """Normalization must hold for writes that bypass ``UserManager``.

    Django Admin's add form, ``objects.create`` and future allauth signup all
    reach the database through ``Model.save()``, not ``create_user``.
    """

    def test_direct_save_normalizes_email(self) -> None:
        user = User(email="Dup@Example.COM")
        user.set_password("p")
        user.save()
        user.refresh_from_db()
        assert user.email == "dup@example.com"

    def test_direct_save_conflicts_with_existing_case_insensitive_email(self) -> None:
        User.objects.create_user(email="dup@example.com", password="p1")
        duplicate = User(email="Dup@Example.com")
        duplicate.set_password("p2")
        with pytest.raises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_direct_save_strips_surrounding_whitespace(self) -> None:
        user = User(email="  Dup@Example.com  ")
        user.set_password("p")
        user.save()
        user.refresh_from_db()
        assert user.email == "dup@example.com"

    def test_objects_create_normalizes_email(self) -> None:
        user = User.objects.create(email="Created@Example.COM", password="p")
        user.refresh_from_db()
        assert user.email == "created@example.com"

    def test_clean_normalizes_email(self) -> None:
        user = User(email="Clean@Example.COM")
        user.clean()
        assert user.email == "clean@example.com"

    def test_normalize_user_email_helper(self) -> None:
        assert normalize_user_email("  Mixed@Case.ORG  ") == "mixed@case.org"

    def test_resaving_keeps_normalized_email(self) -> None:
        user = User.objects.create_user(email="keep@example.com", password="p")
        user.last_name = "王"
        user.save()
        user.refresh_from_db()
        assert user.email == "keep@example.com"
