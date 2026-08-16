"""Schema for the doctor preflight a run records.

Its own module rather than another entry in ``migrations.py``, which sits four
lines under the organization gate's ceiling — see ``migrations_git_boundary`` for
the same reasoning applied to the boundary columns.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_agent_run_preflight(conn: Connection) -> None:
    """Record which doctor checks failed before this run was dispatched.

    Only the failures, as a JSON array of check ids. Storing every result would
    write seven rows' worth of "fine" per dispatch to record the one case anybody
    queries, and the empty array is the answer for a healthy environment.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "start_preflight_failures_json": (
                "ALTER TABLE agent_runs ADD COLUMN start_preflight_failures_json TEXT NOT NULL "
                "DEFAULT '[]'"
            ),
        },
    )


def m_orchestration_run_lease(conn: Connection) -> None:
    """A liveness signal an absent owner cannot fake.

    `OrchestrationRun` carried no owner, pid, heartbeat or deadline, so
    `_occupant_is_live` had to answer "is this still running?" with
    `status in LIVE_ORCHESTRATION_STATUSES` — a field only the owner moves. An
    external harness that walked away therefore held its lane permanently, not
    until restart. `last_seen_at` is stamped by any control-plane write naming
    the run, so the lease is renewed by the work itself rather than by a human
    vouching for it.

    Null on existing rows and read as "never renewed", which falls back to the
    run's own start time — an abandoned run from before this migration is
    reclaimable on the first sweep rather than needing a backfill.
    """
    add_columns_if_missing(
        conn,
        "orchestration_runs",
        {
            "last_seen_at": "ALTER TABLE orchestration_runs ADD COLUMN last_seen_at TEXT",
        },
    )


def m_agent_run_lease(conn: Connection) -> None:
    """A liveness signal for agent runs, mirroring `orchestration_runs`.

    `RunStatus.RUNNING` is committed before a subprocess exists, so an in-flight
    status alone proved nothing and `fail_interrupted_runs` never tried to test
    it — it fails every in-flight run, which is sound only at startup. Stamped
    by the thread supervising the run, so the lease measures the supervisor
    rather than the agent's output.

    Null on existing rows, read as "never renewed", which falls back to the
    run's own start time — so a row written before this migration is judged
    rather than exempt, and needs no backfill.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "last_seen_at": "ALTER TABLE agent_runs ADD COLUMN last_seen_at TEXT",
        },
    )


def m_agent_run_process_identity(conn: Connection) -> None:
    """The detached agent's pid, and a fingerprint pid reuse cannot fake.

    317 lets a run outlive the process that spawned it, at which point "is this
    pid alive?" stops being a useful question — the number may since have been
    handed to something else. `agent_pid_identity` holds the process start time
    so a later reattach can tell "still mine" from "same number, different
    process".

    Null / empty on existing rows and read as "no identity", which
    `process_identity.still_running` treats as not-running rather than falling
    back to a bare liveness check.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "agent_pid": "ALTER TABLE agent_runs ADD COLUMN agent_pid INTEGER",
            "agent_pid_identity": (
                "ALTER TABLE agent_runs ADD COLUMN agent_pid_identity TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def m_agent_slot_number_unique(conn: Connection) -> None:
    """One row per slot number, enforced rather than assumed.

    `initialize_slots` reads the pool, works out which numbers are missing and
    inserts them. Two callers doing that against an empty pool both see nothing
    and both insert a full set, so a limit of three becomes six slots and the
    admission gate stops bounding anything. Nothing in the schema said otherwise.

    Existing duplicates are collapsed before the index goes on, keeping the row
    that is occupied — dropping a slot with a live orchestration in it would
    strand that run's lane with no record of what held it.
    """
    if not table_exists(conn, "agent_slots"):
        # A database built from a partial schema has no pool yet; the model
        # carries the constraint, so a later create_all makes it for free.
        return

    conn.execute(
        text(
            """
            DELETE FROM agent_slots
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY slot_number
                               ORDER BY is_available ASC, assigned_at DESC, id ASC
                           ) AS rank
                    FROM agent_slots
                ) ranked
                WHERE rank = 1
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_slots_slot_number ON agent_slots(slot_number)"
        )
    )
