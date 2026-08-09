"""Migrations for the stage fan-out group / attempt schema.

Split out of `migrations.py`, which had grown past the organization gate's limit
again. The division follows `migrations_mcp.py`: a cluster that belongs together
moves as a cluster. This one owns `stage_fanout_groups` and
`stage_fanout_attempts`, the two tables backing one stage running as N competing
attempts, and nothing else touches them.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

_INDEXES = {
    "ix_stage_fanout_groups_workspace_id": (
        "CREATE INDEX ix_stage_fanout_groups_workspace_id ON stage_fanout_groups (workspace_id)"
    ),
    "ix_stage_fanout_groups_ticket_stage": (
        "CREATE INDEX ix_stage_fanout_groups_ticket_stage "
        "ON stage_fanout_groups (ticket_id, stage_key)"
    ),
    "ix_stage_fanout_groups_orchestration_run_id": (
        "CREATE INDEX ix_stage_fanout_groups_orchestration_run_id "
        "ON stage_fanout_groups (orchestration_run_id)"
    ),
    "ix_stage_fanout_groups_status": (
        "CREATE INDEX ix_stage_fanout_groups_status ON stage_fanout_groups (status)"
    ),
    "ix_stage_fanout_groups_winner_attempt_id": (
        "CREATE INDEX ix_stage_fanout_groups_winner_attempt_id "
        "ON stage_fanout_groups (winner_attempt_id)"
    ),
    "ix_stage_fanout_attempts_group_id": (
        "CREATE INDEX ix_stage_fanout_attempts_group_id ON stage_fanout_attempts (group_id)"
    ),
    "ix_stage_fanout_attempts_agent_run_id": (
        "CREATE INDEX ix_stage_fanout_attempts_agent_run_id ON stage_fanout_attempts (agent_run_id)"
    ),
    "ix_stage_fanout_attempts_worktree_id": (
        "CREATE INDEX ix_stage_fanout_attempts_worktree_id ON stage_fanout_attempts (worktree_id)"
    ),
    "ix_stage_fanout_attempts_status": (
        "CREATE INDEX ix_stage_fanout_attempts_status ON stage_fanout_attempts (status)"
    ),
    "ix_stage_fanout_attempts_group_index": (
        "CREATE UNIQUE INDEX ix_stage_fanout_attempts_group_index "
        "ON stage_fanout_attempts (group_id, attempt_index)"
    ),
}


def m_stage_fanout_groups(conn: Connection) -> None:
    if not table_exists(conn, "stage_fanout_groups"):
        conn.execute(
            text(
                """
                CREATE TABLE stage_fanout_groups (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    orchestration_run_id TEXT,
                    stage_key TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
                    pre_fanout_workflow_stage_key TEXT NOT NULL DEFAULT '',
                    pre_fanout_workflow_stage_status TEXT NOT NULL DEFAULT 'pending',
                    pre_fanout_stage_map_json TEXT NOT NULL DEFAULT '[]',
                    pre_fanout_next_agent TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    outcome TEXT NOT NULL DEFAULT 'pending',
                    winner_attempt_id TEXT,
                    declined_reason TEXT NOT NULL DEFAULT '',
                    failure_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    settled_at TEXT,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
                    FOREIGN KEY(ticket_id) REFERENCES tickets(id),
                    FOREIGN KEY(orchestration_run_id) REFERENCES orchestration_runs(id)
                )
                """
            )
        )

    if not table_exists(conn, "stage_fanout_attempts"):
        conn.execute(
            text(
                """
                CREATE TABLE stage_fanout_attempts (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    attempt_index INTEGER NOT NULL CHECK (attempt_index >= 0),
                    attempt_name TEXT NOT NULL DEFAULT '',
                    agent_run_id TEXT,
                    worktree_id TEXT,
                    branch TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned',
                    failure_details TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(group_id) REFERENCES stage_fanout_groups(id),
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(id),
                    FOREIGN KEY(worktree_id) REFERENCES worktrees(id),
                    UNIQUE(group_id, attempt_index)
                )
                """
            )
        )

    for name, statement in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(statement))
