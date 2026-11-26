"""blobert-tdd verifies the done-claim, and reviews Python and security.

Its `implement` stage handed straight to `script_review`, whose lanes were
`gdscript_reviewer`, `static_qa` and `architecture_reviewer`: nobody exercised
the change, nobody read it for weaknesses, and nobody owned Python, which is
most of `asset_generation/`. `studio-blobert-editor-tdd` already has this shape.

`visual_qa` is left out: its role file runs loregarden's `npm run visual-qa`,
and blobert's game has no such runner.

In-flight tickets keep their stages. Pinned ones resolve stages and transitions
from their own version (`resolve_ticket_transitions`); unpinned ones are pinned
to the outgoing version first, or a required `verify` would appear, pending,
behind the cursor of a ticket already past it. Never-run backlog moves.
"""

from __future__ import annotations

import json
import logging

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_template_moves import move_never_run_backlog, pin_unpinned
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261125_blobert_verify_and_lanes"
TEMPLATE = "blobert-tdd"
WORKSPACE = "blobert"
_IMPLEMENT, _VERIFY, _REVIEW = "implement", "verify", "script_review"
NEW_LANES = ("python_reviewer", "security_reviewer")

_VERIFY_BRIEF = (
    "This is blobert: a Godot game, a Python asset pipeline and a web editor. Use the test "
    "commands its AGENTS.md defines for the part the diff touches. A gameplay change is "
    "verified by its Godot tests or a headless run, not by reading the script."
)
_LANES_BRIEF = (
    "Each lane reads its own part of the diff: gdscript_reviewer the .gd files, "
    "python_reviewer the .py files. A lane whose files the diff does not touch passes with "
    "one line."
)
_VERIFY_STAGE = {
    "key": _VERIFY,
    "name": "Verify",
    "agent_id": "verifier",
    "skill_name": "",
    "stage_type": "verify",
    "required_evidence": ["verify_verdict"],
    "required_inputs": ["diff", "acceptance_criteria"],
    "stage_brief": _VERIFY_BRIEF,
    "timeout_seconds": 1800,
    "exit_actions_enabled": False,
    "exit_actions": [],
}


@migration("20261125_blobert_verify_and_lanes", after="20261125_product_tdd_template")
def m_blobert_verify_and_lanes(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json "
                "FROM workflow_templates WHERE slug=:s"
            ),
            {"s": TEMPLATE},
        )
        .mappings()
        .first()
    )
    if row is None:
        return
    stages = json.loads(row["stages_json"] or "[]")
    transitions = json.loads(row["transitions_json"] or "[]")
    by_key = {stage.get("key"): stage for stage in stages}
    review = by_key.get(_REVIEW)
    if _IMPLEMENT not in by_key or review is None or review.get("stage_type") != "parallel":
        # Not silent: the review gap this closes stays open.
        logger.warning(
            "%s: %s no longer has an implement stage and a parallel %s; leaving it as it is",
            MIGRATION_ID,
            TEMPLATE,
            _REVIEW,
        )
        return

    new_stages = with_review_lanes(with_verify(stages))
    if new_stages == stages:
        return
    new_transitions = transitions if _VERIFY in by_key else through_verify(transitions)

    old_version = int(row["version"] or 1)
    new_version = old_version + 1
    moved = move_never_run_backlog(
        conn,
        workspace_slug=WORKSPACE,
        from_template_id=row["id"],
        to_template_id=row["id"],
        version=new_version,
        stages=new_stages,
    )
    pinned = pin_unpinned(conn, row["id"], old_version)
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(new_stages),
            "tr": json.dumps(new_transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    snapshot_template_version(
        conn, row["id"], new_version, "Verify stage, and Python and security review lanes"
    )
    logger.info(
        "%s: moved %d never-run tickets to v%d, pinned %d unpinned to v%d",
        TEMPLATE,
        moved,
        new_version,
        pinned,
        old_version,
    )


def with_verify(stages: list[dict]) -> list[dict]:
    """`stages` with verify after implement, reordered; unchanged when it has one."""
    if any(stage.get("key") == _VERIFY for stage in stages):
        return stages
    out: list[dict] = []
    for stage in sorted(stages, key=lambda s: s.get("order", 0)):
        out.append(stage)
        if stage.get("key") == _IMPLEMENT:
            out.append(dict(_VERIFY_STAGE))
    return [{**stage, "order": index} for index, stage in enumerate(out, start=1)]


def with_review_lanes(stages: list[dict]) -> list[dict]:
    """`stages` with the missing review lanes added, and the brief that scopes them."""
    out: list[dict] = []
    for stage in stages:
        lanes = stage.get("parallel_agents") or []
        fielded = {lane.get("agent_id") for lane in lanes}
        missing = [agent for agent in NEW_LANES if agent not in fielded]
        if stage.get("key") == _REVIEW and missing:
            brief = stage.get("stage_brief") or ""
            stage = {
                **stage,
                "parallel_agents": [
                    *lanes,
                    *({"agent_id": agent, "skill_name": ""} for agent in missing),
                ],
                "stage_brief": f"{brief}\n\n{_LANES_BRIEF}".strip(),
            }
        out.append(stage)
    return out


def through_verify(transitions: list[dict]) -> list[dict]:
    """implement → script_review becomes implement → verify → script_review."""
    out = [
        {**edge, "to": _VERIFY} if (edge["from"], edge["to"]) == (_IMPLEMENT, _REVIEW) else edge
        for edge in transitions
    ]
    return [
        *out,
        {"from": _VERIFY, "to": _REVIEW, "when": "pass"},
        {"from": _VERIFY, "to": _IMPLEMENT, "when": "reject"},
    ]
