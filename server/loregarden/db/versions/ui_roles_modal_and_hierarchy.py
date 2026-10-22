"""Visual QA checks visual hierarchy; both UI agents know forms open in modals.

Refreshes `visual_qa` (now carrying *Check the visual hierarchy* and the modal
rule) and `ui-design-decision` (the modal rule) from their seeds. Via
`refresh_role_from_seed`: a row a person has edited is reported, not overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261022_ui_roles_modal_and_hierarchy"
#: (slug, seed file, a line only the new text has)
REFRESHES = (
    ("visual_qa", "agents/misc_agents/visual_qa_v1.md", "## Check the visual hierarchy"),
    (
        "ui-design-decision",
        "agents/misc_agents/ui_design_decision_v1.md",
        "Forms and imports open in a modal (`ModalShell`)",
    ),
)


@migration("20261022_ui_roles_modal_and_hierarchy", after="20261022_survey_source_upstream")
def m_ui_roles_modal_and_hierarchy(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    for slug, role_file, marker in REFRESHES:
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
