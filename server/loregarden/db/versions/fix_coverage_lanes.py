"""The spec and architecture-review roles learn the four answers a bug fix owes.

`run_completion` now sends a bug ticket's spec pass with no `fix_coverage`
record back to the stage (`fix_coverage.missing_fix_coverage`). The spec seed
says so and says what the four answers are; the architecture reviewer's seed
says how to judge them, which no script can. This refreshes both live roles
from their seeds, so the agents learn the rule from their briefs rather than
from a rework round.

Via `refresh_role_from_seed`: a row a person has edited is reported, not
overwritten. The tool itself reaches the agents without a data change:
`reconcile_builtin_mcp_tools` offers built-in agents every new tool at boot.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261020_fix_coverage_lanes"

#: (slug, seed file, a heading only the new seed carries).
ROLES = (
    (
        "spec",
        "agents/2_spec/spec_v1.md",
        "## Bug tickets: decide how the fix covers the whole state",
    ),
    (
        "architecture_reviewer",
        "agents/misc_agents/architecture_reviewer_v1.md",
        "## Bug tickets: is the fix the whole state?",
    ),
)


@migration("20261020_fix_coverage_lanes", after="20261015_ux_answers_required")
def m_fix_coverage_lanes(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    for slug, role_file, marker in ROLES:
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
