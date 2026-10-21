"""The repair agent gets 2400s, not 900s.

An integration merge turn runs on `repair`, and on 2026-10-19 both turns for
lg-milestone-that-1216 were killed at 900s. Each had resolved its four
conflicts within seven minutes and spent the rest running the covering tests,
which take one to two minutes a file on this machine. 2400s is what the
implement stage already grants a repair run.

Only the old default is raised; a timeout a person chose is left alone.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import snapshot_agent_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

MIGRATION_ID = "20261021_repair_timeout_for_merges"
SLUG = "repair"
OLD_TIMEOUT = 900
NEW_TIMEOUT = 2400


@migration("20261021_repair_timeout_for_merges", after="20261021_tickets_block_kind_in_roles")
def m_repair_timeout_for_merges(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    row = (
        conn.execute(
            text("SELECT id, version, timeout FROM studio_agents WHERE slug=:s"),
            {"s": SLUG},
        )
        .mappings()
        .fetchone()
    )
    if row is None or row["timeout"] != OLD_TIMEOUT:
        return
    now = datetime.now(timezone.utc)
    version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE studio_agents SET timeout=:t, version=:v, updated_at=:now WHERE id=:id"),
        {"t": NEW_TIMEOUT, "v": version, "now": now, "id": row["id"]},
    )
    snapshot_agent_version(
        conn,
        agent_id=row["id"],
        version=version,
        note=f"{MIGRATION_ID}: timeout {OLD_TIMEOUT}s -> {NEW_TIMEOUT}s",
        now=now,
    )
