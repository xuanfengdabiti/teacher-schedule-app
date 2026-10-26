"""Shared fixtures for B20 teaching-note tests."""

from __future__ import annotations

import pytest

from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests.demo_data import DemoTermData, build_demo_term


@pytest.fixture
def demo_term(db) -> DemoTermData:
    return build_demo_term()


@pytest.fixture
def generated_demo(demo_term) -> DemoTermData:
    generate_sessions_for_term(demo_term.term)
    return demo_term


@pytest.fixture
def plain_demo(demo_term) -> DemoTermData:
    """Generate without the sample cancel/replace exceptions.

    A10 needs literal 2026-10-05 and 2026-10-19 Monday sessions.  The shared
    demo data moves 10-05 to 10-10, so this test-only variant removes the two
    exceptions before generation; all other fixture data stays identical.
    """
    demo_term.term.calendar_exceptions.all().delete()
    generate_sessions_for_term(demo_term.term)
    return demo_term
