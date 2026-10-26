"""Transactional commit of one previewed import batch."""

from __future__ import annotations

import copy
from datetime import date
from uuid import UUID

from django.db import models, transaction
from django.utils import timezone as django_timezone

from apps.academics.models import (
    CourseOffering,
    DataCoverage,
    Enrollment,
    TeacherProfile,
    Term,
)
from apps.imports.commit_helpers import (
    add_session_display_changes,
    apply_assignment_changes,
    apply_changes,
    apply_generated_session_values,
    assignment_teacher_ids,
    create_teaching_assignments,
    make_identity,
    mark_failed,
    persist_new,
    queue_change,
    stale_result,
)
from apps.imports.models import ImportBatch, ImportStatus
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
from apps.scheduling import selectors
from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import (
    ChangeEntityType,
    ClassSession,
    SchoolSyncState,
    SessionTeacher,
)
from apps.scheduling.services import generate_sessions_for_term
from apps.teaching.models import TeachingPlan as TeachingPlanModel


def commit_plan(
    batch: ImportBatch,
    actor,
    result: PreviewResult,
    *,
    generate_sessions,
) -> CommitResult:
    graph = result.graph
    now = django_timezone.now()
    created_count = 0
    sessions_changed = 0
    sessions_matched = 0
    sessions_generated = 0
    change_entries: dict[tuple[str, UUID], ChangeEntry] = {}
    independent_terms: set[str] = set()
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

    for teacher_id in sorted(scope_bumps, key=str):
        teacher = locked_teachers.get(teacher_id)
        if teacher is None:
            continue
        teacher.assignment_scope_version += 1
        teacher.save(update_fields=["assignment_scope_version", "updated_at"])

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

    for rule_obj in graph.rules.values():
        if rule_obj.state == NEW:
            persist_new(rule_obj)
            teacher_ids = list(rule_obj.metadata.get("teacher_ids", ()))
            if teacher_ids:
                rule_obj.data.teachers.set(teacher_ids)
            make_identity(batch, rule_obj, rule_obj.data.pk)
            created_count += 1

    terms_with_new_rules = {
        obj.metadata.get("term_code")
        for obj in graph.rules.values()
        if obj.state == NEW and obj.metadata.get("term_code")
    }
    for term_code in sorted(terms_with_new_rules):
        term = locked_terms.get(term_code)
        if term is None:
            continue
        generation = generate_sessions(term)
        sessions_generated += generation.created

    generated_sessions_by_key: dict[tuple[UUID, date, int], ClassSession] = {}
    term_ids = [term.pk for term in locked_terms.values()]
    if term_ids:
        for session in ClassSession.objects.filter(offering__term_id__in=term_ids, rule__isnull=False):
            generated_sessions_by_key[(session.rule_id, session.origin_teaching_date, session.origin_period)] = session

    for session_obj in graph.sessions.values():
        if session_obj.state != NEW:
            continue
        if session_obj.generated_session:
            planned_key = session_obj.planned_key
            if planned_key is None:
                raise RuntimeError("规则课次缺少生成键，无法绑定。")
            term_code, rule_code, origin_date, origin_period = planned_key
            rule_obj = graph.rules.get((term_code, rule_code))
            if rule_obj is None:
                raise RuntimeError("规则课次找不到对应规则。")
            generation_key = (rule_obj.data.pk, origin_date, origin_period)
            session = generated_sessions_by_key.get(generation_key)
            if session is None:
                session = (
                    ClassSession.objects.filter(
                        rule_id=rule_obj.data.pk,
                        origin_teaching_date=origin_date,
                        origin_period=origin_period,
                    )
                    .order_by("id")
                    .first()
                )
            if session is None:
                raise RuntimeError(f"规则课次 {planned_key} 生成后未找到对应数据库行。")
            make_identity(batch, session_obj, session.pk)
            sessions_matched += 1
            if apply_generated_session_values(session=session, desired=session_obj.data, actor=actor):
                sessions_changed += 1
                queue_change(
                    change_entries,
                    ChangeEntry(
                        entity_type=ChangeEntityType.SESSION,
                        entity_id=session.pk,
                        version=session.version,
                        offering_id=session.offering_id,
                    ),
                )
            continue

        persist_new(session_obj)
        make_identity(batch, session_obj, session_obj.data.pk)
        created_count += 1
        teacher_ids = list(session_obj.metadata.get("teacher_ids", ()))
        if teacher_ids:
            SessionTeacher.objects.bulk_create(
                [SessionTeacher(session_id=session_obj.data.pk, teacher_id=teacher_id) for teacher_id in teacher_ids]
            )
        queue_change(
            change_entries,
            ChangeEntry(
                entity_type=ChangeEntityType.SESSION,
                entity_id=session_obj.data.pk,
                version=session_obj.data.version,
                offering_id=session_obj.data.offering_id,
            ),
        )
        term_code = session_obj.metadata.get("term_code")
        if term_code:
            independent_terms.add(term_code)

    for term_code in sorted(independent_terms):
        term = locked_terms.get(term_code)
        if term is None:
            continue
        # Generation may already have advanced the same row in this transaction.
        term.refresh_from_db(fields=["schedule_version"])
        term.schedule_version += 1
        term.save(update_fields=["schedule_version", "updated_at"])
        queue_change(
            change_entries,
            ChangeEntry(
                entity_type=ChangeEntityType.TERM,
                entity_id=term.pk,
                version=term.schedule_version,
            ),
        )

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

    for term_code in sorted(term_name_changes):
        term = locked_terms.get(term_code)
        if term is None:
            continue
        term.refresh_from_db(fields=["schedule_version"])
        queue_change(
            change_entries,
            ChangeEntry(
                entity_type=ChangeEntityType.TERM,
                entity_id=term.pk,
                version=term.schedule_version,
            ),
        )

    if teacher_name_changes:
        add_session_display_changes(
            change_entries,
            ClassSession.objects.filter(session_teachers__teacher_id__in=teacher_name_changes).distinct(),
        )
    if course_name_changes:
        add_session_display_changes(
            change_entries,
            ClassSession.objects.filter(offering__course_id__in=course_name_changes),
        )
    if class_name_changes:
        add_session_display_changes(
            change_entries,
            ClassSession.objects.filter(offering__teaching_class_id__in=class_name_changes),
        )
    if classroom_display_changes:
        add_session_display_changes(
            change_entries,
            ClassSession.objects.filter(classroom_id__in=classroom_display_changes),
        )

    # Term name changes are already queued with the current schedule_version.
    # If an independent session also bumped that term, the later queue entry
    # above replaced it with the new version.
    if change_entries:
        record_changes(batch.school_id, list(change_entries.values()))

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
        sessions_generated=sessions_generated,
        sessions_matched=sessions_matched,
        sessions_changed=sessions_changed,
        unchanged=unchanged,
    )


def commit_batch(
    batch: ImportBatch,
    actor,
    *,
    generate_sessions=generate_sessions_for_term,
    school_date: date | None = None,
) -> CommitResult:
    """Commit one previewed batch in a single transaction."""
    from apps.imports.preview import build_preview

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

            baseline_counts = copy.deepcopy(locked_batch.row_counts)
            baseline_errors = copy.deepcopy(locked_batch.errors_json)
            baseline_base = locked_batch.preview_base_schedule_version
            baseline_file_hash = locked_batch.file_hash
            baseline_fingerprint = locked_batch.preview_fingerprint

            preview = build_preview(locked_batch, persist=False, school_date=school_date)
            preview_issue_dicts = [issue.as_dict() for issue in preview.issues]
            if (
                preview.row_counts != baseline_counts
                or preview_issue_dicts != baseline_errors
                or preview.preview_base_schedule_version != baseline_base
                or preview.file_hash != baseline_file_hash
                or preview.fingerprint != baseline_fingerprint
            ):
                return stale_result(locked_batch, preview, baseline_errors)
            if preview.has_errors:
                raise ImportCommitError("PREVIEW_HAS_ERRORS", "重新预览发现错误，整批不能提交。")

            return commit_plan(locked_batch, actor, preview, generate_sessions=generate_sessions)
    except ImportCommitError:
        raise
    except Exception as exc:
        mark_failed(batch, exc)
        raise ImportCommitError("COMMIT_FAILED", f"导入提交失败，已整体回滚：{exc}") from exc
