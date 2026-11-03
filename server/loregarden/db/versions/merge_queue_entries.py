"""The merge queue's entry table (lg-milestone-that-968).

See `models.domain.merge_queue_tables`. Starts empty. Each statement guards
itself, so the migration is a no-op on a database that already has the table
or any of its indexes, and a partial build with only some of them is completed
rather than failed.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

#: The live-state predicate of the duplicate-head index, spelled as the model
#: spells it: every state but the four terminal ones.
_LIVE = "state NOT IN ('merged', 'failed', 'timed_out', 'withdrawn')"

_INDEXES = {
    "ix_merge_queue_entries_workspace_id": (
        "CREATE INDEX ix_merge_queue_entries_workspace_id ON merge_queue_entries (workspace_id)"
    ),
    "ix_merge_queue_entries_ticket_id": (
        "CREATE INDEX ix_merge_queue_entries_ticket_id ON merge_queue_entries (ticket_id)"
    ),
    "ix_merge_queue_entries_state": (
        "CREATE INDEX ix_merge_queue_entries_state ON merge_queue_entries (state)"
    ),
    "uq_merge_queue_live_head": (
        "CREATE UNIQUE INDEX uq_merge_queue_live_head "
        f"ON merge_queue_entries (workspace_id, head_sha) WHERE {_LIVE}"
    ),
}


@migration("20261103_merge_queue_entries", after="20261028_done_ticket_ship_probe")
def m_merge_queue_entries(conn: Connection) -> None:
    if not table_exists(conn, "merge_queue_entries"):
        conn.execute(
            text(
                "CREATE TABLE merge_queue_entries ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "workspace_id VARCHAR NOT NULL REFERENCES workspaces(id), "
                "ticket_id VARCHAR NOT NULL REFERENCES tickets(id), "
                "pr_number INTEGER NOT NULL, "
                "pr_url VARCHAR NOT NULL DEFAULT '', "
                "pr_head_branch VARCHAR NOT NULL DEFAULT '', "
                "source_branch VARCHAR NOT NULL DEFAULT '', "
                "head_sha VARCHAR NOT NULL, "
                "base_branch VARCHAR NOT NULL DEFAULT 'main', "
                "position INTEGER NOT NULL, "
                "state VARCHAR NOT NULL DEFAULT 'queued', "
                "wait VARCHAR NOT NULL DEFAULT '', "
                "attempts INTEGER NOT NULL DEFAULT 0, "
                "staged_sha VARCHAR NOT NULL DEFAULT '', "
                "staged_tree VARCHAR NOT NULL DEFAULT '', "
                "staged_base_sha VARCHAR NOT NULL DEFAULT '', "
                "merge_commit VARCHAR NOT NULL DEFAULT '', "
                "landed_tree VARCHAR NOT NULL DEFAULT '', "
                "detail VARCHAR NOT NULL DEFAULT '', "
                "conflicted_files_json VARCHAR NOT NULL DEFAULT '[]', "
                "checks_json VARCHAR NOT NULL DEFAULT '[]', "
                "log_tail VARCHAR NOT NULL DEFAULT '', "
                "artifact_ref VARCHAR NOT NULL DEFAULT '', "
                "enqueued_at DATETIME NOT NULL, "
                "state_changed_at DATETIME NOT NULL, "
                "testing_started_at DATETIME, "
                "CONSTRAINT uq_merge_queue_position UNIQUE (workspace_id, position))"
            )
        )
    for name, ddl in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(ddl))
