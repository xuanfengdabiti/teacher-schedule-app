"""Account services: teacher linking, token revocation and deactivation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.academics.models import ContactVisibility, School, TeacherProfile, TeacherStatus
from apps.accounts.models import TeacherClaim, TeacherClaimStatus, normalize_user_email
from apps.common.exceptions import TeacherDisabled, TeacherNotLinked

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

AUTO_MATCH_NOTE = "邮箱与工号自动匹配"


class TeacherLinkError(Exception):
    """A claim/profile pair cannot be linked without violating a rule."""


def teacher_profile_for(user: User) -> TeacherProfile | None:
    try:
        return user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return None


def teacher_claim_for(user: User) -> TeacherClaim | None:
    try:
        return user.teacher_claim
    except TeacherClaim.DoesNotExist:
        return None


def teacher_status_for(user: User) -> str:
    """Return the shared ``active/pending/disabled/unlinked`` teacher status."""
    profile = teacher_profile_for(user)
    if not user.is_active:
        return "disabled"
    if profile and (not profile.is_active or profile.teacher_status == TeacherStatus.DISABLED):
        return "disabled"
    if profile and profile.teacher_status == TeacherStatus.ACTIVE:
        return "active"
    claim = teacher_claim_for(user)
    if claim and claim.status == TeacherClaimStatus.PENDING:
        return "pending"
    return "unlinked"


def require_active_teacher(user: User) -> TeacherProfile:
    """Return the linked active profile or raise the stable 403 API error."""
    status = teacher_status_for(user)
    if status == "active":
        profile = teacher_profile_for(user)
        if profile is not None:
            return profile
    elif status == "disabled":
        raise TeacherDisabled()
    raise TeacherNotLinked()


def revoke_all_refresh_tokens(user: User) -> None:
    """Blacklist every refresh token ever issued to ``user``.

    OutstandingToken is SimpleJWT's own ledger.  Access tokens are not stored
    there; they remain valid until their (30 minute) expiry.  Password reset,
    password change and deactivation call this helper.
    """
    outstanding = OutstandingToken.objects.filter(user=user)
    already_blacklisted = BlacklistedToken.objects.filter(token__in=outstanding).values_list("token_id", flat=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in outstanding.exclude(pk__in=already_blacklisted)],
        ignore_conflicts=True,
    )


def link_user_to_profile(
    claim: TeacherClaim,
    profile: TeacherProfile,
    *,
    decided_by: User | None = None,
    note: str = "",
) -> TeacherClaim:
    """Bind a pending claim to an unoccupied, active TeacherProfile.

    Automatic matching and the Admin approval action share this function so
    their transaction and locking order cannot drift apart.
    """
    with transaction.atomic():
        locked_claim = TeacherClaim.objects.select_for_update().select_related("user").get(pk=claim.pk)
        if locked_claim.status != TeacherClaimStatus.PENDING:
            raise TeacherLinkError("该教师身份申报已处理，不能重复关联。")

        locked_profile = TeacherProfile.objects.select_for_update().get(pk=profile.pk)
        if not locked_profile.is_active:
            raise TeacherLinkError("所选教师档案已停用，不能关联。")
        if locked_profile.user_id and locked_profile.user_id != locked_claim.user_id:
            raise TeacherLinkError("所选教师档案已关联其他账号，不能重复关联。")
        if TeacherProfile.objects.filter(user_id=locked_claim.user_id).exclude(pk=locked_profile.pk).exists():
            raise TeacherLinkError("该账号已关联其他教师档案。")

        locked_profile.user = locked_claim.user
        locked_profile.teacher_status = TeacherStatus.ACTIVE
        locked_profile.assignment_scope_version += 1
        locked_profile.save(
            update_fields=[
                "user",
                "teacher_status",
                "assignment_scope_version",
                "updated_at",
            ]
        )

        locked_claim.matched_profile = locked_profile
        locked_claim.status = TeacherClaimStatus.APPROVED
        locked_claim.decided_by = decided_by
        locked_claim.decided_at = timezone.now()
        locked_claim.note = note
        locked_claim.save(
            update_fields=[
                "matched_profile",
                "status",
                "decided_by",
                "decided_at",
                "note",
                "updated_at",
            ]
        )
        return locked_claim


def _single_school() -> School | None:
    schools = list(School.objects.all()[:2])
    if len(schools) != 1:
        logger.warning("教师自动关联跳过：当前部署的 School 数量不是恰好一个。")
        return None
    return schools[0]


def _teacher_match_note(profile: TeacherProfile, email: str) -> str:
    if profile.email != email:
        return "仅工号匹配，邮件与工号未同时匹配，等待教务核对。"
    if not profile.is_active:
        return "邮箱与工号均匹配，但教师档案已停用，等待教务核对。"
    if profile.user_id is not None:
        return "邮箱与工号均匹配，但教师档案已关联其他账号，等待教务核对。"
    return "邮箱与工号均匹配，但未能自动关联，等待教务核对。"


def match_teacher_claim(user: User) -> TeacherClaim | None:
    """Try to auto-link a pending claim after email verification.

    Only an exact school + teacher number + normalized email match to one
    active, unoccupied profile may link automatically.  A teacher-number-only
    match is recorded for staff follow-up but never linked here.
    """
    try:
        claim = TeacherClaim.objects.select_related("user").get(
            user=user,
            status=TeacherClaimStatus.PENDING,
        )
    except TeacherClaim.DoesNotExist:
        return None
    if not user.is_active:
        return None

    school = _single_school()
    if school is None:
        return None

    teacher_no_matches = TeacherProfile.objects.filter(
        school=school,
        teacher_no=claim.claimed_teacher_no,
    )
    teacher_no_match = teacher_no_matches.first()
    email = normalize_user_email(user.email)
    auto_candidates = teacher_no_matches.filter(
        email=email,
        is_active=True,
        user__isnull=True,
    )
    if auto_candidates.count() == 1:
        profile = auto_candidates.get()
        try:
            return link_user_to_profile(claim, profile, note=AUTO_MATCH_NOTE)
        except TeacherLinkError:
            # Another request may have occupied or disabled the profile between
            # the query and the row lock.  Keep the claim pending for staff.
            claim.refresh_from_db(fields=["status"])
            if claim.status != TeacherClaimStatus.PENDING:
                return claim
            teacher_no_match.refresh_from_db()

    if teacher_no_match:
        claim.matched_profile = teacher_no_match
        claim.note = _teacher_match_note(teacher_no_match, email)
        claim.save(update_fields=["matched_profile", "note", "updated_at"])
    return claim


def reject_teacher_claim(claim: TeacherClaim, *, decided_by: User, note: str = "教务驳回") -> TeacherClaim:
    """Reject a still-pending claim without touching any teacher profile."""
    with transaction.atomic():
        locked_claim = TeacherClaim.objects.select_for_update().get(pk=claim.pk)
        if locked_claim.status != TeacherClaimStatus.PENDING:
            raise TeacherLinkError("该教师身份申报已处理，不能重复驳回。")
        locked_claim.status = TeacherClaimStatus.REJECTED
        locked_claim.decided_by = decided_by
        locked_claim.decided_at = timezone.now()
        locked_claim.note = note
        locked_claim.save(update_fields=["status", "decided_by", "decided_at", "note", "updated_at"])
        return locked_claim


def deactivate_user(user: User) -> None:
    """Disable an account, revoke all refresh tokens and clear contacts."""
    user_model = get_user_model()
    with transaction.atomic():
        locked_user = user_model.objects.select_for_update().get(pk=user.pk)
        locked_user.is_active = False
        locked_user.save(update_fields=["is_active"])

        revoke_all_refresh_tokens(locked_user)

        TeacherProfile.objects.filter(user=locked_user).update(
            phone="",
            wechat="",
            phone_visibility=ContactVisibility.PRIVATE,
            wechat_visibility=ContactVisibility.PRIVATE,
            updated_at=timezone.now(),
        )
