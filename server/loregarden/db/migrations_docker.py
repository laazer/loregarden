"""The docker capacity ledger's tables.

A new module rather than growth in `migrations.py`, which sits near the
organization gate's line cap — the same reasoning that produced
`migrations_queue.py` and `migrations_templates.py`.

The pool singleton is seeded here, in the migration, rather than lazily on the
first claim. `agent_slots` is the cautionary tale: its lazy `initialize_slots`
let two threads each insert a full pool, giving six slots for a limit of three —
the admission gate's whole purpose, doubled silently, and it took a unique index
on `slot_number` to close. A row that exists before any caller can race for it
cannot be created twice.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_docker_capacity_ledger(conn: Connection) -> None:
    """Create `docker_leases` and the one-row `docker_capacity_pool`.

    The ceiling columns start at zero with `ceiling_source = 'unknown'`, which
    is the fail-closed state: until something probes `docker info`, admission
    refuses rather than treating an unmeasured machine as an idle one.
    """
    if not table_exists(conn, "docker_capacity_pool"):
        conn.execute(
            text(
                """
                CREATE TABLE docker_capacity_pool (
                    id TEXT PRIMARY KEY,
                    held_cpus REAL NOT NULL DEFAULT 0,
                    held_memory_mb INTEGER NOT NULL DEFAULT 0,
                    held_count INTEGER NOT NULL DEFAULT 0,
                    ceiling_cpus REAL NOT NULL DEFAULT 0,
                    ceiling_memory_mb INTEGER NOT NULL DEFAULT 0,
                    ceiling_leases INTEGER NOT NULL DEFAULT 0,
                    ceiling_source TEXT NOT NULL DEFAULT 'unknown',
                    probed_at DATETIME,
                    probe_error TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0,
                    next_position INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        )

    # Every column named explicitly, and NOT `INSERT OR IGNORE`. The table is
    # created from the SQLModel definition on a fresh database, where the
    # defaults are Python-side and the columns are NOT NULL — so a partial
    # insert violates a constraint, and `OR IGNORE` would swallow that and leave
    # the pool row missing. It did, on the first cut of this migration: the
    # ledger came up with no pool to claim against and nothing said so.
    # `WHERE NOT EXISTS` makes re-running a no-op without hiding a real error.
    conn.execute(
        text(
            """
            INSERT INTO docker_capacity_pool (
                id, held_cpus, held_memory_mb, held_count,
                ceiling_cpus, ceiling_memory_mb, ceiling_leases,
                ceiling_source, probed_at, probe_error, revision, next_position
            )
            SELECT 'global', 0, 0, 0, 0, 0, 0, 'unknown', NULL, '', 0, 1
            WHERE NOT EXISTS (SELECT 1 FROM docker_capacity_pool WHERE id = 'global')
            """
        )
    )

    seeded = conn.execute(
        text("SELECT COUNT(*) FROM docker_capacity_pool WHERE id = 'global'")
    ).scalar()
    if seeded != 1:
        raise RuntimeError(
            "docker_capacity_pool singleton was not seeded; the ledger would have "
            "no pool to claim against and admission would refuse every request."
        )

    if not table_exists(conn, "docker_leases"):
        conn.execute(
            text(
                """
                CREATE TABLE docker_leases (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    holder_kind TEXT NOT NULL DEFAULT 'ad_hoc',
                    holder_label TEXT NOT NULL DEFAULT '',
                    agent_run_id TEXT REFERENCES agent_runs(id),
                    orchestration_run_id TEXT REFERENCES orchestration_runs(id),
                    ticket_id TEXT REFERENCES tickets(id),
                    workspace_id TEXT REFERENCES workspaces(id),
                    holder_pid INTEGER,
                    footprint TEXT NOT NULL DEFAULT 'custom',
                    cpus REAL NOT NULL DEFAULT 0,
                    memory_mb INTEGER NOT NULL DEFAULT 0,
                    compose_project TEXT NOT NULL DEFAULT '',
                    container_names_json TEXT NOT NULL DEFAULT '[]',
                    position INTEGER NOT NULL DEFAULT 0,
                    ttl_seconds INTEGER NOT NULL DEFAULT 900,
                    requested_at DATETIME NOT NULL,
                    granted_at DATETIME,
                    expires_at DATETIME,
                    last_renewed_at DATETIME,
                    released_at DATETIME,
                    end_reason TEXT,
                    last_probe_at DATETIME,
                    last_probe_outcome TEXT NOT NULL DEFAULT '',
                    last_probe_error TEXT NOT NULL DEFAULT '',
                    running_container_count INTEGER,
                    note TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

    # `(status, position)` serves the promotion walk, `(status, expires_at)` the
    # reap sweep. Both are the whole predicate of a query that runs on a timer.
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_docker_leases_status_position "
            "ON docker_leases (status, position)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_docker_leases_status_expires "
            "ON docker_leases (status, expires_at)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_docker_leases_agent_run ON docker_leases (agent_run_id)"
        )
    )
