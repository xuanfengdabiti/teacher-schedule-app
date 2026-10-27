"""Transactional commit of one previewed import batch."""

from __future__ import annotations

import copy
from datetime import date
from uuid import UUID, uuid4

from django.db import models, transaction
from django.utils import timezone as django_timezone

from apps.academics.models import (
    CourseOffering,
    DataCoverage,
    Enrollment,
    Period,
    TeacherProfile,
    Term,
)
from apps.classrooms.models import Classroom
from apps.imports.commit_helpers import (
    add_session_display_changes,
    apply_assignment_changes,
    apply_changes,
    assignment_teacher_ids,
    create_teaching_assignments,
    make_identity,
    mark_failed,
    persist_new,
    stale_result,
)
from apps.imports.models import ImportBatch, ImportDecision, ImportStatus
from apps.imports.projection.base import (
    CHANGED,
    ENTITY_TEACHER,
    NEW,
    UNCHANGED,
    CommitResult,
    ExistingLookup,
    ImportCommitError,
    PreviewResult,
)
from apps.imports.projection.planning import referenced_term_codes
from apps.imports.session_classify import (
    ACTION_CANCEL,
    ACTION_DECISION,
    ACTION_NEW,
    ACTION_RESTORE,
    ACTION_UPDATE,
    DECISION_COMPLETED,
    DECISION_CONFLICT,
    OPTION_APPLY,
    OPTION_KEEP,
)
from apps.imports.session_targets import ReconcileTarget
from apps.scheduling import selectors
from apps.scheduling.changelog import ChangeEntry
from apps.scheduling.import_changes import (
    IMPORT_CANCEL,
    IMPORT_NEW,
    IMPORT_RESTORE,
    IMPORT_UPDATE,
    ImportSessionWrite,
    apply_import_session_batch,
)
from apps.scheduling.models import (
    ClassSession,
    SchoolSyncState,
    SessionStatus,
)
from apps.scheduling.reschedule_options import (
    ImportEvaluationRequest,
    ImportOccupancyTarget,
    evaluate_import_targets,
)
from apps.teaching.models import TeachingPlan as TeachingPlanModel


def commit_plan(
    batch: ImportBatch,
    actor,
    result: PreviewResult,
    *,
    decisions: dict,
    now,
) -> CommitResult:
    graph = result.graph
    created_count = 0
    sessions_new = 0
    sessions_cancelled = 0
    sessions_restored = 0
    sessions_changed = 0
    sessions_matched = 0
    decisions_kept = 0
    decisions_applied = 0
    definition_changed_terms: set[str] = set()
    term_name_changes: set[str] = set()
    teacher_name_changes: set[UUID] = set()
    classroom_display_changes: set[UUID] = set()
    course_name_changes: set[UUID] = set()
    class_name_changes: set[UUID] = set()
    scope_bumps: set[UUID] = set()

    for term_code in sorted(graph.terms):
        term_obj = graph.terms[term_code]
        if term_obj.state == NEW:
            persist_new(term_obj)
            make_identity(batch, term_obj, term_obj.data.pk)
            created_count += 1

    existing_lookup = ExistingLookup.load(batch)
    locked_terms: dict[str, Term] = {}
    for term_code in sorted(referenced_term_codes(graph, batch)):
        term_obj = graph.terms.get(term_code)
        if term_obj is not None:
            term_id = term_obj.data.pk
        else:
            target = existing_lookup.target("term", term_code)
            if not isinstance(target, Term):
                target = batch.term if batch.term is not None and batch.term.code == term_code else None
            if target is None:
                continue
            term_id = target.pk
        locked_terms[term_code] = Term.objects.select_for_update().get(pk=term_id)

    for term_code in sorted(graph.terms):
        term_obj = graph.terms[term_code]
        if term_obj.state != CHANGED:
            continue
        term = locked_terms.get(term_code)
        if term is None:
            continue
        applied = apply_changes(term, term_obj.changes, allowed={"name"})
        if applied:
            term_name_changes.add(term_code)

    existing_teacher_ids = {
        obj.existing.pk for obj in graph.teachers.values() if obj.existing is not None
    } | assignment_teacher_ids(graph)
    locked_teachers = {
        teacher.pk: teacher
        for teacher in TeacherProfile.objects.select_for_update().filter(pk__in=existing_teacher_ids)
    }
    teacher_lookup: dict[str, TeacherProfile] = {}
    for identity in existing_lookup.identities.values():
        if identity.entity_type == ENTITY_TEACHER:
            target = existing_lookup.target(ENTITY_TEACHER, identity.external_id)
            if isinstance(target, TeacherProfile):
                teacher_lookup[identity.external_id] = locked_teachers.get(target.pk, target)

    for teacher_obj in graph.teachers.values():
        if teacher_obj.state == NEW:
            persist_new(teacher_obj)
            make_identity(batch, teacher_obj, teacher_obj.data.pk)
            created_count += 1
            teacher_lookup[teacher_obj.external_id] = teacher_obj.data  # type: ignore[assignment]
            locked_teachers[teacher_obj.data.pk] = teacher_obj.data  # type: ignore[assignment]
            continue
        target = teacher_obj.existing
        if isinstance(target, TeacherProfile):
            locked = locked_teachers.get(target.pk, target)
            if teacher_obj.state == CHANGED:
                applied = apply_changes(locked, teacher_obj.changes)
                if "formal_name" in applied:
                    teacher_name_changes.add(locked.pk)
            teacher_lookup[teacher_obj.external_id] = locked

    for period_obj in graph.periods.values():
        if period_obj.state == NEW:
            persist_new(period_obj)
            make_identity(batch, period_obj, period_obj.data.pk)
            created_count += 1
        elif period_obj.state == CHANGED and period_obj.existing is not None:
            apply_changes(
                period_obj.existing,
                period_obj.changes,
                allowed={"start_time", "end_time", "block_code"},
            )
            if period_obj.metadata.get("term_code"):
                definition_changed_terms.add(period_obj.metadata["term_code"])

    for classroom_obj in graph.classrooms.values():
        if classroom_obj.state == NEW:
            persist_new(classroom_obj)
            make_identity(batch, classroom_obj, classroom_obj.data.pk)
            created_count += 1
        elif classroom_obj.state == CHANGED:
            target = classroom_obj.existing
            if target is not None:
                applied = apply_changes(target, classroom_obj.changes)
                if {"campus", "building", "room_no"} & set(applied):
                    classroom_display_changes.add(target.pk)

    for course_obj in graph.courses.values():
        if course_obj.state == NEW:
            persist_new(course_obj)
            make_identity(batch, course_obj, course_obj.data.pk)
            created_count += 1
        elif course_obj.state == CHANGED and course_obj.existing is not None:
            applied = apply_changes(course_obj.existing, course_obj.changes, allowed={"name"})
            if applied:
                course_name_changes.add(course_obj.existing.pk)

    for class_obj in graph.teaching_classes.values():
        if class_obj.state == NEW:
            persist_new(class_obj)
            make_identity(batch, class_obj, class_obj.data.pk)
            created_count += 1
        elif class_obj.state == CHANGED and class_obj.existing is not None:
            applied = apply_changes(class_obj.existing, class_obj.changes, allowed={"name"})
            if applied:
                class_name_changes.add(class_obj.existing.pk)

    for offering_obj in graph.offerings.values():
        if offering_obj.state == NEW:
            persist_new(offering_obj)
            make_identity(batch, offering_obj, offering_obj.data.pk)
            created_count += 1
            term_code = offering_obj.metadata.get("term_code")
            term = locked_terms.get(term_code)
            if term is not None:
                create_teaching_assignments(offering_obj, term, teacher_lookup)
            continue
        if offering_obj.state != CHANGED:
            continue
        offering = offering_obj.existing
        if not isinstance(offering, CourseOffering):
            continue
        apply_changes(
            offering,
            offering_obj.changes,
            allowed={"target_hours", "required_equipment"},
        )
        if offering_obj.metadata.get("lead_changed"):
            offering.lead_teacher_id = offering_obj.data.lead_teacher_id
            offering.save(update_fields=["lead_teacher", "updated_at"])
        term_code = offering_obj.metadata.get("term_code")
        bumped = apply_assignment_changes(offering, locked_terms.get(term_code), offering_obj)
        scope_bumps.update(bumped)
        owner_change = offering_obj.metadata.get("plan_owner_change")
        if owner_change and owner_change.get("new_owner_id") is not None:
            TeachingPlanModel.objects.filter(
                offering_id=offering.pk,
                owner_id=owner_change["old_owner_id"],
            ).update(owner_id=owner_change["new_owner_id"], updated_at=now)

    for student_obj in graph.students.values():
        if student_obj.state == NEW:
            persist_new(student_obj)
            make_identity(batch, student_obj, student_obj.data.pk)
            created_count += 1

    for enrollment_obj in graph.enrollments.values():
        if enrollment_obj.state == NEW:
            persist_new(enrollment_obj)
            make_identity(batch, enrollment_obj, enrollment_obj.data.pk)
            created_count += 1
        elif enrollment_obj.state == CHANGED and isinstance(enrollment_obj.existing, Enrollment):
            target = enrollment_obj.existing
            applied = apply_changes(target, enrollment_obj.changes, allowed={"effective_until"})
            if applied:
                desired_until = target.effective_until or date.max
                overlapping = (
                    Enrollment.objects.filter(
                        offering_id=target.offering_id,
                        student_id=target.student_id,
                    )
                    .exclude(pk=target.pk)
                    .filter(effective_from__lt=desired_until)
                    .filter(
                        models.Q(effective_until__isnull=True) | models.Q(effective_until__gt=target.effective_from)
                    )
                )
                if overlapping.exists():
                    raise RuntimeError("选课有效期与同一课程开设、同一学生的其他选课重叠。")

    for exception_obj in graph.calendar_exceptions.values():
        if exception_obj.state == NEW:
            persist_new(exception_obj)
            make_identity(batch, exception_obj, exception_obj.data.pk)
            created_count += 1
            if exception_obj.metadata.get("term_code"):
                definition_changed_terms.add(exception_obj.metadata["term_code"])
        elif exception_obj.state == CHANGED and exception_obj.existing is not None:
            apply_changes(
                exception_obj.existing,
                exception_obj.changes,
                allowed={"date", "kind", "source_teaching_date", "reason"},
            )
            if exception_obj.metadata.get("term_code"):
                definition_changed_terms.add(exception_obj.metadata["term_code"])

    for rule_obj in graph.rules.values():
        if rule_obj.state == NEW:
            persist_new(rule_obj)
            teacher_ids = list(rule_obj.metadata.get("teacher_ids", ()))
            if teacher_ids:
                rule_obj.data.teachers.set(teacher_ids)
            make_identity(batch, rule_obj, rule_obj.data.pk)
            created_count += 1
            if rule_obj.metadata.get("term_code"):
                definition_changed_terms.add(rule_obj.metadata["term_code"])
        elif rule_obj.state == CHANGED and rule_obj.existing is not None:
            apply_changes(
                rule_obj.existing,
                rule_obj.changes,
                allowed={
                    "weekday",
                    "start_period",
                    "end_period",
                    "weeks",
                    "classroom_id",
                    "planned_hours",
                },
            )
            rule_obj.existing.teachers.set(list(rule_obj.metadata.get("teacher_ids", ())))
            if rule_obj.metadata.get("term_code"):
                definition_changed_terms.add(rule_obj.metadata["term_code"])

    reconciliation = result.reconciliation
    if reconciliation is None:
        raise RuntimeError("导入预览缺少课次重排结果，不能提交。")

    writes: list[ImportSessionWrite] = []
    write_targets: list[ReconcileTarget] = []
    for action in reconciliation.actions:
        target = action.target
        if action.action == ACTION_NEW:
            writes.append(
                ImportSessionWrite(
                    action=IMPORT_NEW,
                    target=target.write_target(),
                    identity_target=target.import_object,
                )
            )
            write_targets.append(target)
            continue

        forced = False
        change_ref = target.change_ref
        if action.action == ACTION_DECISION:
            decision = decisions.get(target.existing.pk) if target.existing is not None else None
            if decision is None:
                raise RuntimeError("导入人工决定项缺失，不能提交。")
            if decision.choice == OPTION_KEEP:
                decisions_kept += 1
                continue
            if decision.choice != OPTION_APPLY:
                raise RuntimeError("导入人工决定项尚未选择，不能提交。")
            forced = True
            change_ref = decision.note.strip()
            decisions_applied += 1
        elif action.action not in {ACTION_UPDATE, ACTION_CANCEL, ACTION_RESTORE}:
            continue

        if action.action == ACTION_CANCEL or (
            action.action == ACTION_DECISION and target.status == SessionStatus.CANCELLED
        ):
            write_action = IMPORT_CANCEL
        elif target.existing is not None and target.existing.status == SessionStatus.CANCELLED:
            write_action = IMPORT_RESTORE
        else:
            write_action = IMPORT_UPDATE
        writes.append(
            ImportSessionWrite(
                action=write_action,
                target=target.write_target(),
                session_id=target.existing.pk if target.existing else None,
                change_ref=change_ref,
                forced=forced,
            )
        )
        write_targets.append(target)

    def display_entries() -> list[ChangeEntry]:
        entries: dict[tuple[str, UUID], ChangeEntry] = {}
        if teacher_name_changes:
            add_session_display_changes(
                entries,
                ClassSession.objects.filter(session_teachers__teacher_id__in=teacher_name_changes).distinct(),
            )
        if course_name_changes:
            add_session_display_changes(
                entries,
                ClassSession.objects.filter(offering__course_id__in=course_name_changes),
            )
        if class_name_changes:
            add_session_display_changes(
                entries,
                ClassSession.objects.filter(offering__teaching_class_id__in=class_name_changes),
            )
        if classroom_display_changes:
            add_session_display_changes(
                entries,
                ClassSession.objects.filter(classroom_id__in=classroom_display_changes),
            )
        return list(entries.values())

    for block_obj in graph.room_blocks.values():
        if block_obj.state == NEW:
            persist_new(block_obj)
            make_identity(batch, block_obj, block_obj.data.pk)
            created_count += 1
        elif block_obj.state == CHANGED and block_obj.existing is not None:
            apply_changes(block_obj.existing, block_obj.changes, allowed={"start_at", "end_at", "reason"})

    for block_obj in graph.person_blocks.values():
        if block_obj.state == NEW:
            persist_new(block_obj)
            make_identity(batch, block_obj, block_obj.data.pk)
            created_count += 1
        elif block_obj.state == CHANGED and block_obj.existing is not None:
            apply_changes(block_obj.existing, block_obj.changes, allowed={"start_at", "end_at", "reason"})

    replaced_session_ids = {write.session_id for write in writes if write.session_id is not None}
    offering_ids = {write.target.offering_id for write in writes}
    offering_codes = dict(CourseOffering.objects.filter(pk__in=offering_ids).values_list("id", "offering_code"))
    occupancy_targets: list[ImportOccupancyTarget] = []
    for write in writes:
        target = write.target
        if write.action == IMPORT_CANCEL or target.status == SessionStatus.CANCELLED:
            continue
        occupancy_targets.append(
            ImportOccupancyTarget(
                id=write.session_id or uuid4(),
                offering_id=target.offering_id,
                start_at=target.start_at,
                end_at=target.end_at,
                classroom_id=target.classroom_id,
                teacher_ids=target.teacher_ids,
                status=target.status,
                course_name=offering_codes.get(target.offering_id, ""),
            )
        )
    requests_by_term: dict[str, list[ImportEvaluationRequest]] = {}
    classroom_ids = {
        write.target.classroom_id
        for write, target in zip(writes, write_targets)
        if target.existing is not None
        and write.action in {IMPORT_UPDATE, IMPORT_RESTORE}
        and target.status in {SessionStatus.SCHEDULED, SessionStatus.COMPLETED}
    }
    classrooms = {classroom.id: classroom for classroom in Classroom.objects.filter(pk__in=classroom_ids)}
    for write, target in zip(writes, write_targets):
        if target.existing is None or write.session_id is None:
            continue
        if write.action not in {IMPORT_UPDATE, IMPORT_RESTORE}:
            continue
        if target.status not in {SessionStatus.SCHEDULED, SessionStatus.COMPLETED}:
            continue
        classroom = classrooms.get(write.target.classroom_id)
        if classroom is None:
            continue
        requests_by_term.setdefault(target.term_code, []).append(
            ImportEvaluationRequest(
                session=target.existing,
                day=target.session_date,
                start_period=target.start_period,
                end_period=target.end_period,
                classroom=classroom,
                start_at=target.start_at,
                end_at=target.end_at,
                teacher_ids=target.teacher_ids,
                status=target.status,
            )
        )
    periods_by_term: dict[str, dict[int, Period]] = {}
    for term_code in requests_by_term:
        term = locked_terms.get(term_code)
        if term is None:
            continue
        periods_by_term[term_code] = {period.period_number: period for period in Period.objects.filter(term=term)}
    for term_code, requests in requests_by_term.items():
        periods = periods_by_term.get(term_code)
        if periods is None:
            continue
        evaluations = evaluate_import_targets(
            requests,
            periods=periods,
            now=now,
            replaced_session_ids=replaced_session_ids,
            occupancy_targets=occupancy_targets,
        )
        for request in requests:
            evaluation = evaluations.get(request.session.id)
            if evaluation is None or not evaluation.conflicts:
                continue
            decision = decisions.get(request.session.id)
            if decision is None or decision.kind != DECISION_CONFLICT or decision.choice != OPTION_APPLY:
                raise ImportCommitError(
                    "IMPORT_FINAL_CONFLICT",
                    (
                        "提交时按实际采用/保留组合重新检查，目标课次仍存在教师、学生或教室冲突；"
                        "请重新预览并调整人工决定。"
                    ),
                )

    write_result = apply_import_session_batch(
        writes,
        batch=batch,
        actor=actor,
        now=now,
        terms_by_code=locked_terms,
        definition_term_codes=definition_changed_terms,
        term_log_codes=term_name_changes,
        extra_entries=display_entries
        if teacher_name_changes or course_name_changes or class_name_changes or classroom_display_changes
        else [],
    )
    for write, session in zip(
        (write for write in writes if write.action == IMPORT_NEW),
        write_result.created_sessions,
    ):
        if write.identity_target is not None:
            make_identity(batch, write.identity_target, session.pk)
    sessions_new = write_result.sessions_new
    sessions_changed = write_result.sessions_updated
    sessions_cancelled = write_result.sessions_cancelled
    sessions_restored = write_result.sessions_restored

    sessions_matched = sum(
        1
        for action in reconciliation.actions
        if action.target.existing is not None and action.target.external_session_id
    )

    remaining_scope_bumps = scope_bumps - write_result.scope_bumped_teacher_ids
    if remaining_scope_bumps:
        for teacher in TeacherProfile.objects.select_for_update().filter(pk__in=remaining_scope_bumps):
            teacher.assignment_scope_version += 1
            teacher.save(update_fields=["assignment_scope_version", "updated_at"])

    for coverage_obj in graph.coverages.values():
        if coverage_obj.state == NEW:
            persist_new(coverage_obj)
            make_identity(batch, coverage_obj, coverage_obj.data.pk)
            created_count += 1
        elif coverage_obj.state == CHANGED and isinstance(coverage_obj.existing, DataCoverage):
            apply_changes(
                coverage_obj.existing,
                coverage_obj.changes,
                allowed={
                    "from_date",
                    "until_date",
                    "completeness",
                    "source_updated_at",
                    "confirmation_ref",
                },
            )

    if len(referenced_term_codes(graph, batch)) == 1 and len(locked_terms) == 1:
        batch.term = next(iter(locked_terms.values()))
    batch.status = ImportStatus.COMMITTED
    batch.committed_by = actor
    batch.committed_at = now
    batch.save(update_fields=["term", "status", "committed_by", "committed_at", "updated_at"])

    unchanged = sum(1 for obj in graph.objects() if obj.state == UNCHANGED)
    changed = sum(1 for obj in graph.objects() if obj.state == CHANGED)
    return CommitResult(
        batch=batch,
        committed=True,
        created=created_count,
        changed=changed,
        sessions_new=sessions_new,
        sessions_matched=sessions_matched,
        sessions_changed=sessions_changed,
        sessions_cancelled=sessions_cancelled,
        sessions_restored=sessions_restored,
        decisions_kept=decisions_kept,
        decisions_applied=decisions_applied,
        unchanged=unchanged,
    )


def _preview_decision_signature(preview: PreviewResult) -> dict[str, tuple]:
    if preview.reconciliation is None:
        return {}
    return {str(decision.session_id): decision.signature() for decision in preview.reconciliation.decisions}


def _stored_decision_signature(decisions: list[ImportDecision]) -> dict[str, tuple]:
    return {
        str(decision.session_id): (
            decision.kind,
            decision.proposed_json,
            decision.current_json,
            tuple(decision.options or ()),
        )
        for decision in decisions
    }


def _validate_decisions(decisions: list[ImportDecision]) -> None:
    undecided = [decision for decision in decisions if not decision.choice]
    if undecided:
        raise ImportCommitError(
            "UNDECIDED_IMPORT_DECISIONS",
            f"还有 {len(undecided)} 条导入人工决定项未选择；请逐条选择或全部保留现状后再提交。",
        )
    for decision in decisions:
        options = set(decision.options or ())
        if decision.choice not in options:
            raise ImportCommitError(
                "INVALID_IMPORT_DECISION",
                f"课次 {decision.session_id} 的决定项不支持所选动作。",
            )
        if decision.choice == OPTION_APPLY:
            if decision.kind == DECISION_COMPLETED:
                raise ImportCommitError(
                    "COMPLETED_SESSION_LOCKED",
                    "已完成课次只能保留现状，不能采用导入结果。",
                )
            if not decision.note.strip():
                raise ImportCommitError(
                    "IMPORT_DECISION_NOTE_REQUIRED",
                    "采用导入结果时必须填写依据说明。",
                )


def commit_batch(
    batch: ImportBatch,
    actor,
    *,
    school_date: date | None = None,
    now=None,
) -> CommitResult:
    """Commit one previewed batch in a single transaction."""
    from apps.imports.preview import build_preview

    now = now or django_timezone.now()
    batch.refresh_from_db()
    if batch.status != ImportStatus.PREVIEWED:
        raise ImportCommitError("INVALID_STATUS", "只有已预览的批次可以提交。")
    if batch.has_errors:
        raise ImportCommitError("PREVIEW_HAS_ERRORS", "预览存在错误，整批不能提交。")
    school_date = school_date or selectors.current_school_date()

    try:
        with transaction.atomic():
            SchoolSyncState.objects.select_for_update().get_or_create(school_id=batch.school_id)
            if batch.term_id:
                Term.objects.select_for_update().get(pk=batch.term_id)

            locked_batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
            if locked_batch.status != ImportStatus.PREVIEWED:
                raise ImportCommitError("INVALID_STATUS", "批次已被其他操作处理，只有已预览的批次可以提交。")
            if locked_batch.has_errors:
                raise ImportCommitError("PREVIEW_HAS_ERRORS", "预览存在错误，整批不能提交。")
            decisions = list(
                ImportDecision.objects.select_for_update().filter(batch=locked_batch).order_by("created_at", "id")
            )

            baseline_counts = copy.deepcopy(locked_batch.row_counts)
            baseline_errors = copy.deepcopy(locked_batch.errors_json)
            baseline_base = locked_batch.preview_base_schedule_version
            baseline_file_hash = locked_batch.file_hash
            baseline_fingerprint = locked_batch.preview_fingerprint

            preview = build_preview(locked_batch, persist=False, school_date=school_date, now=now)
            preview_issue_dicts = [issue.as_dict() for issue in preview.issues]
            if (
                preview.row_counts != baseline_counts
                or preview_issue_dicts != baseline_errors
                or preview.preview_base_schedule_version != baseline_base
                or preview.file_hash != baseline_file_hash
                or preview.fingerprint != baseline_fingerprint
            ):
                return stale_result(locked_batch, preview, baseline_errors)
            if _preview_decision_signature(preview) != _stored_decision_signature(decisions):
                return stale_result(
                    locked_batch,
                    preview,
                    baseline_errors,
                    code="PREVIEW_DECISIONS_STALE",
                    message="预览之后人工决定项已变化；请重新预览并重新选择，旧决定不能提交。",
                )
            if preview.has_errors:
                raise ImportCommitError("PREVIEW_HAS_ERRORS", "重新预览发现错误，整批不能提交。")
            _validate_decisions(decisions)

            decision_map = {decision.session_id: decision for decision in decisions}
            return commit_plan(
                locked_batch,
                actor,
                preview,
                decisions=decision_map,
                now=now,
            )
    except ImportCommitError:
        raise
    except Exception as exc:
        mark_failed(batch, exc)
        raise ImportCommitError("COMMIT_FAILED", f"导入提交失败，已整体回滚：{exc}") from exc
