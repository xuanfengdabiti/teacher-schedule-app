"""Scheduling fixtures shared through the demo-term builder."""

from __future__ import annotations

import pytest

from apps.scheduling.tests.demo_data import DemoTermData, build_demo_term


@pytest.fixture
def demo_term(db) -> DemoTermData:
    return build_demo_term()
