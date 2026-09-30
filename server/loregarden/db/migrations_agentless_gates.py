"""Migration 0149: keep the human gate on agentless stages.

Before exit actions, a non-terminal stage with no agent was a human gate by
construction: the orchestrator parked there and opened an approval. `0138`
converted only the explicit `gate_required` flag, and the exit-actions model
deliberately stops treating an empty `agent_id` as a gate — a stage gates when
it authors an action a person must resolve. Together they would have turned
every such stage into one that completes itself with nobody asked: measured on
2026-09-30, `loregarden-tdd`'s `approval` (three workspaces) and
`blobert-tdd`'s `playtest` (one).

So each of them authors the same operator-judgment action `0138` gives a
`gate_required` stage. The criteria mirror `is_agentless_stage` and
`is_terminal_stage` as they stood when this was written, inlined because a
migration must not change meaning when that code does. Guarded per stage: a
stage that already has exit actions enabled is left alone, so re-running
changes nothing.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_exit_actions import legacy_sign_off_action
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: Stage types that dispatch agents of their own despite an empty `agent_id`.
_AGENT_STAGE_TYPES = frozenset({"classify", "gate", "parallel", "verify"})


def _is_implicit_human_gate(stage: dict) -> bool:
    if stage.get("terminal") or stage.get("key") == "done":
        return False
    if stage.get("stage_type") in _AGENT_STAGE_TYPES:
        return False
    return not str(stage.get("agent_id") or "").strip()


def _gate_agentless_stages(raw: str) -> str | None:
    """The rewritten stage list, or None when nothing in it changes."""
    stages = json.loads(raw or "[]")
    changed = False
    for stage in stages:
        if stage.get("exit_actions_enabled") or not _is_implicit_human_gate(stage):
            continue
        stage["exit_actions_enabled"] = True
        stage["exit_actions"] = [legacy_sign_off_action(stage)]
        changed = True
    return json.dumps(stages) if changed else None


def m_agentless_stage_exit_actions(conn: Connection) -> None:
    for table_name in ("workflow_templates", "studio_workflows"):
        if not table_exists(conn, table_name):
            continue
        rows = conn.execute(text(f"SELECT id, stages_json FROM {table_name}")).mappings().all()
        for row in rows:
            rewritten = _gate_agentless_stages(row["stages_json"])
            if rewritten is not None:
                conn.execute(
                    text(f"UPDATE {table_name} SET stages_json=:stages WHERE id=:id"),
                    {"id": row["id"], "stages": rewritten},
                )

    # Version-pinned instances read their stages from these snapshots.
    if not table_exists(conn, "workflow_template_versions"):
        return
    rows = (
        conn.execute(text("SELECT id, snapshot_json FROM workflow_template_versions"))
        .mappings()
        .all()
    )
    for row in rows:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        if "stages_json" not in snapshot:
            continue
        rewritten = _gate_agentless_stages(snapshot["stages_json"])
        if rewritten is not None:
            snapshot["stages_json"] = rewritten
            conn.execute(
                text("UPDATE workflow_template_versions SET snapshot_json=:snapshot WHERE id=:id"),
                {"id": row["id"], "snapshot": json.dumps(snapshot)},
            )
