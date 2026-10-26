"""Reschedule request state machine and approval services (B31).

Views and Django Admin call this module; neither writes request rows directly.
A request is an intent only.  Submission re-evaluates the target, approval calls
the single formal write entry point in ``scheduling.changes`` and therefore
re-evaluates again under the project lock order.  Withdrawal and rejection never
touch the timetable.

Errors deliberately carry the same ``evaluation`` payload as the B30 query so a
teacher or academic staff member sees why a target cannot be submitted or
approved.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone as django_timezone

from apps.academics.models import TeacherProfile, Term
from apps.classrooms.models import Classroom
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.scheduling.changes import ScheduleChangeError, apply_session_change
from apps.scheduling.models import (
    ClassSession,
    RescheduleRequest,
    RescheduleStatus,
    SchoolSyncState,
    SessionStatus,
)
from apps.scheduling.reschedule_options import (
    ELIGIBILITY_CONFLICT,
    ELIGIBILITY_UNVERIFIED,
    RESCHEDULE_CONFLICT,
    TARGET_NOT_ALLOWED,
    RescheduleOptionsError,
    TargetEvaluation,
    evaluate_target,
)
from apps.scheduling.selectors import (
    can_reschedule_session,
    visible_reschedule_requests,
)


class RescheduleRequestError(Exception):
    """A request transition failed without leaving a forbidden state."""

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


def _check_summary(evaluation: TargetEvaluation, now: datetime) -> dict[str, object]:
    payload = evaluation.as_dict()
    payload["checked_at"] = now.isoformat()
    return payload


def _evaluate(
    session: ClassSession,
    *,
    day,
    start_period: int,
    end_period: int,
    classroom: Classroom,
    now: datetime,
) -> TargetEvaluation:
    try:
        return evaluate_target(
            session,
            start_period=start_period,
            end_period=end_period,
            day=day,
            classroom=classroom,
            now=now,
        )
    except RescheduleOptionsError as exc:
        raise RescheduleRequestError(
            exc.code,
            exc.detail,
            status_code=exc.status_code,
            field_errors=exc.field_errors,
            current_version=session.version,
        ) from exc


def _record_failed_evaluation(
    request: RescheduleRequest,
    evaluation: TargetEvaluation,
    now: datetime,
) -> None:
    request.check_summary = _check_summary(evaluation, now)
    request.checked_schedule_version = request.session.offering.term.schedule_version
    if evaluation.start_at is not None and evaluation.end_at is not None:
        request.target_start_at = evaluation.start_at
        request.target_end_at = evaluation.end_at
    request.save(
        update_fields=[
            "check_summary",
            "checked_schedule_version",
            "target_start_at",
            "target_end_at",
            "updated_at",
        ]
    )


def _error_for_evaluation(
    evaluation: TargetEvaluation,
    *,
    current_version: int,
) -> RescheduleRequestError:
    if evaluation.eligibility == ELIGIBILITY_CONFLICT:
        return RescheduleRequestError(
            RESCHEDULE_CONFLICT,
            evaluation.detail or "目标时段存在已知冲突，不能普通提交。",
            status_code=409,
            field_errors=evaluation.field_errors,
            evaluation=evaluation,
            current_version=current_version,
        )
    return RescheduleRequestError(
        evaluation.code or TARGET_NOT_ALLOWED,
        evaluation.detail or "该目标不能作为调课申请。",
        status_code=409,
        field_errors=evaluation.field_errors,
        evaluation=evaluation,
        current_version=current_version,
    )


def _locked_visible_request(
    *,
    teacher: TeacherProfile,
    request_id: UUID,
) -> RescheduleRequest:
    """Lock a request that is visible to ``teacher`` or return a 404 error."""
    related = visible_reschedule_requests(teacher, scope="related").filter(pk=request_id).exists()
    own = RescheduleRequest.objects.filter(pk=request_id, applicant=teacher).exists()
    if not (related or own):
        raise RescheduleRequestError(
            "NOT_FOUND",
            "调课申请不存在。",
            status_code=404,
        )
    return (
        # Submit/withdraw only change the request row.  Locking related
        # session/term rows here would introduce an unneeded inverse lock order
        # for future sync writers, so restrict the row lock to the request.
        RescheduleRequest.objects.select_for_update(of=("self",))
        .select_related(
            "session__offering__term",
            "session__offering__course",
            "session__classroom",
            "target_classroom",
            "applicant",
        )
        .get(pk=request_id)
    )


def _pending_exists(*, session_id: UUID, applicant_id: UUID, exclude_id: UUID | None = None) -> bool:
    rows = RescheduleRequest.objects.filter(
        session_id=session_id,
        applicant_id=applicant_id,
        status=RescheduleStatus.SUBMITTED,
    )
    if exclude_id is not None:
        rows = rows.exclude(pk=exclude_id)
    return rows.exists()


def create_request(
    teacher: TeacherProfile,
    session: ClassSession,
    *,
    target_date,
    target_start_period: int,
    target_end_period: int,
    target_classroom: Classroom,
    reason: str,
    submit: bool,
    now: datetime,
) -> RescheduleRequest:
    """Create a draft/submitted request after a fresh target evaluation.

    A hard-invalid target is never persisted.  A known conflict may be stored
    as a draft but not submitted; unverified data may be submitted and is
    flagged ``needs_verification``.  No request reserves a classroom.
    """
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")
    if not reason.strip():
        raise RescheduleRequestError(
            "VALIDATION_ERROR",
            "请填写调课说明。",
            status_code=400,
            field_errors={"reason": ["请填写调课说明。"]},
        )
    if not can_reschedule_session(teacher, session):
        # Do not reveal whether a session exists outside the teacher's scope.
        raise RescheduleRequestError("NOT_FOUND", "课次不存在。", status_code=404)
    if session.status != SessionStatus.SCHEDULED:
        raise RescheduleRequestError(
            "SESSION_NOT_RESCHEDULABLE",
            "只有待上课的课次可以申请调课。",
            status_code=409,
            current_version=session.version,
        )

    if submit and _pending_exists(session_id=session.id, applicant_id=teacher.id):
        raise RescheduleRequestError(
            "REQUEST_ALREADY_PENDING",
            "该课次已有待审批的调课申请，不能重复提交。",
            status_code=409,
            current_version=session.version,
        )

    evaluation = _evaluate(
        session,
        day=target_date,
        start_period=target_start_period,
        end_period=target_end_period,
        classroom=target_classroom,
        now=now,
    )
    if not evaluation.is_allowed:
        raise _error_for_evaluation(evaluation, current_version=session.version)
    if submit and evaluation.eligibility == ELIGIBILITY_CONFLICT:
        # Conflict targets may be saved as drafts only; this request is not
        # saved at all, matching the documented 409 contract.
        raise _error_for_evaluation(evaluation, current_version=session.version)
    if evaluation.start_at is None or evaluation.end_at is None:
        raise _error_for_evaluation(evaluation, current_version=session.version)

    status = RescheduleStatus.SUBMITTED if submit else RescheduleStatus.DRAFT
    values = {
        "session": session,
        "applicant": teacher,
        "target_date": target_date,
        "target_start_period": target_start_period,
        "target_end_period": target_end_period,
        "target_start_at": evaluation.start_at,
        "target_end_at": evaluation.end_at,
        "target_classroom": target_classroom,
        "reason": reason,
        "status": status,
        "base_session_version": session.version,
        "checked_schedule_version": session.offering.term.schedule_version,
        "check_summary": _check_summary(evaluation, now),
        "needs_verification": submit and evaluation.eligibility == ELIGIBILITY_UNVERIFIED,
    }
    try:
        with transaction.atomic():
            return RescheduleRequest.objects.create(**values)
    except IntegrityError as exc:
        if submit:
            raise RescheduleRequestError(
                "REQUEST_ALREADY_PENDING",
                "该课次已有待审批的调课申请，不能重复提交。",
                status_code=409,
                current_version=session.version,
            ) from exc
        raise


def submit_request(
    request: RescheduleRequest,
    teacher: TeacherProfile,
    now: datetime,
) -> RescheduleRequest:
    """Move a draft to submitted after re-evaluating its target."""
    if django_timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime")

    error: RescheduleRequestError | None = None
    with transaction.atomic():
        locked = _locked_visible_request(teacher=teacher, request_id=request.id)
        if locked.applicant_id != teacher.id:
            raise RescheduleRequestError(
                "NOT_REQUEST_APPLICANT",
                "只有申请人本人可以提交该调课申请。",
                status_code=403,
            )
        if locked.status != RescheduleStatus.DRAFT:
            raise RescheduleRequestError(
                "REQUEST_NOT_SUBMITTABLE",
                "只有草稿状态的调课申请可以提交。",
                status_code=409,
            )
        if _pending_exists(
            session_id=locked.session_id,
            applicant_id=teacher.id,
            exclude_id=locked.id,
        ):
            raise RescheduleRequestError(
                "REQUEST_ALREADY_PENDING",
                "该课次已有待审批的调课申请，不能重复提交。",
                status_code=409,
                current_version=locked.session.version,
            )

        evaluation = _evaluate(
            locked.session,
            day=locked.target_date,
            start_period=locked.target_start_period,
            end_period=locked.target_end_period,
            classroom=locked.target_classroom,
            now=now,
        )
        locked.check_summary = _check_summary(evaluation, now)
        locked.checked_schedule_version = locked.session.offering.term.schedule_version
        if evaluation.start_at is not None and evaluation.end_at is not None:
            locked.target_start_at = evaluation.start_at
            locked.target_end_at = evaluation.end_at

        if not evaluation.is_allowed or evaluation.eligibility == ELIGIBILITY_CONFLICT:
            _record_failed_evaluation(locked, evaluation, now)
            error = _error_for_evaluation(evaluation, current_version=locked.session.version)
        else:
            try:
                with transaction.atomic():
                    locked.status = RescheduleStatus.SUBMITTED
                    locked.base_session_version = locked.session.version
                    locked.needs_verification = evaluation.eligibility == ELIGIBILITY_UNVERIFIED
                    locked.save(
                        update_fields=[
                            "check_summary",
                            "checked_schedule_version",
                            "target_start_at",
                            "target_end_at",
                            "status",
                            "base_session_version",
                            "needs_verification",
                            "updated_at",
                        ]
                    )
            except IntegrityError:
                # Another draft for this session/applicant won the race.  The
                # partial unique constraint rolled the save back; keep this
                # draft and commit the latest evaluation as a diagnostic.
                locked.status = RescheduleStatus.DRAFT
                _record_failed_evaluation(locked, evaluation, now)
                error = RescheduleRequestError(
                    "REQUEST_ALREADY_PENDING",
                    "该课次已有待审批的调课申请，不能重复提交。",
                    status_code=409,
                    current_version=locked.session.version,
                )
    if error is not None:
        # Commit the latest evaluation and keep the request in draft, then let
        # the API/Admin show the reason.
        raise error
    return locked


def withdraw_request(request: RescheduleRequest, teacher: TeacherProfile) -> RescheduleRequest:
    """Withdraw a draft/submitted request; only the applicant may do so."""
    with transaction.atomic():
        locked = _locked_visible_request(teacher=teacher, request_id=request.id)
        if locked.applicant_id != teacher.id:
            raise RescheduleRequestError(
                "NOT_REQUEST_APPLICANT",
                "只有申请人本人可以撤回调课申请。",
                status_code=403,
            )
        if locked.status not in {RescheduleStatus.DRAFT, RescheduleStatus.SUBMITTED}:
            raise RescheduleRequestError(
                "REQUEST_NOT_WITHDRAWABLE",
                "只有草稿或待审批的调课申请可以撤回。",
                status_code=409,
            )
        locked.status = RescheduleStatus.WITHDRAWN
        locked.save(update_fields=["status", "updated_at"])
        return locked


def _pending_locked_request(request_id: UUID) -> RescheduleRequest:
    """Lock only the request row, never its session or term relations.

    Approval callers must already hold SchoolSyncState and Term in the project
    order.  Rejection calls this helper alone because it does not read or write
    schedule rows, so it has no reason to wait for the earlier locks.
    """
    locked = (
        RescheduleRequest.objects.select_for_update(of=("self",))
        .select_related(
            "session__offering__term",
            "session__offering__course",
            "session__classroom",
            "target_classroom",
            "applicant",
        )
        .get(pk=request_id)
    )
    if locked.status != RescheduleStatus.SUBMITTED:
        raise RescheduleRequestError(
            "REQUEST_NOT_PENDING",
            "只有待审批的调课申请可以审批。",
            status_code=409,
            current_version=locked.session.version,
        )
    return locked


def _approve(
    request: RescheduleRequest,
    *,
    actor,
    decision_ref: str,
    note: str,
    now: datetime,
    forced: bool,
) -> RescheduleRequest:
    """Shared normal/forced approval transaction.

    On a target recheck failure the request stays ``submitted`` and the newest
    evaluation is committed.  The error is raised only after that commit so a
    caller cannot accidentally roll the diagnostic update back.
    """
    # This is a non-locking read used only to identify the school/term lock
    # targets.  It stays outside the transaction so no row can be held before
    # SchoolSyncState.
    scope = (
        RescheduleRequest.objects.select_related("session__offering__term")
        .only(
            "id",
            "session__offering__term_id",
            "session__offering__term__school_id",
        )
        .get(pk=request.id)
    )
    error: ScheduleChangeError | None = None
    change = None
    with transaction.atomic():
        # Approval follows the project lock order:
        # SchoolSyncState -> Term -> RescheduleRequest -> ClassSession.  The
        # request row must not be locked before these first two rows, because a
        # future importer may hold State/Term and then touch pending requests.
        # apply_session_change locks the same State/Term rows again inside its
        # savepoint; PostgreSQL permits re-locking within one transaction.
        SchoolSyncState.objects.select_for_update().get_or_create(
            school_id=scope.session.offering.term.school_id,
        )
        Term.objects.select_for_update().get(pk=scope.session.offering.term_id)
        locked = _pending_locked_request(request.id)
        try:
            # The inner atomic is a savepoint.  expected validation failures in
            # apply_session_change raise before changing anything; the outer
            # transaction can still persist the updated check summary.
            with transaction.atomic():
                change = apply_session_change(
                    locked.session_id,
                    target_date=locked.target_date,
                    start_period=locked.target_start_period,
                    end_period=locked.target_end_period,
                    classroom_id=locked.target_classroom_id,
                    actor=actor,
                    reason=locked.reason,
                    approval_ref=decision_ref,
                    forced=forced,
                    request=locked,
                    now=now,
                )
        except ScheduleChangeError as exc:
            if exc.evaluation is not None:
                _record_failed_evaluation(locked, exc.evaluation, now)
            error = exc
        else:
            locked.status = RescheduleStatus.APPROVED
            locked.decision_by = actor
            locked.decided_at = now
            locked.decision_ref = decision_ref
            locked.decision_note = note
            locked.forced_override = forced
            locked.save(
                update_fields=[
                    "status",
                    "decision_by",
                    "decided_at",
                    "decision_ref",
                    "decision_note",
                    "forced_override",
                    "updated_at",
                ]
            )
    if error is not None:
        raise error
    assert change is not None
    return locked


def approve_request(
    request: RescheduleRequest,
    actor,
    *,
    decision_ref: str,
    note: str,
    now: datetime,
) -> RescheduleRequest:
    """Approve normally; only an ``available`` fresh evaluation may proceed."""
    return _approve(
        request,
        actor=actor,
        decision_ref=decision_ref.strip(),
        note=note,
        now=now,
        forced=False,
    )


def force_approve_request(
    request: RescheduleRequest,
    actor,
    *,
    decision_ref: str,
    note: str,
    now: datetime,
) -> RescheduleRequest:
    """Register a school-forced arrangement with a mandatory approval reference."""
    if not decision_ref.strip():
        raise RescheduleRequestError(
            "APPROVAL_REF_REQUIRED",
            "登记学校强制安排时必须填写学校审批编号。",
            status_code=400,
            field_errors={"decision_ref": ["登记学校强制安排时必须填写学校审批编号。"]},
        )
    return _approve(
        request,
        actor=actor,
        decision_ref=decision_ref.strip(),
        note=note,
        now=now,
        forced=True,
    )


def reject_request(
    request: RescheduleRequest,
    actor,
    *,
    note: str,
    now: datetime,
) -> RescheduleRequest:
    """Reject a submitted request and notify the applicant; never move a session.

    Rejection writes no session, version or ChangeLog row.  It therefore holds
    only the request row instead of taking SchoolSyncState/Term; a concurrent
    sync writer that holds those rows first can wait for this short request lock
    without creating an inverse lock cycle.
    """
    with transaction.atomic():
        locked = _pending_locked_request(request.id)
        locked.status = RescheduleStatus.REJECTED
        locked.decision_by = actor
        locked.decided_at = now
        locked.decision_ref = ""
        locked.decision_note = note
        locked.forced_override = False
        locked.save(
            update_fields=[
                "status",
                "decision_by",
                "decided_at",
                "decision_ref",
                "decision_note",
                "forced_override",
                "updated_at",
            ]
        )
        body = "你的调课申请已被驳回，课表保持原安排。"
        if note.strip():
            body += f" 审批说明：{note.strip()}"
        notify(
            locked.applicant,
            type=NotificationType.RESCHEDULE_RESULT,
            object_type="reschedule_request",
            object_id=locked.id,
            title="调课申请已驳回",
            body=body,
            event_key=f"reschedule_request:{locked.id}:rejected",
        )
        return locked
