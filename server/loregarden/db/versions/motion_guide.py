"""The design, visual-QA and frontend roles read the motion guide.

Refreshes `ui-design-decision`, `visual_qa` and `frontend_implementer` from their
seed files, which now point at `common_assets/motion_v1.md`: motion shows a
change of state, on the tokens, transform and opacity only, with reduced motion
honoured. Via `refresh_role_from_seed`: a row a person has edited is reported,
not overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261009_motion_guide"

#: Each seed's new line names the guide; no other text in these roles does.
MARKER = "common_assets/motion_v1.md"

#: (slug, seed file, marker).
ROLES = (
    ("ui-design-decision", "agents/misc_agents/ui_design_decision_v1.md", MARKER),
    ("visual_qa", "agents/misc_agents/visual_qa_v1.md", MARKER),
    ("frontend_implementer", "agents/6_frontend_implementer/frontend_implementer_v1.md", MARKER),
)


@migration("20261009_motion_guide", after="20261009_sonnet_for_mechanical_lanes")
def m_motion_guide(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    for slug, role_file, marker in ROLES:
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
