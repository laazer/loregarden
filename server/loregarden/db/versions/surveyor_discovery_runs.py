"""The reference surveyor knows a discovery run from a survey pass.

Refreshes `reference_surveyor` from its seed, which now carries *When your
ticket is a discovery run*. Via `refresh_role_from_seed`: a row a person has
edited is reported, not overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261023_surveyor_discovery_runs"
SLUG = "reference_surveyor"
ROLE_FILE = "agents/misc_agents/reference_surveyor_v1.md"
MARKER = "## When your ticket is a discovery run"


@migration("20261023_surveyor_discovery_runs", after="20261023_survey_discoveries")
def m_surveyor_discovery_runs(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    refresh_role_from_seed(
        conn, migration_id=MIGRATION_ID, slug=SLUG, role_file=ROLE_FILE, marker=MARKER
    )
