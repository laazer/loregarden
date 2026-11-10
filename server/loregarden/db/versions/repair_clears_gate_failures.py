"""The repair agent may make the change a failing transition gate asks for.

Refreshes `repair` from its seed, which now carries *When a transition gate is
the block*: a gate failure whose agent retries are spent now gets the repair
turn, and the role says the structural change the gate names (splitting a file
over its size limit, moving code) is the stage's work. Via
`refresh_role_from_seed`: a row a person has edited is reported, not overwritten.
"""

from __future__ import annotations

from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261110_repair_clears_gate_failures"
SLUG = "repair"
ROLE_FILE = "agents/misc_agents/repair_v1.md"
MARKER = "## When a transition gate is the block"


@migration("20261110_repair_clears_gate_failures", after="20261109_run_dispatch_key")
def m_repair_clears_gate_failures(conn: Connection) -> None:
    # `refresh_role_from_seed` returns without writing when the agent tables are absent.
    refresh_role_from_seed(
        conn, migration_id=MIGRATION_ID, slug=SLUG, role_file=ROLE_FILE, marker=MARKER
    )
