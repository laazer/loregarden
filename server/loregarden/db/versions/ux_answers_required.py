"""The design role is told its stage does not pass without recorded UX answers.

`run_completion` now sends a `ui-design-decision` pass with no `ux_answers`
record back to the stage as rework (`ux_answers.missing_ux_answers`). The seed
says so; this refreshes the live role from it, so the agent learns the rule
from its brief rather than from its first rework round.

Via `refresh_role_from_seed`: a row a person has edited is reported, not
overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261015_ux_answers_required"

SLUG = "ui-design-decision"
ROLE_FILE = "agents/misc_agents/ui_design_decision_v1.md"
MARKER = "This stage does not pass without a record."


@migration("20261015_ux_answers_required", after="20261015_ux_answers_recorded")
def m_ux_answers_required(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    refresh_role_from_seed(
        conn, migration_id=MIGRATION_ID, slug=SLUG, role_file=ROLE_FILE, marker=MARKER
    )
