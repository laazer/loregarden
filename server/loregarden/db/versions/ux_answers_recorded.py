"""The design role records the answers a tree's PR needs for its UX section.

Refreshes `ui-design-decision` from its seed file, which now tells it to record
the question, action and real data with `loregarden_record_ux_answers` once the
criteria are settled. A tree's PR is written by `publish_tree`, which assembles
its "User-facing surfaces" section from those records; until they existed, it
had none to give, and #622 failed the PR UX check on 917's surface changes.

Via `refresh_role_from_seed`: a row a person has edited is reported, not
overwritten. The tool itself reaches the agent without a data change:
`reconcile_builtin_mcp_tools` offers built-in agents every new tool at boot.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261015_ux_answers_recorded"

SLUG = "ui-design-decision"
ROLE_FILE = "agents/misc_agents/ui_design_decision_v1.md"
MARKER = "## Record the answers the PR will carry"


@migration("20261015_ux_answers_recorded", after="20261014_loregarden_implement_no_gdscript")
def m_ux_answers_recorded(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    refresh_role_from_seed(
        conn, migration_id=MIGRATION_ID, slug=SLUG, role_file=ROLE_FILE, marker=MARKER
    )
