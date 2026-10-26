"""Custom User model with UUID primary key and email-based login."""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


def normalize_user_email(email: str) -> str:
    """Return the canonical stored form of an email address.

    ``BaseUserManager.normalize_email`` strips surrounding whitespace and
    lowercases only the domain part, preserving the local-part case because
    RFC 5321 allows it to be case-sensitive.  We additionally lowercase the
    whole address: teacher rosters are imported from spreadsheets where casing
    is inconsistent, so keeping a single stored form makes roster matching
    case-insensitive without per-query ``iexact`` and lets the unique
    constraint reject case-only duplicates.
    """
    return BaseUserManager.normalize_email(email).lower()


class UserManager(BaseUserManager["User"]):
    """Custom manager where email is the unique identifier.

    Email normalization lives in ``User.save()`` so that every write path
    (Admin add form, API registration, ``objects.create``, direct
    ``User(...).save()``) stores the same canonical value.
    """

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields) -> User:
        if not email:
            raise ValueError(_("邮箱地址为必填项。"))
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields) -> User:
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields) -> User:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True:
            raise ValueError(_("超级用户必须设置 is_staff=True。"))
        if extra_fields["is_superuser"] is not True:
            raise ValueError(_("超级用户必须设置 is_superuser=True。"))
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Custom User model.

    - UUID primary key (no auto-increment integer exposed via the API).
    - ``username`` is removed; ``email`` is the unique login identifier.
    - Email is normalized in ``clean()`` and ``save()``; ``clean()`` also makes
      model form validation compare the normalized value, so Admin and future
      allauth forms report a field error instead of relying on the database.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None  # type: ignore[assignment]
    email = models.EmailField(_("邮箱地址"), unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        db_table = "accounts_user"
        verbose_name = _("用户")
        verbose_name_plural = _("用户")

    def clean(self) -> None:
        super().clean()
        if self.email:
            self.email = normalize_user_email(self.email)

    def save(self, *args, **kwargs) -> None:
        # Last line of defence: paths that skip full_clean() (e.g. direct
        # ``User(...).save()``) must still store the canonical form.
        if self.email:
            self.email = normalize_user_email(self.email)
        super().save(*args, **kwargs)
