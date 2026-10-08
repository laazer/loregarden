"""The design and visual-QA roles choose a list, table or other layout by the question.

Refreshes `ui-design-decision` and `visual_qa` from their seed files, which now
carry the layout rules from CLAUDE.md (*Lists, tables, or something else*). Via
`refresh_role_from_seed`: a row a person has edited is reported, not overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

MIGRATION_ID = "20261007_layout_by_question"

#: (slug, seed file, a heading only the new seed carries).
ROLES = (
    (
        "ui-design-decision",
        "agents/misc_agents/ui_design_decision_v1.md",
        "## Choose the layout by the question",
    ),
    (
        "visual_qa",
        "agents/misc_agents/visual_qa_v1.md",
        "## Check the layout fits the question",
    ),
)


@migration(MIGRATION_ID, after="20261007_initiative_planner_runtime")
def m_layout_by_question(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    for slug, role_file, marker in ROLES:
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
