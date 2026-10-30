"""Personal preferences and authenticated avatar endpoints (B10b)."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from apps.academics.models import School, TeacherStatus, Term
from apps.accounts.models import TeacherClaim, UserPreferences

from .conftest import jwt_client_for, make_teacher_profile, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return settings.MEDIA_ROOT


def _term(school: School, code: str = "2026-autumn", *, offset_days: int = 0) -> Term:
    start = timezone.localdate() + timedelta(days=offset_days)
    return Term.objects.create(
        school=school,
        code=code,
        name=f"示例学期 {code}",
        start_date=start,
        end_date=start + timedelta(days=120),
        first_week_monday=start - timedelta(days=start.weekday()),
        teaching_week_count=18,
    )


def _image_upload(name: str = "avatar.png", *, color: str = "blue", size=(8, 8)) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class TestPreferencesDefaults:
    def test_first_read_returns_documented_defaults(self, api_client) -> None:
        user = make_user("prefs-default@example.invalid")
        response = jwt_client_for(user).get("/api/v1/me/preferences/")

        assert response.status_code == 200
        assert response.data == {
            "font_size": "system",
            "local_reminder_enabled": True,
            "local_reminder_minutes": 15,
            "preferred_term": None,
            "push_enabled": True,
            "push_schedule_changed": True,
            "push_handoff": True,
            "push_inquiry": True,
            "push_plan": True,
            "updated_at": response.data["updated_at"],
        }
        assert UserPreferences.objects.filter(user=user).count() == 1

    def test_requires_authentication(self, api_client) -> None:
        assert api_client.get("/api/v1/me/preferences/").status_code == 401


class TestPreferencesPatch:
    def test_partial_patch_keeps_unsubmitted_values(self, api_client) -> None:
        user = make_user("prefs-partial@example.invalid")
        client = jwt_client_for(user)
        warm = client.patch(
            "/api/v1/me/preferences/",
            {
                "font_size": "large",
                "local_reminder_enabled": False,
                "local_reminder_minutes": 30,
                "push_enabled": False,
                "push_handoff": False,
            },
            format="json",
        )
        assert warm.status_code == 200, warm.data

        response = client.patch("/api/v1/me/preferences/", {"font_size": "extra_large"}, format="json")

        assert response.status_code == 200
        assert response.data["font_size"] == "extra_large"
        assert response.data["local_reminder_enabled"] is False
        assert response.data["local_reminder_minutes"] == 30
        assert response.data["push_enabled"] is False
        assert response.data["push_handoff"] is False
        assert response.data["push_schedule_changed"] is True

    @pytest.mark.parametrize(
        "payload",
        [
            {"font_size": "huge"},
            {"local_reminder_minutes": -1},
            {"local_reminder_minutes": 181},
            {"push_enabled": "not-a-boolean"},
        ],
    )
    def test_invalid_values_are_rejected(self, api_client, payload) -> None:
        client = jwt_client_for(make_user("prefs-invalid@example.invalid"))

        response = client.patch("/api/v1/me/preferences/", payload, format="json")

        assert response.status_code == 400
        assert response.data["code"] == "VALIDATION_ERROR"
        assert next(iter(payload)) in response.data["field_errors"]

    def test_unknown_field_is_rejected(self, api_client) -> None:
        client = jwt_client_for(make_user("prefs-unknown@example.invalid"))

        response = client.patch("/api/v1/me/preferences/", {"theme": "dark"}, format="json")

        assert response.status_code == 400
        assert "theme" in response.data["field_errors"]


class TestPreferencesTeacherAndTerm:
    def test_pending_account_can_set_preferences(self, api_client, school) -> None:
        term = _term(school)
        user = make_user("prefs-pending@example.invalid")
        TeacherClaim.objects.create(user=user, claimed_formal_name="待确认教师", claimed_teacher_no="P1001")

        response = jwt_client_for(user).patch(
            "/api/v1/me/preferences/",
            {"font_size": "large", "preferred_term_id": str(term.id)},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert response.data["font_size"] == "large"
        assert response.data["preferred_term"]["id"] == str(term.id)
        assert response.data["preferred_term"]["code"] == term.code

    def test_other_school_term_is_rejected(self, api_client, school) -> None:
        other_school = School.objects.create(code="OTHER-SCHOOL", name="其他虚构学校")
        other_term = _term(other_school, code="other-term")
        user = make_user("prefs-other-school@example.invalid")
        profile = make_teacher_profile(school, "T5101", email=user.email)
        profile.user = user
        profile.teacher_status = TeacherStatus.ACTIVE
        profile.save(update_fields=["user", "teacher_status", "updated_at"])

        response = jwt_client_for(user).patch(
            "/api/v1/me/preferences/",
            {"preferred_term_id": str(other_term.id)},
            format="json",
        )

        assert response.status_code == 400
        assert "preferred_term_id" in response.data["field_errors"]
        assert UserPreferences.objects.get(user=user).preferred_term_id is None

    def test_null_clears_preferred_term(self, api_client, school) -> None:
        term = _term(school)
        user = make_user("prefs-clear-term@example.invalid")
        profile = make_teacher_profile(school, "T5102", email=user.email)
        profile.user = user
        profile.teacher_status = TeacherStatus.ACTIVE
        profile.save(update_fields=["user", "teacher_status", "updated_at"])
        client = jwt_client_for(user)
        assert (
            client.patch(
                "/api/v1/me/preferences/",
                {"preferred_term_id": str(term.id)},
                format="json",
            ).status_code
            == 200
        )

        response = client.patch("/api/v1/me/preferences/", {"preferred_term_id": None}, format="json")

        assert response.status_code == 200
        assert response.data["preferred_term"] is None
        assert UserPreferences.objects.get(user=user).preferred_term_id is None

    def test_disabled_profile_cannot_change_preferences(self, api_client, school) -> None:
        user = make_user("prefs-disabled@example.invalid")
        profile = make_teacher_profile(school, "T5103", email=user.email)
        profile.user = user
        profile.teacher_status = TeacherStatus.DISABLED
        profile.save(update_fields=["user", "teacher_status", "updated_at"])

        response = jwt_client_for(user).patch("/api/v1/me/preferences/", {"font_size": "large"}, format="json")

        assert response.status_code == 403
        assert response.data["code"] == "TEACHER_DISABLED"


class TestPushPreferenceEntryPoint:
    def test_remote_push_requires_both_global_and_category_switch(self) -> None:
        from apps.notifications.models import NotificationType
        from apps.notifications.preferences import allows_remote_push

        user = make_user("prefs-push-mapping@example.invalid")
        preferences = UserPreferences.objects.create(user=user)

        assert allows_remote_push(user, NotificationType.SCHEDULE_CHANGED) is True
        preferences.push_handoff = False
        preferences.save(update_fields=["push_handoff", "updated_at"])
        assert allows_remote_push(user, NotificationType.HANDOFF_RECEIVED) is False
        assert allows_remote_push(user, NotificationType.SCHEDULE_CHANGED) is True

        preferences.push_enabled = False
        preferences.save(update_fields=["push_enabled", "updated_at"])
        assert allows_remote_push(user, NotificationType.SCHEDULE_CHANGED) is False
        # 未登记的类别默认站内，不做远程推送。
        assert allows_remote_push(user, "unknown_type") is False


class TestAvatar:
    def test_me_reports_no_avatar_initially(self, api_client) -> None:
        response = jwt_client_for(make_user("avatar-none@example.invalid")).get("/api/v1/me/")

        assert response.status_code == 200
        assert response.data["avatar_url"] is None

    def test_upload_read_replace_delete(self, api_client) -> None:
        user = make_user("avatar-lifecycle@example.invalid")
        client = jwt_client_for(user)

        uploaded = client.post("/api/v1/me/avatar/", {"avatar": _image_upload()}, format="multipart")
        assert uploaded.status_code == 200, uploaded.data
        first_url = uploaded.data["avatar_url"]
        assert first_url
        user.refresh_from_db()
        first_name = user.avatar.name
        assert first_name.startswith(f"avatars/{user.pk}/")

        read = client.get("/api/v1/me/avatar/")
        assert read.status_code == 200
        assert read["Content-Type"] == "image/png"
        content = b"".join(read.streaming_content)
        assert content.startswith(b"\x89PNG")

        me = client.get("/api/v1/me/")
        assert me.data["avatar_url"] == first_url

        replaced = client.post(
            "/api/v1/me/avatar/",
            {"avatar": _image_upload("second.png", color="red")},
            format="multipart",
        )
        assert replaced.status_code == 200
        user.refresh_from_db()
        assert user.avatar.name != first_name
        assert replaced.data["avatar_url"] != first_url
        assert not user.avatar.storage.exists(first_name)

        deleted = client.delete("/api/v1/me/avatar/")
        again = client.delete("/api/v1/me/avatar/")
        assert deleted.status_code == again.status_code == 204
        user.refresh_from_db()
        assert not user.avatar
        assert client.get("/api/v1/me/avatar/").status_code == 404

    def test_invalid_and_missing_upload_are_rejected(self, api_client) -> None:
        client = jwt_client_for(make_user("avatar-invalid@example.invalid"))

        missing = client.post("/api/v1/me/avatar/", {}, format="multipart")
        not_image = client.post(
            "/api/v1/me/avatar/",
            {"avatar": SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")},
            format="multipart",
        )

        assert missing.status_code == 400
        assert not_image.status_code == 400
        assert "avatar" in missing.data["field_errors"]
        assert not_image.data["code"] == "VALIDATION_ERROR"

    def test_oversize_and_unsupported_format_are_rejected(self, api_client) -> None:
        client = jwt_client_for(make_user("avatar-boundary@example.invalid"))
        png = _image_upload()
        oversized = SimpleUploadedFile(
            "large.png",
            png.read() + b"\0" * (2 * 1024 * 1024),
            content_type="image/png",
        )
        gif_buffer = BytesIO()
        Image.new("RGB", (8, 8), "green").save(gif_buffer, format="GIF")
        gif = SimpleUploadedFile("avatar.gif", gif_buffer.getvalue(), content_type="image/gif")

        too_large = client.post("/api/v1/me/avatar/", {"avatar": oversized}, format="multipart")
        unsupported = client.post("/api/v1/me/avatar/", {"avatar": gif}, format="multipart")

        assert too_large.status_code == 400
        assert "avatar" in too_large.data["field_errors"]
        assert unsupported.status_code == 400
        assert "avatar" in unsupported.data["field_errors"]

    def test_pending_account_can_upload_avatar(self, api_client) -> None:
        user = make_user("avatar-pending@example.invalid")
        TeacherClaim.objects.create(user=user, claimed_formal_name="待确认教师", claimed_teacher_no="P1002")

        response = jwt_client_for(user).post(
            "/api/v1/me/avatar/",
            {"avatar": _image_upload()},
            format="multipart",
        )

        assert response.status_code == 200
        assert response.data["avatar_url"]

    def test_uploads_are_per_user_and_not_public(self, api_client) -> None:
        first = make_user("avatar-owner-a@example.invalid")
        second = make_user("avatar-owner-b@example.invalid")
        first_client = jwt_client_for(first)
        second_client = jwt_client_for(second)
        assert (
            first_client.post("/api/v1/me/avatar/", {"avatar": _image_upload()}, format="multipart").status_code == 200
        )

        assert second_client.get("/api/v1/me/avatar/").status_code == 404
        first.refresh_from_db()
        assert first.avatar.name.startswith(f"avatars/{first.pk}/")
        # The raw media path must not be routed: MEDIA_ROOT also holds private
        # import source files.
        assert first_client.get(f"/media/{first.avatar.name}").status_code == 404
