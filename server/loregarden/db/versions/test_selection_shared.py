"""Every role that runs tests reads one rule for which tests to run.

Refreshes `backend_implementer`, `frontend_implementer`, `repair`, `spec` and
`verifier` from their seeds, which now point at
`common_assets/test_selection_v1.md`: the tests the change reaches, in the
foreground, never a full suite left running when the headless turn ends (the
run then exits with no stage report). Via `refresh_role_from_seed`: a row a
person has edited is reported, not overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261014_test_selection_shared"

#: Each seed's new line names the rule; no other text in these roles does.
MARKER = "common_assets/test_selection_v1.md"

#: (slug, seed file, marker).
ROLES = (
    ("backend_implementer", "agents/5_backend_implementer/backend_implementer_v1.md", MARKER),
    ("frontend_implementer", "agents/6_frontend_implementer/frontend_implementer_v1.md", MARKER),
    ("repair", "agents/misc_agents/repair_v1.md", MARKER),
    ("spec", "agents/2_spec/spec_v1.md", MARKER),
    ("verifier", "agents/misc_agents/verifier_v1.md", MARKER),
)


@migration("20261014_test_selection_shared", after="20261013_artifact_content_objects")
def m_test_selection_shared(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    for slug, role_file, marker in ROLES:
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
