"""Migration for the memory-briefing telemetry table.

Split out of `migrations.py` the same way `migrations_reference.py` owns the
reference cache: one table, one id, nothing else touches it. Identity is the id
string in the MIGRATIONS list.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_memory_briefings_table(conn: Connection) -> None:
    """One row per inherited-wisdom assembly.

    `outcome` is stored rather than derived, so the aggregate over this table
    reports what the classifier decided at write time instead of reclassifying
    old rows under new rules.

    Indexes are deliberately limited to the four foreign/temporal keys the
    aggregate joins and windows on; the table is written once per prompt build
    and read in bulk, so anything else is index maintenance nobody reads.
    """
    if table_exists(conn, "memory_briefings"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE memory_briefings (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES agent_runs(id),
                ticket_id TEXT NULL REFERENCES tickets(id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                stage_key TEXT NOT NULL DEFAULT '',
                assembly_source TEXT NOT NULL DEFAULT 'dispatch',
                outcome TEXT NOT NULL,
                checkpoints_injected INTEGER NOT NULL DEFAULT 0,
                learnings_injected INTEGER NOT NULL DEFAULT 0,
                checkpoints_saturated INTEGER NOT NULL DEFAULT 0,
                learnings_saturated INTEGER NOT NULL DEFAULT 0,
                query_had_terms INTEGER NOT NULL DEFAULT 0,
                chars_injected INTEGER NOT NULL DEFAULT 0,
                pre_truncation_chars INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0,
                store_states_json TEXT NOT NULL DEFAULT '{}',
                store_errors TEXT NOT NULL DEFAULT '',
                elapsed_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    for column in ("run_id", "ticket_id", "workspace_id", "created_at"):
        conn.execute(
            text(f"CREATE INDEX ix_memory_briefings_{column} ON memory_briefings ({column})")
        )
