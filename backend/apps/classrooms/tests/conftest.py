"""Fixtures for B32 classroom tests."""

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
