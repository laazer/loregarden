"""Stored diff artifacts that show a deleted file's `+++ /dev/null` header as a line.

The diff parser once read lines by prefix, so a deleted file's `+++ /dev/null`
header became an added row reading `++ /dev/null`, ahead of the file's first
hunk header. A stored artifact is reused until the ticket's next run replaces
it, so finished tickets kept that row. A real added line always follows a hunk
header, so the row is recognisable by its place and can be dropped; a deleted
file adds nothing, so its count becomes 0.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import Any

from loregarden.models.domain import Artifact, ArtifactKind, InvariantReentry
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.unified_diff import DiffRow
from sqlmodel import Session, col, select

_HEADER_TEXT = "++ /dev/null"


def _is_header_row(section: dict[str, Any]) -> bool:
    lines = section.get("lines") or []
    return (
        len(lines) >= 2
        and lines[0].get("type") == DiffRow.ADDED
        and lines[0].get("text") == _HEADER_TEXT
        and lines[1].get("type") == DiffRow.HUNK
    )


def _bad_sections(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [section for section in content.get("sections") or [] if _is_header_row(section)]


def diffs_showing_a_deleted_file_header(session: Session) -> list[Violation]:
    """`entered_at` is the row's `created_at`, which every rewrite resets."""
    rows = session.exec(
        select(Artifact).where(
            Artifact.kind == ArtifactKind.DIFF,
            col(Artifact.content_json).contains(_HEADER_TEXT),
        )
    ).all()
    violations: list[Violation] = []
    for artifact in rows:
        bad = _bad_sections(json.loads(artifact.content_json or "{}"))
        if bad:
            violations.append(
                Violation(
                    subject_id=artifact.id,
                    ticket_id=artifact.ticket_id,
                    entered_at=artifact.created_at,
                    detail="deleted files shown with a '++ /dev/null' line: "
                    + ", ".join(str(section.get("path")) for section in bad),
                )
            )
    return violations


def drop_deleted_file_header_rows(session: Session, violations: Sequence[Violation]) -> int:
    repaired = 0
    for violation in violations:
        artifact = session.get(Artifact, violation.subject_id)
        if artifact is None:
            continue  # silent-ok: deleted since detection; nothing is left to show the row
        content = json.loads(artifact.content_json or "{}")
        bad = _bad_sections(content)
        if not bad:
            continue  # silent-ok: replaced since detection by a capture from the fixed parser
        for section in bad:
            section["lines"] = section["lines"][1:]
            section["add"] = 0
        artifact.content_json = json.dumps(content)
        session.add(artifact)
        repaired += 1
    session.commit()
    return repaired


DIFFS_SHOW_NO_DELETED_FILE_HEADER = Invariant(
    name="diffs_show_no_deleted_file_header",
    ticket="lg-verdict-outcome-2756",
    statement="no stored diff shows a deleted file's '+++ /dev/null' header as an added line",
    detect=diffs_showing_a_deleted_file_header,
    repair=drop_deleted_file_header_rows,
    reentry=InvariantReentry.DEFECT,
    # The day the fixed parser lands; diffs stored before it are backlog.
    since=date(2026, 11, 27),
    prevented_by=(
        "server/tests/test_unified_diff.py"
        "::test_a_deleted_file_with_a_space_in_its_name_has_only_its_deletions"
    ),
)
