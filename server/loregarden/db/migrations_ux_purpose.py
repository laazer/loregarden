"""Migration 0147: make the UX lanes ask what a surface is for, not only its shape.

0123 gave the pipeline a design stage and a visual lane, both built around the
five states — loading, empty, error, in-flight, keyboard. Those are shape, and
shape passed every surface that later turned out useless: the Monitor tab
handled all five states and printed 138 findings with no ticket named and 85 of
94 on finished tickets; the Memory map drew 33 records and 0 links; Initiatives
was one sentence beside 74 unassigned milestones.

Two halves, each guarding itself:

1. Refresh `ui-design-decision` and `visual_qa` from their seed files, which now
   require the question a surface answers, the action it leads to, and a check
   against a sandbox snapshot of production data. Via `refresh_role_from_seed`:
   untouched when a person has edited the row.
2. Add the same three questions to the v3 `ui-design` stage brief, which is
   what the stage injects beside the role body. Untouched when already present.
"""

from __future__ import annotations

import json
import logging

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.migrations_templates import snapshot_template_version
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

MIGRATION_ID = "0147_ux_purpose_in_design_lanes"

#: (slug, seed file, a heading only the new seed carries).
_ROLES = (
    (
        "ui-design-decision",
        "agents/misc_agents/ui_design_decision_v1.md",
        "## What the surface is for",
    ),
    ("visual_qa", "agents/misc_agents/visual_qa_v1.md", "## Check what the surface is for"),
)

_TEMPLATE = "studio-loregarden-tdd-v3"
_DESIGN_STAGE = "ui-design"
_BRIEF_MARKER = "question it answers"
_BRIEF_ADDITION = (
    "\n\nBefore the states, record three things as acceptance criteria for every surface the "
    "ticket touches: the question it answers for the operator (one sentence, from their side), "
    "the action it leads to (the link, button or filter they use next), and how it behaves at the "
    "largest realistic volume — measured from the live database read-only, not a fixture."
)


def _extend_design_brief(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return
    stages = json.loads(row["stages_json"] or "[]")
    design = next((stage for stage in stages if stage.get("key") == _DESIGN_STAGE), None)
    if design is None:
        logger.warning(
            "%s: %s has no %r stage; brief not extended", MIGRATION_ID, _TEMPLATE, _DESIGN_STAGE
        )
        return
    brief = design.get("stage_brief") or ""
    if _BRIEF_MARKER in brief:
        return
    design["stage_brief"] = brief + _BRIEF_ADDITION
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    snapshot_template_version(
        conn, row["id"], new_version, "ui-design brief: question, action, real data"
    )


def m_ux_purpose_in_design_lanes(conn: Connection) -> None:
    if table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions"):
        for slug, role_file, marker in _ROLES:
            refresh_role_from_seed(
                conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
            )
    _extend_design_brief(conn)
