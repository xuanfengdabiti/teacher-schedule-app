"""Admin approval/rejection shares the teacher-linking service."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.academics.models import TeacherStatus
from apps.accounts.models import TeacherClaim, TeacherClaimStatus, User

from .conftest import (
    extract_verification_key,
    jwt_client_for,
    latest_message_body,
    make_teacher_profile,
    make_user,
    register,
)

pytestmark = pytest.mark.django_db


def _make_admin_client() -> tuple[Client, User]:
    admin = User.objects.create_superuser(email="admin@example.invalid", password="admin-pass-2026")
    client = Client()
    client.force_login(admin)
    return client, admin


def _register_and_verify(api_client, **payload) -> TeacherClaim:
    assert register(api_client, **payload).status_code == 201
    response = api_client.post(
        "/api/v1/auth/registration/verify-email/",
        {"key": extract_verification_key(latest_message_body())},
        format="json",
    )
    assert response.status_code == 200
    return TeacherClaim.objects.get(user__email=payload["email"].lower())


def _run_action(client: Client, action: str, claim: TeacherClaim):
    return client.post(
        reverse("admin:accounts_teacherclaim_changelist"),
        {"action": action, "_selected_action": [str(claim.pk)]},
        follow=True,
    )


def test_admin_approval_links_matched_profile(api_client, school) -> None:
    profile = make_teacher_profile(school, "T2001", email="roster@example.invalid")
    claim = _register_and_verify(
        api_client,
        email="only-number@example.invalid",
        formal_name="待确认教师",
        teacher_no="T2001",
    )
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id == profile.id

    admin_client, admin = _make_admin_client()
    response = _run_action(admin_client, "approve_and_link", claim)

    assert response.status_code == 200
    claim.refresh_from_db()
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.APPROVED
    assert claim.decided_by_id == admin.id
    assert profile.user_id == claim.user_id
    assert profile.teacher_status == TeacherStatus.ACTIVE
    assert profile.assignment_scope_version == 2

    me = jwt_client_for(claim.user).get("/api/v1/me/")
    assert me.status_code == 200
    assert me.data["teacher_status"] == "active"


def test_admin_rejection_keeps_account_unlinked(api_client, school) -> None:
    make_teacher_profile(school, "T2002", email="roster-reject@example.invalid")
    claim = _register_and_verify(
        api_client,
        email="rejected@example.invalid",
        formal_name="被驳回教师",
        teacher_no="T2002",
    )

    admin_client, admin = _make_admin_client()
    response = _run_action(admin_client, "reject", claim)

    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == TeacherClaimStatus.REJECTED
    assert claim.decided_by_id == admin.id
    assert claim.matched_profile.user_id is None

    me = jwt_client_for(claim.user).get("/api/v1/me/")
    assert me.status_code == 200
    assert me.data["teacher_status"] == "unlinked"
    assert me.data["claim"] == {
        "status": "rejected",
        "claimed_formal_name": "被驳回教师",
        "claimed_teacher_no": "T2002",
    }


def test_admin_can_assign_profile_for_email_only_claim(api_client, school) -> None:
    profile = make_teacher_profile(school, "T2005", email="email-only-admin@example.invalid")
    claim = _register_and_verify(
        api_client,
        email="email-only-admin@example.invalid",
        formal_name="只匹配邮箱",
        teacher_no="T9998",
    )
    assert claim.matched_profile_id is None
    claim.matched_profile = profile
    claim.save(update_fields=["matched_profile", "updated_at"])

    admin_client, admin = _make_admin_client()
    response = _run_action(admin_client, "approve_and_link", claim)

    assert response.status_code == 200
    claim.refresh_from_db()
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.APPROVED
    assert claim.decided_by_id == admin.id
    assert profile.user_id == claim.user_id


def test_admin_cannot_approve_without_matched_profile(api_client, school) -> None:
    make_teacher_profile(school, "T2003", email="roster-no-match@example.invalid")
    claim = _register_and_verify(
        api_client,
        email="no-match@example.invalid",
        formal_name="邮箱相同",
        teacher_no="T9999",
    )
    assert claim.matched_profile_id is None

    admin_client, _ = _make_admin_client()
    response = _run_action(admin_client, "approve_and_link", claim)

    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING


def test_occupied_profile_cannot_be_linked_again(api_client, school) -> None:
    profile = make_teacher_profile(school, "T2004", email="roster-occupied@example.invalid")
    first_user = make_user("first-owner@example.invalid")
    profile.user = first_user
    profile.teacher_status = TeacherStatus.ACTIVE
    profile.save(update_fields=["user", "teacher_status", "updated_at"])

    claim = _register_and_verify(
        api_client,
        email="second-person@example.invalid",
        formal_name="后来教师",
        teacher_no="T2004",
    )
    assert claim.status == TeacherClaimStatus.PENDING
    assert claim.matched_profile_id == profile.id

    admin_client, _ = _make_admin_client()
    response = _run_action(admin_client, "approve_and_link", claim)

    assert response.status_code == 200
    claim.refresh_from_db()
    profile.refresh_from_db()
    assert claim.status == TeacherClaimStatus.PENDING
    assert profile.user_id == first_user.id
