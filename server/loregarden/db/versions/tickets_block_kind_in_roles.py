"""Every built-in role's outcome line names `tickets` as a block kind, with `waiting_on`.

A stage report may now say the ticket waits on other tickets
(`blocked_kind: tickets` plus `waiting_on`), and the orchestrator holds it
rather than spending a repair turn (lg-milestone-that-1315, whose triage said
the same wait as `work` twice). The contract (`workflow_enforcement_v1.md`) is
read from disk; the role bodies are DB-authoritative, so their one-line summary
of the kinds is rewritten here.

In place, not refreshed from the seed: the line is a sentence this repository
wrote into every body (0137), so replacing that exact sentence cannot undo a
person's edit elsewhere in the body, and a body without it is left alone.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import snapshot_agent_version
from loregarden.db.versions import migration
from loregarden.models.domain.enums import utcnow
from sqlalchemy import Connection, text

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261021_tickets_block_kind_in_roles"

OLD_SENTENCE = (
    "`harness` | `work` | `decision` | `human_action` — and a `decision` must carry 2–4 "
    "`options` a person can pick in one click;"
)
NEW_SENTENCE = (
    "`harness` | `work` | `decision` | `human_action` | `tickets` — a `decision` must carry "
    "2–4 `options` a person can pick in one click, and `tickets` must carry `waiting_on`, the "
    "ids of the tickets this one waits for;"
)


@migration("20261021_tickets_block_kind_in_roles", after="20261020_fix_coverage_lanes")
def m_tickets_block_kind_in_roles(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    rows = (
        conn.execute(
            text("SELECT id, version, role_body FROM studio_agents WHERE role_body LIKE :old"),
            {"old": f"%{OLD_SENTENCE}%"},
        )
        .mappings()
        .fetchall()
    )
    now = utcnow()
    for row in rows:
        version = int(row["version"] or 1) + 1
        conn.execute(
            text(
                "UPDATE studio_agents SET role_body=:body, version=:v, updated_at=:now WHERE id=:id"
            ),
            {
                "body": row["role_body"].replace(OLD_SENTENCE, NEW_SENTENCE),
                "v": version,
                "now": now,
                "id": row["id"],
            },
        )
        snapshot_agent_version(
            conn,
            agent_id=row["id"],
            version=version,
            note=f"{MIGRATION_ID}: `tickets` block kind named in the outcome line",
            now=now,
        )
