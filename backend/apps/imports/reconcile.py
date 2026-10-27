"""Turn the effective import timetable into session changes and decisions.

Preview and commit both consume this module.  It never writes a formal table;
it pairs the expansion (plus explicit ``sessions.csv`` targets) with existing
sessions, classifies each difference and attaches the conflict evaluation that
B30/B31 share.  ``commit`` performs the actual writes through
``apps.scheduling.changes``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from django.utils import timezone as django_timezone

from apps.academics.models import CourseOffering, TeacherProfile
from apps.classrooms.models import Classroom, RoomBlock
from apps.imports.parsing import make_issue
from apps.imports.projection.base import CHANGED, NEW, BuildContext
from apps.imports.projection.planning import PlanData
from apps.imports.session_classify import (
    ACTION_CANCEL,
    ACTION_DECISION,
    ACTION_KEPT_COMPLETED,
    ACTION_KEPT_PAST,
    ACTION_NEW,
    ACTION_RESTORE,
    ACTION_UNCHANGED,
    ACTION_UPDATE,
    DECISION_COMPLETED,
    DECISION_CONFLICT,
    DecisionDraft,
    ReconcileResult,
    SessionAction,
    _classify,
    _teachers_by_session,
    _touch_reasons,
    _touch_sets,
)
from apps.imports.session_targets import (
    _LAYOUT_FIELDS,
    ReconcileTarget,
    _pair_plan_to_existing,
    _state_payload,
    _target_cancel,
    _target_diff,
    _target_from_plan,
    _target_from_row,
)
from apps.scheduling.models import (
    PersonBlock,
    SessionStatus,
)
from apps.scheduling.reschedule_options import (
    ImportEvaluationRequest,
    ImportOccupancyTarget,
    TargetEvaluation,
    evaluate_import_targets,
)


def _reconcile_error(ctx: BuildContext, target: ReconcileTarget, message: str) -> None:
    if target.row is not None:
        ctx.add_issue(
            make_issue(
                file=target.row.file,
                sheet=target.row.sheet,
                row=target.row.row_number,
                field="session_date",
                value=target.session_date.isoformat(),
                code="IMPORT_TARGET_INVALID",
                message=message,
            )
        )
        return
    ctx.add_issue(
        make_issue(
            file="",
            sheet="",
            row=0,
            field="",
            value=str(target.external_session_id),
            code="IMPORT_TARGET_INVALID",
            message=message,
        )
    )


def reconcile_sessions(ctx: BuildContext, plan: PlanData, *, now: datetime) -> ReconcileResult:
    """Pair, classify and evaluate every affected session without writing."""
    if django_timezone.is_naive(now):
        raise ValueError("now must be an aware datetime")

    term_plans = plan.term_plans
    all_existing = [session for term_plan in term_plans.values() for session in term_plan.existing_sessions]
    touches = _touch_sets(all_existing)
    teachers_by_session = _teachers_by_session(all_existing)

    external_by_session: dict[UUID, str] = {}
    for identity in ctx.existing.identities.values():
        if identity.entity_type == "session":
            external_by_session[identity.object_id] = identity.external_id

    teacher_ids: set[UUID] = set()
    classroom_ids: set[UUID] = set()
    offering_ids: set[UUID] = set()
    for term_plan in term_plans.values():
        for session in term_plan.existing_sessions:
            teacher_ids.update(teachers_by_session.get(session.id, set()))
            classroom_ids.add(session.classroom_id)
            offering_ids.add(session.offering_id)
        for planned in term_plan.planned:
            teacher_ids.update(planned.teacher_ids)
            classroom_ids.add(planned.classroom_id)
            offering_ids.add(planned.offering_id)
    row_objects = [obj for obj in ctx.graph.sessions.values() if obj.state != "error"]
    for obj in row_objects:
        classroom_ids.add(obj.data.classroom_id)
        offering_ids.add(obj.data.offering_id)
        teacher_ids.update(obj.metadata.get("teacher_ids", ()))

    teacher_codes = dict(TeacherProfile.objects.filter(pk__in=teacher_ids).values_list("id", "teacher_no"))
    classroom_codes = dict(Classroom.objects.filter(pk__in=classroom_ids).values_list("id", "classroom_code"))
    classrooms = {classroom.id: classroom for classroom in Classroom.objects.filter(pk__in=classroom_ids)}
    offering_codes = dict(CourseOffering.objects.filter(pk__in=offering_ids).values_list("id", "offering_code"))
    # New graph objects have no database row yet; their in-memory UUIDs change
    # on every preview.  Display their stable source code instead so the
    # preview fingerprint does not change between identical previews.
    for obj in ctx.graph.teachers.values():
        teacher_codes[obj.data.pk] = obj.external_id
    for obj in ctx.graph.classrooms.values():
        classroom_codes[obj.data.pk] = obj.data.classroom_code
    for (_, offering_code), obj in ctx.graph.offerings.items():
        offering_codes[obj.data.pk] = offering_code

    row_by_existing: dict[UUID, ReconcileTarget] = {}
    row_by_plan: dict[tuple[UUID, date, int], ReconcileTarget] = {}
    row_new: list[ReconcileTarget] = []
    for obj in row_objects:
        term = term_plans[obj.metadata["term_code"]].term
        target = _target_from_row(term, obj)
        if target.existing is not None:
            row_by_existing[target.existing.pk] = target
        else:
            row_new.append(target)
        planned_key = obj.metadata.get("planned_key")
        if planned_key is not None:
            row_by_plan[planned_key] = target

    targets_by_existing: dict[UUID, ReconcileTarget] = {}
    new_targets: list[ReconcileTarget] = []
    for term_code, term_plan in term_plans.items():
        pairs = _pair_plan_to_existing(term_plan)
        for planned in term_plan.planned:
            key = (planned.rule_id, planned.origin_teaching_date, planned.origin_period)
            if key in row_by_plan:
                continue
            existing = pairs.by_plan.get(key)
            if existing is not None and existing.pk in row_by_existing:
                continue
            target = _target_from_plan(
                term=term_plan.term,
                term_code=term_code,
                planned=planned,
                existing=existing,
                external_session_id=external_by_session.get(existing.pk, "") if existing is not None else "",
            )
            if existing is None:
                new_targets.append(target)
            else:
                targets_by_existing[existing.pk] = target

        for existing in term_plan.existing_sessions:
            if existing.rule_id is None or existing.pk in targets_by_existing or existing.pk in row_by_existing:
                continue
            if existing.rule_id not in term_plan.rule_plans:
                # The effective rule could not be expanded (missing teacher,
                # period or classroom); leave its existing sessions alone and
                # let the validation issue block the batch.
                continue
            targets_by_existing[existing.pk] = _target_cancel(
                term_code=term_code,
                existing=existing,
                external_session_id=external_by_session.get(existing.pk, ""),
                reason="导入更新：规则不再生成该课次",
                teachers_by_session=teachers_by_session,
            )

    for session_id, target in row_by_existing.items():
        targets_by_existing[session_id] = target
    new_targets.extend(row_new)

    def target_sort_key(target: ReconcileTarget) -> tuple:
        return (
            target.term_code,
            offering_codes.get(target.offering_id, ""),
            target.session_date,
            target.start_period,
            target.end_period,
            target.status,
            tuple(teacher_codes.get(teacher_id, str(teacher_id)) for teacher_id in target.teacher_ids),
            classroom_codes.get(target.classroom_id, str(target.classroom_id)),
            target.external_session_id,
            str(target.existing.pk) if target.existing is not None else "",
        )

    ordered_existing = sorted(targets_by_existing.values(), key=target_sort_key)
    new_targets = sorted(new_targets, key=target_sort_key)

    def evaluation_request(target: ReconcileTarget) -> ImportEvaluationRequest | None:
        existing = target.existing
        if existing is None or target.status != SessionStatus.SCHEDULED:
            return None
        if existing.status == SessionStatus.COMPLETED:
            return None
        diff = _target_diff(existing, target, term_plans[target.term_code].term, teachers_by_session)
        if not (existing.status == SessionStatus.CANCELLED or diff & (_LAYOUT_FIELDS | {"teachers"})):
            return None
        classroom = classrooms.get(target.classroom_id)
        if classroom is None:
            return None
        return ImportEvaluationRequest(
            session=existing,
            day=target.session_date,
            start_period=target.start_period,
            end_period=target.end_period,
            classroom=classroom,
            start_at=target.start_at,
            end_at=target.end_at,
            teacher_ids=target.teacher_ids,
            status=target.status,
        )

    evaluations: dict[UUID, TargetEvaluation] = {}
    requests_by_term: dict[str, list[ImportEvaluationRequest]] = defaultdict(list)
    occupancy_targets: list[ImportOccupancyTarget] = []
    replaced_session_ids: set[UUID] = set()

    for target in [*ordered_existing, *new_targets]:
        existing = target.existing
        if existing is None:
            if target.status != SessionStatus.CANCELLED:
                occupancy_targets.append(
                    ImportOccupancyTarget(
                        id=uuid4(),
                        offering_id=target.offering_id,
                        start_at=target.start_at,
                        end_at=target.end_at,
                        classroom_id=target.classroom_id,
                        teacher_ids=target.teacher_ids,
                        status=target.status,
                        course_name=offering_codes.get(target.offering_id, ""),
                    )
                )
            continue
        diff = _target_diff(existing, target, term_plans[target.term_code].term, teachers_by_session)
        if not diff or existing.status == SessionStatus.COMPLETED:
            continue
        if (
            (diff & _LAYOUT_FIELDS or target.status == SessionStatus.CANCELLED)
            and existing.start_at <= now
            and not (target.explicit_change or target.from_row)
            and not _touch_reasons(existing, touches)
        ):
            continue
        replaced_session_ids.add(existing.pk)
        if target.status != SessionStatus.CANCELLED:
            occupancy_targets.append(
                ImportOccupancyTarget(
                    id=existing.pk,
                    offering_id=target.offering_id,
                    start_at=target.start_at,
                    end_at=target.end_at,
                    classroom_id=target.classroom_id,
                    teacher_ids=target.teacher_ids,
                    status=target.status,
                    course_name=offering_codes.get(target.offering_id, ""),
                )
            )

    for target in ordered_existing:
        request = evaluation_request(target)
        if request is not None:
            requests_by_term[target.term_code].append(request)
            replaced_session_ids.add(request.session.id)

    overlay_room_blocks: list[RoomBlock] = []
    replaced_room_block_ids: set[UUID] = set()
    for obj in ctx.graph.room_blocks.values():
        if obj.state in {NEW, CHANGED} and isinstance(obj.data, RoomBlock):
            overlay_room_blocks.append(obj.data)
            if isinstance(obj.existing, RoomBlock):
                replaced_room_block_ids.add(obj.existing.pk)
    overlay_person_blocks: list[PersonBlock] = []
    replaced_person_block_ids: set[UUID] = set()
    for obj in ctx.graph.person_blocks.values():
        if obj.state in {NEW, CHANGED} and isinstance(obj.data, PersonBlock):
            overlay_person_blocks.append(obj.data)
            if isinstance(obj.existing, PersonBlock):
                replaced_person_block_ids.add(obj.existing.pk)
    for term_code, requests in requests_by_term.items():
        evaluations.update(
            evaluate_import_targets(
                requests,
                periods=term_plans[term_code].periods,
                now=now,
                replaced_session_ids=replaced_session_ids,
                occupancy_targets=occupancy_targets,
                replaced_room_block_ids=replaced_room_block_ids,
                overlay_room_blocks=overlay_room_blocks,
                replaced_person_block_ids=replaced_person_block_ids,
                overlay_person_blocks=overlay_person_blocks,
            )
        )

    actions: list[SessionAction] = []
    for target in ordered_existing:
        term_plan = term_plans[target.term_code]
        if target.end_at <= target.start_at:
            _reconcile_error(ctx, target, "导入目标时间无效。")
            continue
        actions.append(
            _classify(
                target=target,
                term=term_plan.term,
                touches=touches,
                teacher_codes=teacher_codes,
                classroom_codes=classroom_codes,
                teachers_by_session=teachers_by_session,
                evaluation=evaluations.get(target.existing.pk if target.existing else None),
                now=now,
            )
        )
    for target in new_targets:
        if target.end_at <= target.start_at:
            _reconcile_error(ctx, target, "导入新增课次时间无效。")
            continue
        actions.append(
            SessionAction(
                action=ACTION_NEW,
                target=target,
                proposed_state=_state_payload(
                    target=target,
                    teacher_codes=teacher_codes,
                    classroom_codes=classroom_codes,
                    teachers_by_session=teachers_by_session,
                    term=term_plans[target.term_code].term,
                    external_session_id=target.external_session_id,
                ),
            )
        )

    counts = {
        "new": 0,
        "unchanged": 0,
        "auto_updated": 0,
        "auto_cancelled": 0,
        "auto_restored": 0,
        "manual": 0,
        "conflict": 0,
        "completed_locked": 0,
        "kept_past": 0,
        "kept_completed": 0,
    }
    changes: list[dict] = []
    decisions: list[DecisionDraft] = []
    for action in actions:
        if action.action == ACTION_NEW:
            counts["new"] += 1
        elif action.action == ACTION_UNCHANGED:
            counts["unchanged"] += 1
        elif action.action == ACTION_UPDATE:
            counts["auto_updated"] += 1
        elif action.action == ACTION_CANCEL:
            counts["auto_cancelled"] += 1
        elif action.action == ACTION_RESTORE:
            counts["auto_restored"] += 1
        elif action.action == ACTION_KEPT_PAST:
            counts["kept_past"] += 1
        elif action.action == ACTION_KEPT_COMPLETED:
            counts["kept_completed"] += 1
        elif action.action == ACTION_DECISION:
            counts["manual"] += 1
            if action.kind == DECISION_CONFLICT:
                counts["conflict"] += 1
            if action.kind == DECISION_COMPLETED:
                counts["completed_locked"] += 1
            decisions.append(
                DecisionDraft(
                    session_id=action.target.existing.pk,
                    kind=action.kind,
                    proposed=action.proposed_state,
                    current=action.current_state,
                    reason="；".join(action.reasons),
                    options=action.options,
                )
            )
        if action.action not in {ACTION_UNCHANGED}:
            entry = {
                "session_id": str(action.target.existing.pk) if action.target.existing else None,
                "external_session_id": action.target.external_session_id,
                "offering_code": offering_codes.get(action.target.offering_id, ""),
                "action": action.action,
                "kind": action.kind,
                "before": action.current_state,
                "after": action.proposed_state,
                "reason": "；".join(action.reasons),
            }
            changes.append(entry)

    # A removed offering-assignment teacher may only lose visibility when this
    # same batch reassigns every future scheduled session away from them.
    for check in ctx.deferred_assignment_checks:
        blocking = [
            action
            for action in actions
            if action.target.offering_id == check.offering_id
            and action.target.status == SessionStatus.SCHEDULED
            and action.target.start_at > now
            and check.teacher_id in action.target.teacher_ids
        ]
        if not blocking:
            continue
        ctx.add_issue(
            make_issue(
                file=check.row.file,
                sheet=check.row.sheet,
                row=check.row.row_number,
                field="teacher_codes",
                value=check.teacher_no,
                code="CHANGE_NOT_SUPPORTED_YET",
                message=(
                    f"教师 {check.teacher_no} 在该课程开设中仍有未来 scheduled 课次，"
                    "移除任课会改变课表；请同时调整生成这些课次的规则或先处理课次。"
                ),
            )
        )

    completion_hours: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for action in actions:
        target = action.target
        if target.status != SessionStatus.COMPLETED or target.actual_hours is None:
            continue
        if target.existing is None or (
            target.existing.status == SessionStatus.SCHEDULED and action.action == ACTION_UPDATE
        ):
            offering_code = offering_codes.get(target.offering_id)
            if offering_code:
                completion_hours[offering_code] += target.actual_hours

    return ReconcileResult(
        actions=tuple(actions),
        decisions=tuple(decisions),
        counts=counts,
        changes=tuple(changes),
        estimated_hours={code: str(value) for code, value in sorted(completion_hours.items())},
    )
