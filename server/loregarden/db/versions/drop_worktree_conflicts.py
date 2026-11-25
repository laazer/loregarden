"""Drop the worktree merge's columns and the conflict reports.

`POST /api/parallel/worktree/{id}/merge` was the only writer of these four
`worktrees` columns and nothing ever wrote `conflict_reports`; the endpoints
that read them are gone. Measured on the live database on 2026-11-24: 659
worktrees, none with a conflict, a summary or `merged_at`, and 0 reports.

The first migration here that removes anything. A build older than it fails,
loudly, on a read that loads a worktree row. The server pulls and reloads
together when idle, so it never runs one against this schema; an older
worktree's CLI may, and already refuses to write. An older build's
`create_all` also brings the empty table back: see `db/retired_tables.py`.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_columns
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_COLUMNS = ("has_conflicts", "conflict_files_json", "conflict_summary", "merged_at")


@migration("20261124_drop_worktree_conflicts", after="20261122_visual_qa_review_kit")
def m_drop_worktree_conflicts(conn: Connection) -> None:
    conn.execute(text("DROP TABLE IF EXISTS conflict_reports"))
    # Empty when there is no `worktrees` table, so this is a no-op then.
    present = table_columns(conn, "worktrees")
    for column in _COLUMNS:
        if column in present:
            conn.execute(text(f"ALTER TABLE worktrees DROP COLUMN {column}"))
