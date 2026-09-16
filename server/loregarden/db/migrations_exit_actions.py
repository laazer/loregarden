"""Migrate static gate_required flags to runtime exit actions."""

from __future__ import annotations

import json

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def _migrate_legacy_stage(stage: dict) -> dict:
    payload = dict(stage)
    if "gate_required" not in payload:
        payload.setdefault("exit_actions_enabled", False)
        payload.setdefault("exit_actions", [])
        return payload
    gate_required = bool(payload.pop("gate_required", False))
    payload["exit_actions_enabled"] = gate_required
    stage_name = str(payload.get("name") or payload.get("key") or "stage")
    payload["exit_actions"] = (
        [
            {
                "key": "legacy-stage-sign-off",
                "label": f"Approve {stage_name} completion",
                "requirement": {
                    "kind": "operator_judgment",
                    "decision_prompt": f"Approve completion of stage '{stage_name}'.",
                },
            }
        ]
        if gate_required
        else []
    )
    return payload


def _migrate_stage_list(raw: str) -> str:
    stages = json.loads(raw or "[]")
    return json.dumps([_migrate_legacy_stage(stage) for stage in stages])


def m_runtime_exit_actions(conn: Connection) -> None:
    """Replace persisted static sign-off flags with typed exit actions."""
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "runtime_exit_action_snapshot_json": (
                "ALTER TABLE agent_runs ADD COLUMN runtime_exit_action_snapshot_json TEXT NOT NULL DEFAULT ''"
            ),
            "assigned_exit_action_keys_json": (
                "ALTER TABLE agent_runs ADD COLUMN assigned_exit_action_keys_json TEXT NOT NULL DEFAULT '[]'"
            ),
            "completed_exit_action_keys_json": (
                "ALTER TABLE agent_runs ADD COLUMN completed_exit_action_keys_json TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )

    for table_name in ("workflow_templates", "studio_workflows", "workflow_instances"):
        if not table_exists(conn, table_name):
            continue
        rows = conn.execute(text(f"SELECT id, stages_json FROM {table_name}")).mappings().all()
        for row in rows:
            conn.execute(
                text(f"UPDATE {table_name} SET stages_json=:stages WHERE id=:id"),
                {"id": row["id"], "stages": _migrate_stage_list(row["stages_json"])},
            )

    if not table_exists(conn, "workflow_template_versions"):
        return
    rows = (
        conn.execute(text("SELECT id, snapshot_json FROM workflow_template_versions"))
        .mappings()
        .all()
    )
    for row in rows:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        if "stages_json" in snapshot:
            snapshot["stages_json"] = _migrate_stage_list(snapshot["stages_json"])
            conn.execute(
                text("UPDATE workflow_template_versions SET snapshot_json=:snapshot WHERE id=:id"),
                {"id": row["id"], "snapshot": json.dumps(snapshot)},
            )
