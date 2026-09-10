"""Migration 0122: give the pipeline a UX voice, and give it authority.

Two agents were built for this and wired to nothing. `ui-design-decision` and
`visual_qa` appear in no workflow template and have never run — measured across
every row of `agent_runs`. Meanwhile the `ui-design` stage dispatched the
*planner* under the `plan` skill, and was `optional`, so the one stage named for
the user was a second planning turn that any run could prune.

The result was a pipeline a ticket could cross end to end — plan, spec, test,
implement, verify, three reviewers, gate — without one agent looking at what the
person in front of the app would see.

Two halves, each guarding itself, in the append-only style of this package:

1. Reshape the template — point `ui-design` at the agent it was named for,
   require it, and add the visual lane to the review fan-out.
2. Repair the agent row it now points at. The live `ui-design-decision` was
   created by an earlier migration with no stage-outcome section in its role
   body, which is the sentinel the orchestrator routes on: a required stage
   running that agent would block on every ticket. It is refreshed from its seed
   file — but only where no human has edited it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.config import settings
from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

_UX_TEMPLATE = "studio-loregarden-tdd-v3"
_UX_DESIGN_STAGE = "ui-design"
_UX_DESIGN_AGENT = "ui-design-decision"
_UX_REVIEW_STAGE = "review"
_UX_REVIEW_LANE = "visual_qa"

# What the ui-design stage wants from this agent specifically, beyond its role
# file. Two things the role file cannot know: where a report goes in this repo,
# and that most tickets have no user-facing surface at all and should cost one
# short turn rather than a full design pass.
_UX_DESIGN_BRIEF = (
    "Decide the user-facing behaviour this ticket leaves undecided, before anyone "
    "writes it. Name, for every surface it touches: the loading state, the empty "
    "state, the error state, what happens on a slow action, and how it is reached "
    "and dismissed from the keyboard. A ticket that changes no surface a person "
    "sees needs none of that — say so in one line and pass.\n\n"
    "Record the decisions with `loregarden_update_ticket` as acceptance criteria, "
    "so the implementer is held to them and the gatekeeper can check them. Long "
    "rationale goes to `loregarden_attach_artifact`. Never write a markdown file: "
    "nothing reads it, and the orchestrator sweeps it into an unrelated commit."
)


def _reshape_template(conn: Connection) -> None:
    """Point `ui-design` at its agent, and add the visual lane to review."""
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _UX_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {stage.get("key"): stage for stage in stages}
    changed = False

    design = by_key.get(_UX_DESIGN_STAGE)
    if design is not None and design.get("agent_id") != _UX_DESIGN_AGENT:
        design["agent_id"] = _UX_DESIGN_AGENT
        # The planner's `plan` skill came with the planner. The design agent's
        # own role file is the instruction now, plus the brief below.
        design["skill_name"] = ""
        design["optional"] = False
        design["skip_when"] = "routed_as_light_work"
        design["stage_brief"] = _UX_DESIGN_BRIEF
        changed = True

    review = by_key.get(_UX_REVIEW_STAGE)
    if review is not None and review.get("stage_type") == "parallel":
        lanes = review.get("parallel_agents") or []
        if all(lane.get("agent_id") != _UX_REVIEW_LANE for lane in lanes):
            lanes.append({"agent_id": _UX_REVIEW_LANE, "skill_name": ""})
            review["parallel_agents"] = lanes
            changed = True

    if not changed:
        return

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    snapshot_template_version(
        conn, row["id"], new_version, "UX design stage and visual review lane"
    )


#: The seed file the design agent's role body is refreshed from. Its live row was
#: written by an earlier migration without the stage-outcome section, and that
#: section is the sentinel the orchestrator routes on.
_UX_DESIGN_ROLE_FILE = "agents/misc_agents/ui_design_decision_v1.md"

#: The section whose absence blocks the stage. Checked for by name rather than by
#: whole-body comparison so an operator who has reworded the role file around it
#: is not overwritten for a change that does not matter.
_STAGE_REPORT_SENTINEL = "LOREGARDEN_STAGE_REPORT"


def m_ux_lanes_in_v3(conn: Connection) -> None:
    """Wire the UX stage and lane, and make the agent behind them dispatchable."""
    _reshape_template(conn)
    _repair_design_agent(conn)


def _repair_design_agent(conn: Connection) -> None:
    """Refresh the design agent's role body from its seed file.

    Guarded twice, because overwriting an operator's text is worse than the
    defect: the refresh happens only when the body is missing the stage-outcome
    sentinel (so a correct agent is never touched) and only when no human appears
    in its version history (so a hand-edited one is left alone and reported).
    """
    if not table_exists(conn, "studio_agents") or not table_exists(conn, "studio_agent_versions"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, role_body FROM studio_agents WHERE slug=:s"),
            {"s": _UX_DESIGN_AGENT},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        # A fresh install has not seeded yet; the registry entry covers it.
        logger.info("0122: no %r agent row yet; seeding will create it", _UX_DESIGN_AGENT)
        return
    if _STAGE_REPORT_SENTINEL in (row["role_body"] or ""):
        return

    creators = {
        entry[0]
        for entry in conn.execute(
            text("SELECT created_by FROM studio_agent_versions WHERE agent_id=:id"),
            {"id": row["id"]},
        ).fetchall()
    }
    if creators - {"seed", "migration"}:
        # Not silent: a required stage is about to dispatch an agent that cannot
        # report an outcome, and the operator is the only one who can fix it.
        logger.warning(
            "0122: %r has no %s section and has been edited by %s — leaving it alone. "
            "The ui-design stage will block until that section is restored.",
            _UX_DESIGN_AGENT,
            _STAGE_REPORT_SENTINEL,
            ", ".join(sorted(creators)) or "an unknown editor",
        )
        return

    path = settings.agent_context_dir / _UX_DESIGN_ROLE_FILE
    if not path.is_file():
        logger.warning("0122: %s is missing; cannot refresh %r", path, _UX_DESIGN_AGENT)
        return
    role_body = path.read_text(encoding="utf-8")
    if not role_body.strip():
        logger.warning("0122: empty seed body for %r; not refreshing", _UX_DESIGN_AGENT)
        return

    now = datetime.now(timezone.utc)
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE studio_agents SET role_body=:body, version=:v, built_in=1, "
            # `autopilot` came with the row and is not a design skill; the role
            # file is the instruction, and the stage brief is the rest.
            "default_skill='', updated_at=:now WHERE id=:id"
        ),
        {"body": role_body, "v": new_version, "now": now, "id": row["id"]},
    )
    snapshot = (
        conn.execute(
            text(
                "SELECT slug, name, description, adapter, default_model, timeout, default_skill, "
                "mcp_enabled, mcp_tools_json, gate_checks_json, handoff_checks_json, "
                "tool_grants_json, built_in FROM studio_agents WHERE id=:id"
            ),
            {"id": row["id"]},
        )
        .mappings()
        .fetchone()
    )
    conn.execute(
        text(
            "INSERT INTO studio_agent_versions "
            "(id, agent_id, version, snapshot_json, created_by, change_note, created_at) "
            "VALUES (:id, :agent_id, :v, :snapshot, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "agent_id": row["id"],
            "v": new_version,
            "snapshot": json.dumps(dict(snapshot)),
            "note": "0122_ux_lanes_in_v3: role body refreshed from seed",
            "now": now,
        },
    )
    logger.info("0122: refreshed %r role body from %s", _UX_DESIGN_AGENT, path)
