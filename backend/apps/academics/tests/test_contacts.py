"""Contact visibility table, status distinction and classroom flag (B32)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from apps.academics.models import ContactVisibility, School, TeacherProfile, TeachingAssignment
from apps.scheduling.selectors import current_school_date
from apps.scheduling.tests import factories
from apps.teaching.tests.helpers import auth_client, link_user

pytestmark = pytest.mark.django_db

PHONE = "13800000000"
WECHAT = "demo-wechat"


def _contact(client, target):
    return client.get(f"/api/v1/teachers/{target.id}/contact/")


def _add_third_teacher(demo):
    teacher3 = factories.make_teacher(demo.school, "0003", "示例教师丙")
    TeachingAssignment.objects.create(
        offering=demo.offerings["OFF-DATA-2026A"],
        teacher=teacher3,
        role="teacher",
        effective_from=demo.term.start_date,
        effective_until=demo.term.end_date + timedelta(days=1),
    )
    return teacher3


def _link_all(demo, teacher3):
    return {
        "0001": link_user(demo.teachers["0001"], "contact-1@example.invalid"),
        "0002": link_user(demo.teachers["0002"], "contact-2@example.invalid"),
        "0003": link_user(teacher3, "contact-3@example.invalid"),
    }


def test_visibility_matrix_for_self_same_course_and_other_teacher(demo_term):
    """Three visibility values × self/same-course/other-school-teacher viewers."""
    demo = demo_term
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    target = demo.teachers["0002"]
    target.phone = PHONE
    target.wechat = WECHAT
    clients = {key: auth_client(user) for key, user in users.items()}

    expected = {
        (ContactVisibility.PRIVATE, "self"): ("visible", "visible"),
        (ContactVisibility.PRIVATE, "same_course"): ("hidden", "hidden"),
        (ContactVisibility.PRIVATE, "other"): ("hidden", "hidden"),
        (ContactVisibility.COURSE_TEACHERS, "self"): ("visible", "visible"),
        (ContactVisibility.COURSE_TEACHERS, "same_course"): ("visible", "visible"),
        (ContactVisibility.COURSE_TEACHERS, "other"): ("hidden", "hidden"),
        (ContactVisibility.SCHOOL_TEACHERS, "self"): ("visible", "visible"),
        (ContactVisibility.SCHOOL_TEACHERS, "same_course"): ("visible", "visible"),
        (ContactVisibility.SCHOOL_TEACHERS, "other"): ("visible", "visible"),
    }
    viewer_for = {
        "self": ("0002", target),
        "same_course": ("0001", demo.teachers["0001"]),
        "other": ("0003", teacher3),
    }

    for visibility in ContactVisibility:
        target.phone_visibility = visibility
        target.wechat_visibility = visibility
        target.save(update_fields=["phone", "wechat", "phone_visibility", "wechat_visibility", "updated_at"])
        for viewer_key, (_, viewer) in viewer_for.items():
            # Use the target's own client for the self row; other rows use the viewer.
            client_key = "0002" if viewer_key == "self" else viewer_for[viewer_key][0]
            response = _contact(clients[client_key], target)
            assert response.status_code == 200
            payload = response.json()
            assert (payload["phone_status"], payload["wechat_status"]) == expected[(visibility, viewer_key)]
            if payload["phone_status"] == "visible":
                assert payload["phone"] == PHONE
                assert payload["wechat"] == WECHAT
            else:
                assert payload["phone"] is None
                assert payload["wechat"] is None


def test_private_status_hidden_and_blank_status_not_provided(demo_term):
    demo = demo_term
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    target = demo.teachers["0002"]
    target.phone = PHONE
    target.phone_visibility = ContactVisibility.PRIVATE
    target.wechat = ""
    target.wechat_visibility = ContactVisibility.SCHOOL_TEACHERS
    target.save(update_fields=["phone", "phone_visibility", "wechat", "wechat_visibility", "updated_at"])

    response = _contact(auth_client(users["0001"]), target)
    payload = response.json()
    assert payload["phone"] is None
    assert payload["phone_status"] == "hidden"
    assert payload["wechat"] is None
    assert payload["wechat_status"] == "not_provided"

    target.phone = ""
    target.save(update_fields=["phone", "updated_at"])
    payload = _contact(auth_client(users["0001"]), target).json()
    assert payload["phone_status"] == "not_provided"


def test_same_teaching_class_different_course_is_not_course_teachers(demo_term):
    demo = demo_term
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    target = teacher3
    target.phone = PHONE
    target.phone_visibility = ContactVisibility.COURSE_TEACHERS
    target.save(update_fields=["phone", "phone_visibility", "updated_at"])

    # Both teachers are active in TC-2026-A, but teacher2 is on the crime
    # offering and teacher3 is only on the data offering.
    payload = _contact(auth_client(users["0002"]), target).json()
    assert payload["phone"] is None
    assert payload["phone_status"] == "hidden"


def test_ended_assignment_is_not_course_teachers(demo_term):
    demo = demo_term
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    target = demo.teachers["0002"]
    target.phone = PHONE
    target.phone_visibility = ContactVisibility.COURSE_TEACHERS
    target.save(update_fields=["phone", "phone_visibility", "updated_at"])
    TeachingAssignment.objects.filter(teacher=target).update(effective_until=current_school_date())

    payload = _contact(auth_client(users["0001"]), target).json()
    assert payload["phone_status"] == "hidden"


def test_disabled_profile_and_disabled_account_hide_contacts(demo_term):
    demo = demo_term
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    target = demo.teachers["0002"]
    target.phone = PHONE
    target.phone_visibility = ContactVisibility.SCHOOL_TEACHERS
    target.save(update_fields=["phone", "phone_visibility", "updated_at"])
    client = auth_client(users["0001"])

    target.is_active = False
    target.save(update_fields=["is_active", "updated_at"])
    payload = _contact(client, target).json()
    assert payload["phone"] is None
    assert payload["phone_status"] == "hidden"

    target.is_active = True
    target.save(update_fields=["is_active", "updated_at"])
    target.user.is_active = False
    target.user.save(update_fields=["is_active"])
    payload = _contact(client, target).json()
    assert payload["phone"] is None
    assert payload["phone_status"] == "hidden"


def test_usage_has_visible_contact_matches_contact_endpoint(generated_demo):
    demo = generated_demo
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    target = demo.teachers["0002"]
    target.phone = PHONE
    target.wechat = ""
    target.phone_visibility = ContactVisibility.COURSE_TEACHERS
    target.save(update_fields=["phone", "wechat", "phone_visibility", "updated_at"])

    client = auth_client(users["0001"])
    usage = client.get(f"/api/v1/classrooms/{demo.classrooms['A301'].id}/usage/?date=2026-10-26")
    assert usage.status_code == 200
    session = next(entry for entry in usage.json()["entries"] if entry["kind"] == "session")
    teacher_row = next(item for item in session["teachers"] if item["id"] == str(target.id))
    assert teacher_row["has_visible_contact"] is True
    assert _contact(client, target).json()["phone_status"] == "visible"

    target.phone_visibility = ContactVisibility.PRIVATE
    target.save(update_fields=["phone_visibility", "updated_at"])
    usage = client.get(f"/api/v1/classrooms/{demo.classrooms['A301'].id}/usage/?date=2026-10-26")
    session = next(entry for entry in usage.json()["entries"] if entry["kind"] == "session")
    teacher_row = next(item for item in session["teachers"] if item["id"] == str(target.id))
    assert teacher_row["has_visible_contact"] is False
    assert _contact(client, target).json()["phone_status"] == "hidden"


def test_other_school_teacher_is_404(demo_term):
    demo = demo_term
    teacher3 = _add_third_teacher(demo)
    users = _link_all(demo, teacher3)
    other_school = School.objects.create(code="OTHER-SCHOOL", name="其他虚构学校")
    other_target = TeacherProfile.objects.create(
        school=other_school,
        teacher_no="9001",
        formal_name="其他学校教师",
    )
    response = _contact(auth_client(users["0001"]), other_target)
    assert response.status_code == 404


def test_contact_not_linked_and_disabled_viewer_errors(demo_term):
    """Existing 403 codes remain stable for the new endpoint."""
    demo = demo_term
    from django.contrib.auth import get_user_model

    unlinked = get_user_model().objects.create_user(email="unlinked-contact@example.invalid", password="test-pass-123")
    response = _contact(auth_client(unlinked), demo.teachers["0001"])
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_NOT_LINKED"

    user = link_user(demo.teachers["0001"], "disabled-contact-viewer@example.invalid")
    demo.teachers["0001"].is_active = False
    demo.teachers["0001"].save(update_fields=["is_active", "updated_at"])
    response = _contact(auth_client(user), demo.teachers["0002"])
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_DISABLED"
