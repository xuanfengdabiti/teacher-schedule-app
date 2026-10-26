"""Change-log allocation and generation integration tests (B14)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import close_old_connections, transaction

from apps.scheduling.changelog import ChangeEntry, record_changes
from apps.scheduling.models import ChangeEntityType, ChangeLog, SchoolSyncState
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories


def _entry(version: int = 1) -> ChangeEntry:
    return ChangeEntry(
        entity_type=ChangeEntityType.SESSION,
        entity_id=uuid4(),
        version=version,
    )


@pytest.mark.django_db
class TestGenerationWritesChangeLog:
    def test_demo_generation_writes_40_session_and_one_term_log(self, demo_term) -> None:
        result = generate_sessions_for_term(demo_term.term)

        logs = list(ChangeLog.objects.filter(school=demo_term.school).order_by("change_seq"))
        assert result.created == 40
        assert len(logs) == 41
        assert [log.change_seq for log in logs] == list(range(1, 42))
        session_logs = [log for log in logs if log.entity_type == ChangeEntityType.SESSION]
        term_logs = [log for log in logs if log.entity_type == ChangeEntityType.TERM]
        assert len(session_logs) == 40
        assert len(term_logs) == 1
        assert all(log.version == 1 for log in session_logs)
        assert all(log.offering_id is not None for log in session_logs)
        assert term_logs[0].entity_id == demo_term.term.id
        assert term_logs[0].version == 2
        assert term_logs[0].offering_id is None
        assert SchoolSyncState.objects.get(school=demo_term.school).last_seq == 41

    def test_second_generation_adds_no_logs(self, demo_term) -> None:
        generate_sessions_for_term(demo_term.term)
        logs_before = list(ChangeLog.objects.values_list("change_seq", "entity_id", "version"))
        state_before = SchoolSyncState.objects.get(school=demo_term.school).last_seq

        result = generate_sessions_for_term(demo_term.term)

        assert result.created == 0
        assert list(ChangeLog.objects.values_list("change_seq", "entity_id", "version")) == logs_before
        assert SchoolSyncState.objects.get(school=demo_term.school).last_seq == state_before == 41


@pytest.mark.django_db(transaction=True)
def test_record_changes_outside_transaction_raises_without_writing() -> None:
    school = factories.make_school()

    with pytest.raises(RuntimeError, match="事务"):
        record_changes(school.id, [_entry()])

    assert not ChangeLog.objects.exists()
    assert not SchoolSyncState.objects.exists()


@pytest.mark.django_db
def test_empty_entry_list_returns_current_sequence() -> None:
    school = factories.make_school()
    SchoolSyncState.objects.create(school=school, last_seq=7)

    with transaction.atomic():
        result = record_changes(school.id, [])

    assert result == 7
    assert not ChangeLog.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_record_changes_allocates_disjoint_contiguous_sequences() -> None:
    school = factories.make_school()
    SchoolSyncState.objects.create(school=school)
    barrier = threading.Barrier(2)
    results: list[int] = []

    def worker() -> None:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            with transaction.atomic():
                results.append(record_changes(school.id, [_entry()]))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=20)

    logs = list(ChangeLog.objects.filter(school=school).order_by("change_seq"))
    assert sorted(results) == [1, 2]
    assert [log.change_seq for log in logs] == [1, 2]
    assert len({log.change_seq for log in logs}) == 2
    assert SchoolSyncState.objects.get(school=school).last_seq == 2
