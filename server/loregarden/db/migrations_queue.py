"""Migrations that reshape the execution queue's own tables.

Split out of `migrations.py` for the same reason `migrations_templates.py` was:
that module is at the organization gate's line limit, and new code in a module
already at its cap belongs in a new one. The division here is by subject —
`agent_slots` and `queued_runs` are the scheduler's state, not the domain's.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

from loregarden.db.migration_utils import (
    add_columns_if_missing,
    relax_not_null,
    table_exists,
)
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


def m_per_slot_queues(conn: Connection) -> None:
    """Give every slot its own waiting line, and let a lane hold an orchestration.

    The board's slots were lanes you could put one ticket in; behind them sat a
    single shared line, so "queue this behind that one" had no way to be said.
    A lane is now a serial pipeline: `queued_runs.slot_number` is which lane an
    entry belongs to, and `position` orders it within that lane rather than
    across the whole queue.

    Two other columns follow from a lane running a whole ticket rather than one
    stage. A lane is occupied for the life of an *orchestration*, which spans
    many agent runs, so both the slot and the queue entry need to name one —
    and `queued_runs.run_id` stops being required, because an entry that has
    not started yet has no run behind it at all. `auto_approve` and
    `stop_at_stage_key` ride along too: the dialog that sets them is long gone
    by the time a lane reaches the entry, so the entry has to carry them.

    Existing entries land in lane 1 and keep their relative order: they were
    written against one global line, and lane 1 is the honest reading of that.
    """
    if table_exists(conn, "queued_runs"):
        add_columns_if_missing(
            conn,
            "queued_runs",
            {
                "slot_number": (
                    "ALTER TABLE queued_runs ADD COLUMN slot_number INTEGER NOT NULL DEFAULT 1"
                ),
                "orchestration_run_id": (
                    "ALTER TABLE queued_runs ADD COLUMN orchestration_run_id TEXT "
                    "REFERENCES orchestration_runs(id)"
                ),
                "auto_approve": (
                    "ALTER TABLE queued_runs ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0"
                ),
                "stop_at_stage_key": (
                    "ALTER TABLE queued_runs ADD COLUMN stop_at_stage_key TEXT NOT NULL DEFAULT ''"
                ),
            },
        )
        relax_not_null(conn, "queued_runs", "run_id")
        conn.execute(text("UPDATE queued_runs SET slot_number = 1 WHERE slot_number IS NULL"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_queued_runs_slot_number ON queued_runs (slot_number)"
            )
        )

    if table_exists(conn, "agent_slots"):
        add_columns_if_missing(
            conn,
            "agent_slots",
            {
                "current_orchestration_run_id": (
                    "ALTER TABLE agent_slots ADD COLUMN current_orchestration_run_id TEXT "
                    "REFERENCES orchestration_runs(id)"
                ),
            },
        )


def m_lane_entry_kind(conn: Connection) -> None:
    """Let a lane entry be a single stage, not only a whole ticket.

    Lanes were fed by the queue board, which only ever runs a ticket. Admission
    control feeds them from everywhere else too — the Dashboard, the chat
    primitives, MCP — and "run this one stage" is a real request there. Parking
    it as an orchestration would silently turn it into a much bigger one, so an
    entry now says which it is and, for a stage, which stage.

    Existing entries are all whole-ticket runs, which is the default.
    """
    if not table_exists(conn, "queued_runs"):
        return

    add_columns_if_missing(
        conn,
        "queued_runs",
        {
            "entry_kind": (
                "ALTER TABLE queued_runs ADD COLUMN entry_kind TEXT NOT NULL "
                "DEFAULT 'orchestration'"
            ),
            "stage_key": ("ALTER TABLE queued_runs ADD COLUMN stage_key TEXT NOT NULL DEFAULT ''"),
        },
    )
