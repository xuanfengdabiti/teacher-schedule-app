"""B33 candidate query count does not grow with the number of sessions."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.inquiries.tests.helpers import (
    candidate_query,
    linked_users,
    make_candidate_sessions,
    user_client,
)

pytestmark = pytest.mark.django_db


def test_candidate_query_count_is_constant(generated_demo):
    demo = generated_demo
    _, users = linked_users(demo)
    make_candidate_sessions(demo, teacher_numbers=("0002",))
    client = user_client(users, "0001")

    with CaptureQueriesContext(connection) as first:
        response = client.get("/api/v1/inquiries/candidates/", candidate_query(demo.classrooms["A301"]))
    assert response.status_code == 200
    first_count = len(first)
    assert len(response.json()["candidates"]) == 1

    make_candidate_sessions(demo, teacher_numbers=("0003", "0004", "0005"))
    with CaptureQueriesContext(connection) as second:
        response = client.get("/api/v1/inquiries/candidates/", candidate_query(demo.classrooms["A301"]))
    assert response.status_code == 200
    assert len(response.json()["candidates"]) == 4
    # 1 candidate session vs 4: 5 SQL statements in both cases (including JWT
    # user/profile authentication).
    assert len(second) == first_count
