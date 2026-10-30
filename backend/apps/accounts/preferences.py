"""Personal-settings helpers shared by the preferences API and notification code.

The settings row is keyed by account, not by TeacherProfile: pending accounts
may still change their own font size, reminder and term settings.  Push
delivery code must use ``apps.notifications.preferences.allows_remote_push``
so the notification-type -> category mapping lives in one place.
"""

from __future__ import annotations

from apps.academics.models import School
from apps.accounts.models import UserPreferences
from apps.accounts.services import teacher_profile_for


def preferences_for(user) -> UserPreferences:
    """Return the account's settings row, creating it with defaults if absent."""
    preferences, _ = UserPreferences.objects.get_or_create(user=user)
    return preferences


def school_for(user) -> School | None:
    """Resolve the school a preferred term must belong to.

    A linked teacher profile is authoritative.  A pending account has no
    profile yet; the product is single-school, so exactly one School row is
    accepted as its school.  Anything else returns ``None`` and the API asks
    the account to wait for teacher confirmation instead of guessing.
    """
    profile = teacher_profile_for(user)
    if profile is not None:
        return profile.school
    schools = list(School.objects.all()[:2])
    if len(schools) == 1:
        return schools[0]
    return None
