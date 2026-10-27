"""Short "last note" previews attached to timetable session summaries.

This is a read-only convenience for the schedule cards: when a teacher saved a
note on one session, the next session of the *same offering* shows a short
reminder.  It intentionally does not reuse ``previous_completed_session``: a
card hint must not require the earlier class to be marked completed, and it
must never replace the B20 "last completed record" contract.

Visibility follows the note rules already used elsewhere:

* the viewer's own note is readable from the saved draft;
* another author's note is only readable from its published snapshot.

Only the short preview string is prepared here; the database keeps the full
note text and no version/ID is exposed through the schedule API.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import Q

from apps.academics.models import TeacherProfile
from apps.scheduling.models import ClassSession, SessionStatus
from apps.scheduling.selectors import active_assignments_for_teacher
from apps.teaching.models import TeachingNote

PREVIEW_MAX_CHARS = 80
_WHITESPACE = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WHITESPACE.sub(" ", text).strip()


def _note_text(
    *,
    author_id: UUID,
    viewer_id: UUID,
    draft_covered: str | None,
    draft_unfinished: str | None,
    draft_next_plan: str | None,
    published_covered: str | None,
    published_unfinished: str | None,
    published_next_plan: str | None,
) -> str:
    """Pick the first non-empty section, preferring ``next_plan``.

    The viewer's own draft wins over their published snapshot.  Another
    author's draft columns are never read here, even if they are present in
    the database row.
    """
    if author_id == viewer_id:
        sections = (draft_next_plan, draft_unfinished, draft_covered)
        if any(_clean(value) for value in sections):
            return next(_clean(value) for value in sections if _clean(value))
    sections = (published_next_plan, published_unfinished, published_covered)
    return next((_clean(value) for value in sections if _clean(value)), "")


def _shorten(text: str) -> str:
    if len(text) <= PREVIEW_MAX_CHARS:
        return text
    return text[:PREVIEW_MAX_CHARS].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class _NoteRow:
    session_id: UUID
    offering_id: UUID
    start_at: datetime
    author_id: UUID
    published_at: datetime | None
    text: str


def _load_note_rows(
    teacher: TeacherProfile,
    *,
    offering_ids: Iterable[UUID],
    before: datetime,
) -> list[_NoteRow]:
    """One query for every note that can appear on any target card.

    Rows are narrowed to earlier, non-cancelled sessions of the relevant
    offerings; per-card selection then happens in memory so the list endpoint
    never issues a query per card.
    """
    rows = (
        TeachingNote.objects.filter(
            session__offering_id__in=list(offering_ids),
            session__start_at__lt=before,
        )
        .exclude(session__status=SessionStatus.CANCELLED)
        .filter(
            # Same readability rule as B20: own notes (draft or published), and
            # other authors' notes only through a published snapshot that this
            # teacher may read (offering assignment or actual session teacher).
            Q(author=teacher)
            | (
                Q(published_at__isnull=False)
                & (
                    Q(session__offering_id__in=active_assignments_for_teacher(teacher).values("offering_id"))
                    | Q(session__session_teachers__teacher=teacher)
                )
            )
        )
        .distinct()
        .values(
            "session_id",
            "session__offering_id",
            "session__start_at",
            "author_id",
            "published_at",
            "draft_covered",
            "draft_unfinished",
            "draft_next_plan",
            "published_covered",
            "published_unfinished",
            "published_next_plan",
        )
    )
    result: list[_NoteRow] = []
    for row in rows:
        text = _note_text(
            author_id=row["author_id"],
            viewer_id=teacher.id,
            draft_covered=row["draft_covered"],
            draft_unfinished=row["draft_unfinished"],
            draft_next_plan=row["draft_next_plan"],
            published_covered=row["published_covered"],
            published_unfinished=row["published_unfinished"],
            published_next_plan=row["published_next_plan"],
        )
        if not text:
            continue
        result.append(
            _NoteRow(
                session_id=row["session_id"],
                offering_id=row["session__offering_id"],
                start_at=row["session__start_at"],
                author_id=row["author_id"],
                published_at=row["published_at"],
                text=text,
            )
        )
    return result


def previous_note_previews(
    teacher: TeacherProfile,
    sessions: list[ClassSession],
) -> dict[UUID, str]:
    """Return ``{target_session_id: short_preview}`` with one note query.

    A target finds the nearest earlier non-cancelled session of its own
    offering that has a readable, non-empty note.  On that source session the
    viewer's own note wins; otherwise the latest shared snapshot is used.
    Cancelled targets and offerings without earlier notes get an empty string.
    """
    targets = [session for session in sessions if session.status != SessionStatus.CANCELLED]
    if not targets:
        return {}
    offering_ids = {session.offering_id for session in targets}
    latest_start = max(session.start_at for session in targets)
    rows = _load_note_rows(teacher, offering_ids=offering_ids, before=latest_start)

    by_offering: dict[UUID, dict[UUID, list[_NoteRow]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_offering[row.offering_id][row.session_id].append(row)

    previews: dict[UUID, str] = {}
    for target in targets:
        sessions_of_offering = by_offering.get(target.offering_id, {})
        candidates = [group for group in sessions_of_offering.values() if group[0].start_at < target.start_at]
        candidates.sort(key=lambda group: (group[0].start_at, group[0].session_id), reverse=True)
        preview = ""
        for group in candidates:
            own = [row for row in group if row.author_id == teacher.id]
            if own:
                preview = own[0].text
                break
            shared = sorted(
                (row for row in group if row.published_at is not None),
                key=lambda row: (row.published_at, row.author_id),
                reverse=True,
            )
            if shared:
                preview = shared[0].text
                break
        previews[target.id] = _shorten(preview) if preview else ""
    return previews
