"""Unified formal session-change entry point for B31 approvals.

Every teacher/Admin reschedule must pass through
:func:`apply_session_change`.  The function is the only writer that updates a
session's schedule fields and increments both the session and term versions, so
the following cannot drift into separate implementations:

* project lock order ``SchoolSyncState -> Term -> RescheduleRequest (approval
  only) -> ClassSession``;
* a fresh target evaluation under those locks;
* the before/after :class:`SessionChange` snapshot;
* ``record_changes`` in the same transaction;
* in-app notifications for every actual session teacher and the applicant.

B12 import writes live in :mod:`apps.scheduling.import_changes` and share the
same lock order, model invariants and ChangeLog conventions, but apply a whole
batch in bulk rather than one session at a time.

A request is merely an intent and reserves nothing.  Approval is therefore a
new, authoritative check rather than trusting the request's stored summary.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone as django_timezone

from apps.academics.models import Term
from apps.classrooms.models import Classroom
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import (
    ChangeEntityType,
    ClassSession,
    RescheduleRequest,
    SchoolSyncState,
    SessionChange,
    SessionStatus,
    SessionTeacher,
)
from apps.scheduling.reschedule_options import (
    DATA_UNVERIFIED,
    ELIGIBILITY_CONFLICT,
    ELIGIBILITY_UNVERIFIED,
    TARGET_NOT_ALLOWED,
    TargetEvaluation,
    evaluate_target,
)


class ScheduleChangeError(Exception):
    """A formal move was rejected without changing any row.

    ``evaluation`` is attached for conflict/unverified errors so the API and
    Admin can show the same conflict details that blocked the write.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int,
        field_errors: dict[str, list[str]] | None = None,
        evaluation: TargetEvaluation | None = None,
        current_version: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.field_errors = field_errors or {}
        self.evaluation = evaluation
        self.current_version = current_version

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "detail": self.detail,
            "field_errors": self.field_errors,
        }
        if self.evaluation is not None:
            payload["evaluation"] = self.evaluation.as_dict()
        if self.current_version is not None:
            payload["current_version"] = self.current_version
        return payload


def _session_snapshot(session: ClassSession) -> dict[str, object]:
    tzinfo = session.offering.term.tzinfo
    local_start = session.start_at.astimezone(tzinfo)
    return {
        "date": local_start.date().isoformat(),
        "start_period": session.start_period,
        "end_period": session.end_period,
        "start_at": local_start.isoformat(),
        "end_at": session.end_at.astimezone(tzinfo).isoformat(),
        "classroom_id": str(session.classroom_id),
        "classroom_code": session.classroom.classroom_code,
        "version": session.version,
    }


def _rejection_for(evaluation: TargetEvaluation, *, current_version: int) -> ScheduleChangeError:
    if evaluation.eligibility == ELIGIBILITY_CONFLICT:
        return ScheduleChangeError(
            "SCHEDULE_CONFLICT",
            evaluation.detail or "目标时段存在已知冲突，不能普通生效。",
            status_code=409,
            field_errors=evaluation.field_errors,
            evaluation=evaluation,
            current_version=current_version,
        )
    if evaluation.eligibility == ELIGIBILITY_UNVERIFIED:
        return ScheduleChangeError(
            DATA_UNVERIFIED,
            evaluation.detail or "目标数据不完整，不能普通生效。",
            status_code=409,
            field_errors=evaluation.field_errors,
            evaluation=evaluation,
            current_version=current_version,
        )
    return ScheduleChangeError(
        evaluation.code or TARGET_NOT_ALLOWED,
        evaluation.detail or "该目标不能作为调课结果。",
        status_code=409,
        field_errors=evaluation.field_errors,
        evaluation=evaluation,
        current_version=current_version,
    )


def _format_change_message(
    before: dict[str, object],
    after: dict[str, object],
    *,
    forced: bool,
) -> str:
    before_date = str(before["date"])
    after_date = str(after["date"])
    before_start = datetime.fromisoformat(str(before["start_at"]))
    before_end = datetime.fromisoformat(str(before["end_at"]))
    after_start = datetime.fromisoformat(str(after["start_at"]))
    after_end = datetime.fromisoformat(str(after["end_at"]))
    before_time = f"{before_start:%H:%M}–{before_end:%H:%M}"
    after_time = f"{after_start:%H:%M}–{after_end:%H:%M}"
    before_periods = f"第 {before['start_period']}–{before['end_period']} 节"
    after_periods = f"第 {after['start_period']}–{after['end_period']} 节"
    text = (
        f"{before_date} {before_time}（{before_periods}） → "
        f"{after_date} {after_time}（{after_periods}），"
        f"教室：{before['classroom_code']} → {after['classroom_code']}。"
    )
    if forced:
        text += "本次为学校强制安排，请按学校审批结果执行。"
    return text


def apply_session_change(
    session_id: UUID,
    *,
    target_date,
    start_period: int,
    end_period: int,
    classroom_id: UUID,
    actor,
    reason: str,
    approval_ref: str,
    forced: bool = False,
    request: RescheduleRequest | None = None,
    now: datetime,
) -> SessionChange:
    """Move one scheduled session and write all derivatives atomically.

    Locked reads happen before the target evaluation so the check sees any
    change committed by another approval just before this transaction acquired
    the session row.  ``forced`` only bypasses known conflicts or incomplete
    data; structural impossibilities (wrong periods, inactive room, capacity,
    equipment, past date, calendar exception) remain rejected.  A forced move
    must carry the school's ``approval_ref``.
    """
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")
    approval_ref = (approval_ref or "").strip()
    if forced and not approval_ref:
        raise ScheduleChangeError(
            "APPROVAL_REF_REQUIRED",
            "登记学校强制安排时必须填写学校审批编号或依据。",
            status_code=400,
            field_errors={"approval_ref": ["登记学校强制安排时必须填写学校审批编号或依据。"]},
        )

    # This initial read is only used to learn the lock targets.  Every value
    # that decides the change is re-read under the locks below.
    lock_target = (
        ClassSession.objects.select_related("offering__term")
        .only(
            "id",
            "offering_id",
            "offering__term_id",
            "offering__term__school_id",
        )
        .get(pk=session_id)
    )

    with transaction.atomic():
        # Project-wide lock order: SchoolSyncState first, then the Term row,
        # then (when approval starts from a request) the RescheduleRequest row,
        # and finally the ClassSession row.  This function itself owns the
        # State -> Term -> Session locks; the approval service takes the request
        # lock between Term and calling here.  Generation and every importer/
        # reschedule writer must use this same order to avoid deadlocks
        # (domain-design 2.1/4.4).  Do not reorder these statements.
        SchoolSyncState.objects.select_for_update().get_or_create(
            school_id=lock_target.offering.term.school_id,
        )
        locked_term = Term.objects.select_for_update().get(pk=lock_target.offering.term_id)
        session = (
            ClassSession.objects.select_for_update()
            .select_related("offering__term", "offering__course", "classroom")
            .get(pk=session_id)
        )

        if session.status != SessionStatus.SCHEDULED:
            raise ScheduleChangeError(
                "SESSION_CHANGED",
                "课次已不是待上课状态，不能调课。申请仍保留，请重新核对。",
                status_code=409,
                current_version=session.version,
            )

        tzinfo = locked_term.tzinfo
        current_local_start = session.start_at.astimezone(tzinfo)
        if (
            target_date == current_local_start.date()
            and start_period == session.start_period
            and end_period == session.end_period
            and classroom_id == session.classroom_id
        ):
            raise ScheduleChangeError(
                "TARGET_UNCHANGED",
                "目标与当前课次完全相同，无需调课。",
                status_code=409,
                current_version=session.version,
            )

        classroom = Classroom.objects.get(pk=classroom_id)
        evaluation = evaluate_target(
            session,
            start_period=start_period,
            end_period=end_period,
            day=target_date,
            classroom=classroom,
            now=now,
        )

        if not evaluation.is_allowed:
            # Even a forced arrangement cannot invent a valid period run, move
            # into the past, or violate room capacity/equipment.
            raise _rejection_for(evaluation, current_version=session.version)
        if not forced and not evaluation.is_available:
            raise _rejection_for(evaluation, current_version=session.version)

        before = _session_snapshot(session)
        session.start_at = evaluation.start_at
        session.end_at = evaluation.end_at
        session.start_period = start_period
        session.end_period = end_period
        session.classroom = classroom
        session.version += 1
        session.save(
            update_fields=[
                "start_at",
                "end_at",
                "start_period",
                "end_period",
                "classroom",
                "version",
                "updated_at",
            ]
        )
        after = _session_snapshot(session)
        if forced:
            after["risk"] = {
                "forced_override": True,
                "approval_ref": approval_ref,
                "evaluation": evaluation.as_dict(),
                "notice": "学校强制安排：评估结果可能包含冲突或数据缺口。",
            }

        locked_term.schedule_version += 1
        locked_term.save(update_fields=["schedule_version", "updated_at"])

        change = SessionChange.objects.create(
            session=session,
            request=request,
            session_version=session.version,
            before_json=before,
            after_json=after,
            actor=actor,
            reason=reason,
            approval_ref=approval_ref,
            forced_override=forced,
        )

        record_changes(
            locked_term.school_id,
            [
                ChangeEntry(
                    entity_type=ChangeEntityType.SESSION,
                    entity_id=session.id,
                    version=session.version,
                    offering_id=session.offering_id,
                ),
                ChangeEntry(
                    entity_type=ChangeEntityType.TERM,
                    entity_id=locked_term.id,
                    version=locked_term.schedule_version,
                ),
            ],
        )

        # Local import: teaching.handoffs imports scheduling models/selectors.
        # A module-level import here would create a scheduling <-> teaching
        # import cycle at Django startup.
        from apps.teaching.handoffs import notify_pending_handoffs_for_moved_session

        notify_pending_handoffs_for_moved_session(session, change)

        body = _format_change_message(before, after, forced=forced)
        teachers = list(
            SessionTeacher.objects.filter(session=session).select_related("teacher").order_by("teacher__teacher_no")
        )
        for link in teachers:
            notify(
                link.teacher,
                type=NotificationType.SCHEDULE_CHANGED,
                object_type="session",
                object_id=session.id,
                title="课表调整已生效",
                body=body,
                event_key=f"session_change:{change.id}",
            )

        if request is not None:
            result_body = (
                f"你申请的课次已调整：{body}"
                if not forced
                else f"学校已登记强制安排：{body} 审批依据：{approval_ref}。"
            )
            notify(
                request.applicant,
                type=NotificationType.RESCHEDULE_RESULT,
                object_type="reschedule_request",
                object_id=request.id,
                title="调课申请已批准",
                body=result_body,
                event_key=f"reschedule_request:{request.id}:approved",
            )

    return change
