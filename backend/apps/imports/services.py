"""Public import service entry points.

The implementation is split by responsibility:

* :mod:`apps.imports.projection` builds the typed, read-only import graph;
* :mod:`apps.imports.preview` parses, validates and persists preview counts;
* :mod:`apps.imports.commit` writes one previewed batch under the project locks.

This module stays the stable import surface used by Admin and tests.
"""

from __future__ import annotations

from apps.imports.commit import CommitResult, ImportCommitError
from apps.imports.commit import commit_batch as _commit_batch
from apps.imports.preview import PreviewResult, build_preview, preview_batch

__all__ = [
    "CommitResult",
    "ImportCommitError",
    "PreviewResult",
    "build_preview",
    "commit_batch",
    "preview_batch",
]


def commit_batch(batch, actor, *, school_date=None, now=None) -> CommitResult:
    """Commit one previewed batch; ``now`` keeps tests deterministic."""
    return _commit_batch(batch, actor, school_date=school_date, now=now)
