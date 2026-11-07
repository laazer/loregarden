"""Directed findings, and the scope decision on each (lg-milestone-that-1216).

See `models.domain.discovery_tables`. Creates the table and backfills it from
the only record filings had until now: the symmetric ``ticket_relations`` row
`agent_ticket_filing.record_filing` wrote, stamped ``created_by="run:<id>"``.
That row lost the direction (the pair is stored sorted), so it is recovered
from the run: the filing run's own ticket is the source, and the other side is
the ticket it filed.

What was already settled is recorded as decided:

- ``done``: it was worked as its parent's work, so ``follow_up``.
- ``wont_do``: ``drop``.
- open, and another ticket depends on it: whoever linked that dependency
  decided it was work, so ``follow_up``. Holding it out would hold its
  dependent forever.

Every other open finding is undecided. Its parent stops waiting for it, and
its source's next scope round decides it.

Each statement guards itself: the table and indexes are created only if
missing, and the backfill skips a ticket already recorded.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_columns, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_INDEXES = {
    "ix_ticket_discoveries_source_ticket_id": (
        "CREATE INDEX ix_ticket_discoveries_source_ticket_id "
        "ON ticket_discoveries (source_ticket_id)"
    ),
    "ix_ticket_discoveries_agent_run_id": (
        "CREATE INDEX ix_ticket_discoveries_agent_run_id ON ticket_discoveries (agent_run_id)"
    ),
    "ix_ticket_discoveries_orchestration_run_id": (
        "CREATE INDEX ix_ticket_discoveries_orchestration_run_id "
        "ON ticket_discoveries (orchestration_run_id)"
    ),
    "ix_ticket_discoveries_decision": (
        "CREATE INDEX ix_ticket_discoveries_decision ON ticket_discoveries (decision)"
    ),
}

#: One row per filed ticket. The filing run's ticket is the source; the
#: relation's other side is the filed ticket. `INSERT OR IGNORE` against the
#: unique `filed_ticket_id` keeps the earliest filing and makes a re-run a no-op.
_BACKFILL = """
INSERT OR IGNORE INTO ticket_discoveries (
    id, filed_ticket_id, source_ticket_id, agent_run_id, orchestration_run_id,
    filed_at, decision, decided_at, decided_by, decision_reason
)
SELECT
    lower(
        hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-' || hex(randomblob(2)) || '-'
        || hex(randomblob(2)) || '-' || hex(randomblob(6))
    ),
    filed.id,
    ar.ticket_id,
    ar.id,
    ar.orchestration_run_id,
    r.created_at,
    CASE
        WHEN filed.state = 'wont_do' THEN 'drop'
        WHEN filed.state = 'done' OR depended.ticket_id IS NOT NULL THEN 'follow_up'
    END,
    CASE WHEN filed.state IN ('done', 'wont_do') OR depended.ticket_id IS NOT NULL
        THEN filed.updated_at END,
    CASE WHEN filed.state IN ('done', 'wont_do') OR depended.ticket_id IS NOT NULL
        THEN 'backfill' ELSE '' END,
    CASE
        WHEN filed.state = 'wont_do' THEN 'Closed wont_do before scope decisions existed.'
        WHEN filed.state = 'done' THEN 'Worked as its parent''s work before scope decisions existed.'
        WHEN depended.ticket_id IS NOT NULL THEN 'Another ticket already depends on it.'
        ELSE ''
    END
FROM ticket_relations r
JOIN agent_runs ar ON ar.id = substr(r.created_by, 5)
JOIN tickets filed ON filed.id = CASE
    WHEN r.ticket_id = ar.ticket_id THEN r.related_ticket_id ELSE r.ticket_id END
LEFT JOIN (SELECT DISTINCT depends_on_ticket_id AS ticket_id FROM ticket_dependencies) depended
    ON depended.ticket_id = filed.id
WHERE r.created_by LIKE 'run:%' AND ar.ticket_id IS NOT NULL
ORDER BY r.created_at
"""


#: What the backfill reads. A schema missing any of it predates run filings.
_BACKFILL_SOURCES = {
    "ticket_relations": {"ticket_id", "related_ticket_id", "created_by", "created_at"},
    "agent_runs": {"id", "ticket_id", "orchestration_run_id"},
    "tickets": {"id", "state", "updated_at"},
    "ticket_dependencies": {"depends_on_ticket_id"},
}


@migration("20261107_ticket_discoveries", after="20261105_autopilot_timing")
def m_ticket_discoveries(conn: Connection) -> None:
    if not table_exists(conn, "ticket_discoveries"):
        conn.execute(
            text(
                "CREATE TABLE ticket_discoveries ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "filed_ticket_id VARCHAR NOT NULL UNIQUE REFERENCES tickets(id), "
                "source_ticket_id VARCHAR NOT NULL REFERENCES tickets(id), "
                "agent_run_id VARCHAR NOT NULL REFERENCES agent_runs(id), "
                "orchestration_run_id VARCHAR REFERENCES orchestration_runs(id), "
                "filed_at DATETIME NOT NULL, "
                "decision VARCHAR, "
                "decided_at DATETIME, "
                "decided_by VARCHAR NOT NULL DEFAULT '', "
                "decision_reason VARCHAR NOT NULL DEFAULT ''"
                ")"
            )
        )
    for name, ddl in _INDEXES.items():
        if not index_exists(conn, name):
            conn.execute(text(ddl))
    # A database that predates any of the backfill's sources holds no run filings to
    # recover; the tables arrive empty with create_all after the migrations run.
    if all(
        table_exists(conn, table) and columns <= table_columns(conn, table)
        for table, columns in _BACKFILL_SOURCES.items()
    ):
        conn.execute(text(_BACKFILL))
