"""Teacher contact visibility (B32).

Phone and WeChat are independent choices.  Visibility is computed for the
current viewer only; no endpoint ever returns a number that should be hidden.
The same resolver backs both ``GET teachers/{id}/contact/`` and the
``has_visible_contact`` flag in classroom history, so the two cannot disagree.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import QuerySet

from apps.academics.models import ContactVisibility, TeacherProfile, TeacherStatus
from apps.scheduling.models import SessionTeacher
from apps.scheduling.selectors import active_assignments_for_teacher, active_assignments_for_teachers

CONTACT_STATUS_VISIBLE = "visible"
CONTACT_STATUS_NOT_PROVIDED = "not_provided"
CONTACT_STATUS_HIDDEN = "hidden"
CONTACT_STATUS_CHOICES = (
    (CONTACT_STATUS_VISIBLE, "可见"),
    (CONTACT_STATUS_NOT_PROVIDED, "未提供"),
    (CONTACT_STATUS_HIDDEN, "暂不可见"),
)

_CONTACT_FIELDS = (
    ("phone", "phone_visibility"),
    ("wechat", "wechat_visibility"),
)


def teacher_name(teacher: TeacherProfile) -> str:
    return teacher.display_name or teacher.formal_name


@dataclass(frozen=True)
class ContactField:
    value: str | None
    status: str


class ContactVisibilityResolver:
    """Batch visibility calculator with a fixed number of queries.

    ``prime`` loads the target teachers' active offering ids, shared session
    ids and inactive accounts in three queries total.  Per-teacher decisions
    after that are pure in-memory set intersections.
    """

    def __init__(self, viewer: TeacherProfile) -> None:
        self.viewer = viewer
        self.viewer_offering_ids = set(active_assignments_for_teacher(viewer).values_list("offering_id", flat=True))
        self.viewer_session_ids = set(
            SessionTeacher.objects.filter(teacher=viewer).values_list("session_id", flat=True)
        )
        self._target_offering_ids: dict = {}
        self._target_session_ids: dict = {}
        self._inactive_user_ids: set = set()
        self._primed: set = set()

    def prime(self, targets: Iterable[TeacherProfile]) -> None:
        """Load shared-course facts for every target teacher in one batch."""
        unique: dict = {}
        for target in targets:
            unique[target.id] = target
        new_ids = {target_id for target_id in unique if target_id not in self._primed}
        if not new_ids:
            return

        for teacher_id, offering_id in active_assignments_for_teachers(new_ids).values_list(
            "teacher_id", "offering_id"
        ):
            self._target_offering_ids.setdefault(teacher_id, set()).add(offering_id)

        for teacher_id, session_id in SessionTeacher.objects.filter(
            teacher_id__in=new_ids,
            session_id__in=self.viewer_session_ids,
        ).values_list("teacher_id", "session_id"):
            self._target_session_ids.setdefault(teacher_id, set()).add(session_id)

        user_ids = {target.user_id for target in unique.values() if target.user_id}
        if user_ids:
            self._inactive_user_ids.update(
                get_user_model().objects.filter(pk__in=user_ids, is_active=False).values_list("pk", flat=True)
            )
        self._primed.update(new_ids)

    def _target_is_active(self, target: TeacherProfile) -> bool:
        if not target.is_active or target.teacher_status == TeacherStatus.DISABLED:
            return False
        if target.user_id and target.user_id in self._inactive_user_ids:
            return False
        return True

    def _same_course(self, target: TeacherProfile) -> bool:
        if self.viewer_offering_ids & self._target_offering_ids.get(target.id, set()):
            return True
        return bool(self.viewer_session_ids & self._target_session_ids.get(target.id, set()))

    def _can_view(self, target: TeacherProfile, visibility: str) -> bool:
        if target.id == self.viewer.id:
            return True
        if not self._target_is_active(target):
            return False
        if target.school_id != self.viewer.school_id:
            return False
        if visibility == ContactVisibility.PRIVATE:
            return False
        if visibility == ContactVisibility.SCHOOL_TEACHERS:
            return True
        if visibility == ContactVisibility.COURSE_TEACHERS:
            return self._same_course(target)
        return False

    def field(self, target: TeacherProfile, *, value_attr: str, visibility_attr: str) -> ContactField:
        value = (getattr(target, value_attr) or "").strip()
        if not value:
            return ContactField(value=None, status=CONTACT_STATUS_NOT_PROVIDED)
        if self._can_view(target, getattr(target, visibility_attr)):
            return ContactField(value=value, status=CONTACT_STATUS_VISIBLE)
        return ContactField(value=None, status=CONTACT_STATUS_HIDDEN)

    def contact_fields(self, target: TeacherProfile) -> dict[str, str | None]:
        payload: dict[str, str | None] = {}
        for value_attr, visibility_attr in _CONTACT_FIELDS:
            field = self.field(target, value_attr=value_attr, visibility_attr=visibility_attr)
            payload[value_attr] = field.value
            payload[f"{value_attr}_status"] = field.status
        return payload

    def has_visible_contact(self, target: TeacherProfile) -> bool:
        # Deliberately reuse the exact same field calculation as the contact
        # endpoint; a separate phone/WeChat rule would drift from B32.
        fields = self.contact_fields(target)
        return any(fields[f"{value_attr}_status"] == CONTACT_STATUS_VISIBLE for value_attr, _ in _CONTACT_FIELDS)


def visible_contact(viewer: TeacherProfile, target: TeacherProfile) -> dict[str, str | None]:
    """Return visible phone/WeChat values plus their explicit statuses."""
    resolver = ContactVisibilityResolver(viewer)
    resolver.prime([target])
    return resolver.contact_fields(target)


def visible_contact_payload(viewer: TeacherProfile, target: TeacherProfile) -> dict:
    fields = visible_contact(viewer, target)
    return {
        "teacher": {
            "id": str(target.id),
            "name": teacher_name(target),
            "department": target.department,
        },
        **fields,
    }


def teacher_contact_queryset(school_id) -> QuerySet[TeacherProfile]:
    """Targets a viewer may ask about; another school is indistinguishable from 404."""
    return TeacherProfile.objects.filter(school_id=school_id)
