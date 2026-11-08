"""The AC gatekeeper's role tells it to settle each acceptance criterion.

Blobert TDD has no verify stage, so its AC gate is the stage that settles
criteria met, and the criteria check runs as a ticket leaves that gate
(`criterion_gate_stages`). Without this section the gatekeeper's first pass
settles nothing, and every ticket is sent back to it once with the check's
brief before it does. Role bodies are DB-authoritative, so the seed file's new
section is copied in here (`refresh_role_from_seed`: skipped for a body a
person has edited, which is reported, and for a database with no agents yet).
"""

from __future__ import annotations

from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261107_ac_gatekeeper_settles_criteria"
SLUG = "ac_gatekeeper"
ROLE_FILE = "agents/acceptance_criteria_gatekeeper.md"
MARKER = "## Settle every acceptance criterion"


@migration("20261107_ac_gatekeeper_settles_criteria", after="20261107_criterion_settlements")
def m_ac_gatekeeper_settles_criteria(conn: Connection) -> None:
    refresh_role_from_seed(
        conn,
        migration_id=MIGRATION_ID,
        slug=SLUG,
        role_file=ROLE_FILE,
        marker=MARKER,
    )
