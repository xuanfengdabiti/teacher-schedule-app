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
from apps.scheduling.services import generate_sessions_for_term

# Keep the imported callable as a module attribute so existing integrations can
# patch ``apps.imports.services.generate_sessions_for_term`` in tests.


__all__ = [
    "CommitResult",
    "ImportCommitError",
    "PreviewResult",
    "build_preview",
    "commit_batch",
    "preview_batch",
]


def commit_batch(batch, actor, *, school_date=None) -> CommitResult:
    """Commit through the patchable scheduling generation entry point."""
    return _commit_batch(
        batch,
        actor,
        generate_sessions=generate_sessions_for_term,
        school_date=school_date,
    )
