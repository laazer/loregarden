"""Migrations that reshape the execution queue's own tables.

Split out of `migrations.py` for the same reason `migrations_templates.py` was:
that module is at the organization gate's line limit, and new code in a module
already at its cap belongs in a new one. The division here is by subject —
`agent_slots` and `queued_runs` are the scheduler's state, not the domain's.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

from loregarden.db.migration_utils import relax_not_null, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_global_agent_slots(conn: Connection) -> None:
    """Collapse the per-workspace slot pools into one shared pool.

    A slot models the machine's capacity to run an agent, but the rows were
    keyed by workspace — so two workspaces meant two independent pools of three
    and up to six agents on one box, each pool believing it was the limit.
    Capacity is a property of the machine, so the pool is now global and
    ``workspace_id`` goes null on it. Which workspace a run belongs to is read
    from the run, which is where it was always recorded.

    Free slots are scaffolding and are dropped; ``initialize_slots`` recreates
    the shared pool on the next queue operation. Occupied slots are kept and
    renumbered into it, because deleting them would strand a running agent with
    nothing to release when it finishes. That can leave the pool temporarily
    over capacity — it drains as those runs complete and the surplus is
    reclaimed rather than refilled.
    """
    if not table_exists(conn, "agent_slots"):
        return

    relax_not_null(conn, "agent_slots", "workspace_id")

    conn.execute(text("DELETE FROM agent_slots WHERE is_available = 1"))
    conn.execute(text("UPDATE agent_slots SET workspace_id = NULL"))
    # Renumber the survivors 1..N so the shared pool has no duplicate slot
    # numbers — two workspaces each had a slot 1.
    conn.execute(
        text(
            "UPDATE agent_slots SET slot_number = ("
            "  SELECT COUNT(*) FROM agent_slots older"
            "  WHERE older.rowid <= agent_slots.rowid"
            ")"
        )
    )
