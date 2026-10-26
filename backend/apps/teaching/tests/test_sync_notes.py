"""B20 sync integration: private drafts, shared snapshots and cursor movement."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.academics.models import AssignmentRole, TeachingAssignment
from apps.scheduling.models import ClassSession
from apps.scheduling.tests import factories
from apps.teaching.models import TeachingNote
from apps.teaching.tests.helpers import (
    FIXED_NOW,
    auth_client,
    link_user,
    publish_note,
    save_note,
    session_for,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _bootstrap(client):
    with patch("django.utils.timezone.now", return_value=FIXED_NOW):
        return client.get("/api/v1/sync/bootstrap/")


def _changes(client, cursor: str):
    with patch("django.utils.timezone.now", return_value=FIXED_NOW):
        return client.get("/api/v1/sync/changes/", {"cursor": cursor})


def _bootstrap_query_count(client) -> int:
    with CaptureQueriesContext(connection) as captured:
        response = _bootstrap(client)
    assert response.status_code == 200
    return len(captured)


class TestNoteChangeSync:
    def test_author_gets_draft_others_wait_for_publish_and_cursors_advance(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        user1 = link_user(demo.teachers["0001"], "sync-note-0001@example.invalid")
        user2 = link_user(demo.teachers["0002"], "sync-note-0002@example.invalid")
        client1 = auth_client(user1)
        client2 = auth_client(user2)

        saved = save_note(
            client2,
            session,
            covered="同步私有草稿",
            unfinished="未完成",
            next_plan="下次",
            base_version=0,
            client_mutation_id="sync-draft-1",
        )
        assert saved.status_code == 200

        own_draft = _changes(client2, "41")
        assert own_draft.status_code == 200
        assert own_draft.data["next_cursor"] == "42"
        assert len(own_draft.data["notes"]) == 1
        own_note = own_draft.data["notes"][0]
        assert own_note["session_id"] == str(session.id)
        assert own_note["draft"]["covered"] == "同步私有草稿"
        assert own_note["published"] is None

        other_draft = _changes(client1, "41")
        assert other_draft.status_code == 200
        assert other_draft.data["next_cursor"] == "42"
        assert other_draft.data["notes"] == []
        assert "同步私有草稿" not in json.dumps(other_draft.data, ensure_ascii=False)

        published = publish_note(
            client2,
            session,
            base_version=1,
            client_mutation_id="sync-publish-1",
        )
        assert published.status_code == 200

        other_published = _changes(client1, "42")
        assert other_published.status_code == 200
        assert other_published.data["next_cursor"] == "43"
        assert len(other_published.data["notes"]) == 1
        shared = other_published.data["notes"][0]
        assert shared["draft"] is None
        assert shared["published"]["covered"] == "同步私有草稿"
        assert shared["published_version"] == 2

        edited = save_note(
            client2,
            session,
            covered="发布后的新草稿",
            unfinished="未完成 2",
            next_plan="下次 2",
            base_version=2,
            client_mutation_id="sync-draft-2",
        )
        assert edited.status_code == 200

        other_after_draft = _changes(client1, "43")
        assert other_after_draft.status_code == 200
        assert other_after_draft.data["next_cursor"] == "44"
        assert other_after_draft.data["notes"] == []
        assert "发布后的新草稿" not in json.dumps(other_after_draft.data, ensure_ascii=False)

        own_after_edit = _changes(client2, "43")
        assert own_after_edit.status_code == 200
        assert own_after_edit.data["next_cursor"] == "44"
        assert own_after_edit.data["notes"][0]["draft"]["covered"] == "发布后的新草稿"
        assert own_after_edit.data["notes"][0]["published"]["covered"] == "同步私有草稿"

        own_bootstrap = _bootstrap(client2)
        assert own_bootstrap.status_code == 200
        assert len(own_bootstrap.data["notes"]) == 1
        assert own_bootstrap.data["notes"][0]["draft"]["covered"] == "发布后的新草稿"

        other_bootstrap = _bootstrap(client1)
        assert other_bootstrap.status_code == 200
        assert len(other_bootstrap.data["notes"]) == 1
        assert other_bootstrap.data["notes"][0]["draft"] is None
        assert other_bootstrap.data["notes"][0]["published"]["covered"] == "同步私有草稿"

    def test_unreadable_same_class_note_does_not_enter_bootstrap(self, generated_demo) -> None:
        demo = generated_demo
        session = session_for(demo, "R-CRIME-EVEN", date(2026, 10, 26))
        data_only = factories.make_teacher(demo.school, "SYNC-DATA", "同步只教数据分析教师")
        TeachingAssignment.objects.create(
            offering=demo.offerings["OFF-DATA-2026A"],
            teacher=data_only,
            role=AssignmentRole.TEACHER,
            effective_from=demo.term.start_date,
            effective_until=demo.term.end_date,
        )
        user_data = link_user(data_only, "sync-data-only@example.invalid")
        client_data = auth_client(user_data)
        user2 = link_user(demo.teachers["0002"], "sync-shared@example.invalid")
        client2 = auth_client(user2)
        save_note(
            client2,
            session,
            covered="刑侦共享正文",
            unfinished="",
            next_plan="",
            base_version=0,
            client_mutation_id="sync-shared-draft",
        )
        published = publish_note(
            client2,
            session,
            base_version=1,
            client_mutation_id="sync-shared-publish",
        )
        assert published.status_code == 200

        response = _bootstrap(client_data)

        assert response.status_code == 200
        assert response.data["notes"] == []
        assert "刑侦共享正文" not in json.dumps(response.data, ensure_ascii=False)


class TestBootstrapNotes:
    def test_bootstrap_note_query_count_is_constant(self, generated_demo) -> None:
        demo = generated_demo
        user2 = link_user(demo.teachers["0002"], "sync-count-0002@example.invalid")
        client = auth_client(user2)
        teacher = demo.teachers["0002"]
        before = _bootstrap_query_count(client)
        assert _bootstrap(client).data["notes"] == []

        sessions = list(ClassSession.objects.filter(session_teachers__teacher=teacher).order_by("start_at")[:5])
        assert len(sessions) == 5
        for index, session in enumerate(sessions):
            TeachingNote.objects.create(
                session=session,
                author=teacher,
                draft_covered=f"查询数草稿 {index}",
                version=1,
            )
        after = _bootstrap_query_count(client)
        body = _bootstrap(client).data
        assert len(body["notes"]) == 5
        assert all(note["draft"] is not None for note in body["notes"])
        print(f"bootstrap queries with 0 and 5 notes: {before} -> {after}")
        assert before == after

    def test_reset_payload_contains_empty_notes(self, generated_demo) -> None:
        user2 = link_user(generated_demo.teachers["0002"], "sync-reset-note@example.invalid")

        response = _changes(auth_client(user2), "not-a-cursor")

        assert response.status_code == 200
        assert response.data["reset_required"] is True
        assert response.data["notes"] == []
