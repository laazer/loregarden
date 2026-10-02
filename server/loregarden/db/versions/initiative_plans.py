"""Initiative schedules, and when each ticket was resolved.

``tickets.resolved_at`` is what a schedule forecast measures pace from. It is
backfilled from the best record each resolved ticket has, in order of how
directly it says "this closed then":

1. the latest ``TicketStateChanged`` into ``done``/``wont_do`` — but that event
   only fires for *chosen* closes; a stage-derived close emits nothing, which
   is about half of them;
2. the latest ``TicketLanded`` / ``OrchestrationRunCompleted`` /
   ``StageCompleted`` event for the ticket — the run that finished it;
3. ``updated_at`` — last resort, and noisy (a bulk edit restamps it).

See `models.domain.initiative_tables` for the plan tables.
"""

from __future__ import annotations

from loregarden.db.migration_utils import (
    add_columns_if_missing,
    index_exists,
    table_columns,
    table_exists,
)
from loregarden.db.versions import migration
from sqlalchemy import text
from sqlalchemy.engine import Connection

_RESOLVED = "('done', 'wont_do')"


def _backfill_resolved_at(conn: Connection) -> None:
    sources: list[str] = []
    # A schema old enough to predate the event log, or a minimal one, has less to go on.
    if table_exists(conn, "domain_events"):
        sources += [
            f"""(SELECT MAX(e.created_at) FROM domain_events e
                  WHERE e.ticket_id = tickets.id
                    AND e.type = 'TicketStateChanged'
                    AND json_extract(e.payload_json, '$.state') IN {_RESOLVED})""",
            """(SELECT MAX(e.created_at) FROM domain_events e
                  WHERE e.ticket_id = tickets.id
                    AND e.type IN ('TicketLanded', 'OrchestrationRunCompleted', 'StageCompleted'))""",
        ]
    if "updated_at" in table_columns(conn, "tickets"):
        sources.append("updated_at")
    if not sources:
        return
    conn.execute(
        text(
            f"UPDATE tickets SET resolved_at = COALESCE({', '.join(sources)}, NULL) "  # noqa: S608 — constant fragments, no input
            f"WHERE state IN {_RESOLVED} AND resolved_at IS NULL"
        )
    )


def _create_plan_tables(conn: Connection) -> None:
    if not table_exists(conn, "initiative_plans"):
        conn.execute(
            text(
                """
                CREATE TABLE initiative_plans (
                    initiative_id VARCHAR NOT NULL PRIMARY KEY REFERENCES tickets(id),
                    mode VARCHAR NOT NULL DEFAULT 'fixed',
                    notes VARCHAR NOT NULL DEFAULT '',
                    autopilot BOOLEAN NOT NULL DEFAULT 0,
                    max_parallel INTEGER NOT NULL DEFAULT 3,
                    paused_reason VARCHAR NOT NULL DEFAULT '',
                    autopilot_since DATETIME,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
    if not table_exists(conn, "schedule_targets"):
        conn.execute(
            text(
                """
                CREATE TABLE schedule_targets (
                    ticket_id VARCHAR NOT NULL PRIMARY KEY REFERENCES tickets(id),
                    target_date DATE,
                    plan_order INTEGER NOT NULL DEFAULT 0,
                    updated_by VARCHAR NOT NULL DEFAULT '',
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
    if not table_exists(conn, "schedule_proposals"):
        conn.execute(
            text(
                """
                CREATE TABLE schedule_proposals (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    initiative_id VARCHAR NOT NULL REFERENCES tickets(id),
                    source VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    mode VARCHAR,
                    rationale VARCHAR NOT NULL DEFAULT '',
                    items_json VARCHAR NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )
    if not table_exists(conn, "initiative_planner_messages"):
        conn.execute(
            text(
                """
                CREATE TABLE initiative_planner_messages (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    initiative_id VARCHAR NOT NULL REFERENCES tickets(id),
                    role VARCHAR NOT NULL,
                    content VARCHAR NOT NULL DEFAULT '',
                    status VARCHAR NOT NULL DEFAULT 'complete',
                    turn_mode VARCHAR NOT NULL DEFAULT 'chat',
                    parts_json VARCHAR NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
    if not table_exists(conn, "initiative_autopilot_events"):
        conn.execute(
            text(
                """
                CREATE TABLE initiative_autopilot_events (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    initiative_id VARCHAR NOT NULL REFERENCES tickets(id),
                    action VARCHAR NOT NULL,
                    ticket_id VARCHAR REFERENCES tickets(id),
                    detail VARCHAR NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
    for name, ddl in (
        (
            "ix_initiative_autopilot_events_initiative_id",
            "CREATE INDEX ix_initiative_autopilot_events_initiative_id "
            "ON initiative_autopilot_events (initiative_id)",
        ),
        (
            "ix_initiative_autopilot_events_created_at",
            "CREATE INDEX ix_initiative_autopilot_events_created_at "
            "ON initiative_autopilot_events (created_at)",
        ),
        (
            "ix_initiative_planner_messages_initiative_id",
            "CREATE INDEX ix_initiative_planner_messages_initiative_id "
            "ON initiative_planner_messages (initiative_id)",
        ),
        (
            "ix_initiative_planner_messages_status",
            "CREATE INDEX ix_initiative_planner_messages_status "
            "ON initiative_planner_messages (status)",
        ),
        (
            "ix_schedule_proposals_initiative_id",
            "CREATE INDEX ix_schedule_proposals_initiative_id ON schedule_proposals (initiative_id)",
        ),
        (
            "ix_schedule_proposals_status",
            "CREATE INDEX ix_schedule_proposals_status ON schedule_proposals (status)",
        ),
    ):
        if not index_exists(conn, name):
            conn.execute(text(ddl))


@migration("20261002_initiative_plans", after="0151_ticket_criteria_checked")
def m_initiative_plans(conn: Connection) -> None:
    if table_exists(conn, "tickets"):
        _add_resolved_at(conn)
    _create_plan_tables(conn)


def _add_resolved_at(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "tickets",
        {"resolved_at": "ALTER TABLE tickets ADD COLUMN resolved_at DATETIME"},
    )
    if not index_exists(conn, "ix_tickets_resolved_at"):
        conn.execute(text("CREATE INDEX ix_tickets_resolved_at ON tickets (resolved_at)"))
    _backfill_resolved_at(conn)
