"""The verifier runs the tests that reach the change, not a sweep.

Refreshes `verifier` from its seed, which now carries *Choosing which tests to
run*. Via `refresh_role_from_seed`: a row a person has edited is reported, not
overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261009_verifier_selects_tests"
SLUG = "verifier"
ROLE_FILE = "agents/misc_agents/verifier_v1.md"
MARKER = "## Choosing which tests to run"


@migration("20261009_verifier_selects_tests", after="20261009_ui_design_skip_without_ui")
def m_verifier_selects_tests(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    refresh_role_from_seed(
        conn,
        migration_id=MIGRATION_ID,
        slug=SLUG,
        role_file=ROLE_FILE,
        marker=MARKER,
    )
