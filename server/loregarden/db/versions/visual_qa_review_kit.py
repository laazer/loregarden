"""Visual QA hands the human gate its screenshots and pages as a review kit.

Refreshes `visual_qa` from its seed file, which now tells it to add a `review`
block (its screenshots, and each changed surface as a path link) to the
evidence it already attaches. The review page shows that kit beside the gate;
until now the agent's screenshots stayed in its sandbox's `.visual-qa/`.

The live body was also behind the seed on earlier edits (the exit-3
INCONCLUSIVE guidance), which this brings in with it: every version of the row
was written by seed, migration or reconcile, so the seed is the whole story.

Via `refresh_role_from_seed`: a row a person has edited is reported, not
overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261122_visual_qa_review_kit"

SLUG = "visual_qa"
ROLE_FILE = "agents/misc_agents/visual_qa_v1.md"
MARKER = '"links": [{"path": "/work-items", "label": "Work items"}]'


@migration("20261122_visual_qa_review_kit", after="20261119_stage_ask")
def m_visual_qa_review_kit(conn: Connection) -> None:
    if not (table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions")):
        return
    refresh_role_from_seed(
        conn, migration_id=MIGRATION_ID, slug=SLUG, role_file=ROLE_FILE, marker=MARKER
    )
