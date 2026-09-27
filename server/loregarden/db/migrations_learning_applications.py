"""Migrations for the control-plane memory tables.

`learning_applications` (178) and `memory_health_snapshots` — one id each,
split out the way `migrations_memory_briefings.py` is.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_learning_applications_table(conn: Connection) -> None:
    """Which learnings were surfaced into which run, and how that run ended.

    `outcome` and `settled_at` are nullable on purpose: "not known yet" and
    "concluded with no rung" are facts of their own, and a NOT NULL DEFAULT
    would record them as a rung nobody measured.
    """
    if table_exists(conn, "learning_applications"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE learning_applications (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES agent_runs(id),
                briefing_id TEXT NULL REFERENCES memory_briefings(id),
                node_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                ticket_id TEXT NULL REFERENCES tickets(id),
                stage_key TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                outcome TEXT NULL,
                settled_at TEXT NULL,
                created_at TEXT NOT NULL,
                CONSTRAINT uq_learning_application UNIQUE (run_id, node_id)
            )
            """
        )
    )
    for column in ("run_id", "node_id", "ticket_id", "settled_at"):
        conn.execute(
            text(
                f"CREATE INDEX ix_learning_applications_{column} "
                f"ON learning_applications ({column})"
            )
        )


def m_memory_health_snapshots_table(conn: Connection) -> None:
    """Dated readings of each workspace memory graph's shape. Append-only."""
    if table_exists(conn, "memory_health_snapshots"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE memory_health_snapshots (
                id TEXT PRIMARY KEY,
                workspace_slug TEXT NOT NULL,
                taken_at TEXT NOT NULL,
                learnings INTEGER NOT NULL,
                unlinked INTEGER NOT NULL,
                never_surfaced INTEGER NOT NULL,
                surfaced_unscored INTEGER NOT NULL,
                stale INTEGER NOT NULL,
                contested INTEGER NOT NULL,
                superseded INTEGER NOT NULL,
                discredited INTEGER NOT NULL
            )
            """
        )
    )
    for column in ("workspace_slug", "taken_at"):
        conn.execute(
            text(
                f"CREATE INDEX ix_memory_health_snapshots_{column} "
                f"ON memory_health_snapshots ({column})"
            )
        )
