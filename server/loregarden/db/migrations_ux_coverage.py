"""Migration 0124: the design stage reaches the other workspaces.

0123 gave `studio-loregarden-tdd-v3` a UX voice, and only loregarden runs that
template. blobert, lore-eden and loremaker run `blobert-tdd` and
`loregarden-tdd`, neither of which had a design stage at all — so the fix looked
done while covering one workspace in four, which is worse than not having
started, because it reads as solved.

**Only the design half travels.** `visual_qa` drives `npm run visual-qa`, a
Playwright runner that lives in loregarden's `client/` and reads
`src/lib/visualQaRoutes.json`. lore-eden is a library with no app, loremaker's
client has no such script, and blobert is Godot — a browser screenshot pass is
not a thing that exists there. Adding the lane to those templates would fail
every ticket at review with a missing npm script, which is not enforcement, it
is breakage. The lane stays where its runner is until each workspace has one.

The design agent has no such dependency: it reasons over the ticket and the
surrounding code, and the five states it decides are medium-agnostic. That half
goes everywhere.
"""

from __future__ import annotations

import json
import logging

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

_DESIGN_KEY = "ui-design"
_DESIGN_AGENT = "ui-design-decision"

#: The stage the design decisions feed. Inserting immediately before it is what
#: makes the criteria available to whoever writes the spec, rather than arriving
#: after the shape of the work is already fixed.
_ANCHOR_KEY = "spec"

_WEB_BRIEF = (
    "Decide the user-facing behaviour this ticket leaves undecided, before anyone "
    "writes it. Name, for every surface it touches: the loading state, the empty "
    "state, the error state, what happens on a slow action, and how it is reached "
    "and dismissed from the keyboard. A ticket that changes no surface a person "
    "sees needs none of that — say so in one line and pass.\n\n"
    "Record the decisions with `loregarden_update_ticket` as acceptance criteria, "
    "so the implementer is held to them and the reviewer can check them. Long "
    "rationale goes to `loregarden_attach_artifact`. Never write a markdown file: "
    "nothing reads it, and the orchestrator sweeps it into an unrelated commit."
)

#: Same five states, read for a game rather than a page. Written out rather than
#: left to the agent to translate, because "loading state" in a Godot HUD is a
#: different object than in a React table and a role file cannot be both.
_GAME_BRIEF = (
    "Decide the player-facing behaviour this ticket leaves undecided, before "
    "anyone writes it. Name, for every surface it touches: what the player sees "
    "while something is resolving, what an empty or zero case looks like, how a "
    "failure is communicated in-world rather than swallowed, what feedback "
    "confirms an input was received, and how the surface is operated without a "
    "mouse — keyboard and controller. A ticket that changes nothing a player "
    "perceives needs none of that — say so in one line and pass.\n\n"
    "Record the decisions with `loregarden_update_ticket` as acceptance criteria. "
    "Long rationale goes to `loregarden_attach_artifact`. Never write a markdown "
    "file: nothing reads it, and the orchestrator sweeps it into an unrelated "
    "commit."
)

#: Template slug → the brief its design stage carries.
_TARGETS: dict[str, str] = {
    "loregarden-tdd": _WEB_BRIEF,
    "blobert-tdd": _GAME_BRIEF,
}

#: Tools the design stage's brief tells the agent to use. A brief naming a tool
#: the agent has not been granted is an instruction it cannot follow, and the
#: failure looks like the agent ignoring the brief.
_REQUIRED_TOOLS = (
    "loregarden_update_ticket",
    "loregarden_attach_artifact",
    "loregarden_append_checkpoint",
)


def m_ux_design_everywhere(conn: Connection) -> None:
    """Insert the design stage into the templates the other workspaces run."""
    _grant_design_tools(conn)
    for slug, brief in _TARGETS.items():
        _insert_design_stage(conn, slug=slug, brief=brief)


def _grant_design_tools(conn: Connection) -> None:
    """Give the design agent the tools its brief tells it to use."""
    if not table_exists(conn, "studio_agents"):
        return
    row = (
        conn.execute(
            text("SELECT id, mcp_tools_json FROM studio_agents WHERE slug=:s"),
            {"s": _DESIGN_AGENT},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return
    tools = json.loads(row["mcp_tools_json"] or "[]")
    missing = [tool for tool in _REQUIRED_TOOLS if tool not in tools]
    if not missing:
        return
    tools.extend(missing)
    conn.execute(
        text("UPDATE studio_agents SET mcp_tools_json=:t WHERE id=:id"),
        {"t": json.dumps(tools), "id": row["id"]},
    )
    logger.info("0124: granted %s to %r", ", ".join(missing), _DESIGN_AGENT)


def _insert_design_stage(conn: Connection, *, slug: str, brief: str) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json FROM workflow_templates "
                "WHERE slug=:s"
            ),
            {"s": slug},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {stage.get("key"): stage for stage in stages}
    if _DESIGN_KEY in by_key or _ANCHOR_KEY not in by_key:
        return  # already wired, or this template has no stage to anchor to

    anchor_order = int(by_key[_ANCHOR_KEY].get("order") or 0)
    for stage in stages:
        if int(stage.get("order") or 0) >= anchor_order:
            stage["order"] = int(stage["order"]) + 1
    stages.append(
        {
            "key": _DESIGN_KEY,
            "name": "UI Design",
            "agent_id": _DESIGN_AGENT,
            "skill_name": "",
            # Required. Optional means prunable, and a stage a hurried run can
            # drop is the stage that gets dropped.
            "optional": False,
            "order": anchor_order,
            "stage_type": "agent",
            "terminal": False,
            # Deliberately empty, unlike v3's. `routed_as_light_work` reads the
            # classify route that made the decision, and neither of these
            # templates has a classify triage stage — the condition would never
            # be true and would read as a skip that works.
            "skip_when": "",
            "classify_routes": [],
            "parallel_agents": [],
            "gate_commands": [],
            "gate_required": False,
            "model": "",
            "required_evidence": [],
            "checklist": [],
            "stage_brief": brief,
        }
    )
    stages.sort(key=lambda stage: int(stage.get("order") or 0))

    # Re-point what *advances* into the anchor, then advance out of the new stage
    # into it.
    #
    # Forward edges only. blobert-tdd routes `test-design -> spec` and
    # `test-break -> spec` on `reject`: those are rework, sending a failed test
    # design back to the spec that produced it. Repointing them would send rework
    # to the design stage instead, which neither produced the spec nor can fix
    # it — a silent misroute that looks like a wired pipeline.
    transitions = json.loads(row["transitions_json"] or "[]")
    for item in transitions:
        if item.get("to") == _ANCHOR_KEY and item.get("when", "") in {"", "pass", "default"}:
            item["to"] = _DESIGN_KEY
    transitions.append({"from": _DESIGN_KEY, "to": _ANCHOR_KEY, "when": "pass"})

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(stages),
            "tr": json.dumps(transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    snapshot_template_version(conn, row["id"], new_version, "UI design stage before spec")
    _backfill_into_instances(
        conn,
        row["id"],
        {stage["key"]: int(stage.get("order") or 0) for stage in stages},
    )


def _backfill_into_instances(
    conn: Connection, template_id: str, stage_orders: dict[str, int]
) -> None:
    """Add the new stage to live instances without stranding or rewinding them.

    A required stage inserted mid-pipeline is PENDING for every in-flight ticket,
    which both blocks DONE — nothing ever resolves it — and pulls the cursor
    backwards, since the orchestrator runs the earliest pending stage.

    Whether a ticket has already passed the insertion point is decided by where
    its cursor sits rather than by any one stage's recorded status: a ticket can
    reach review with an earlier stage left unmarked after a reroute, and keying
    off that would hand it a pending design stage it should never run.
    """
    if not table_exists(conn, "workflow_instances"):
        return
    design_order = stage_orders.get(_DESIGN_KEY, 0)
    rows = (
        conn.execute(
            text(
                "SELECT id, stages_json, current_stage_key FROM workflow_instances "
                "WHERE template_id=:tid"
            ),
            {"tid": template_id},
        )
        .mappings()
        .fetchall()
    )
    for row in rows:
        entries = json.loads(row["stages_json"] or "[]")
        if any(entry.get("key") == _DESIGN_KEY for entry in entries):
            continue
        cursor_order = stage_orders.get(row["current_stage_key"] or "", 0)
        already_past = cursor_order > design_order
        entries.append({"key": _DESIGN_KEY, "status": "wont_do" if already_past else "pending"})
        conn.execute(
            text("UPDATE workflow_instances SET stages_json=:st WHERE id=:id"),
            {"st": json.dumps(entries), "id": row["id"]},
        )
