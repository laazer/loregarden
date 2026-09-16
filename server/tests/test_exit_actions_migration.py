"""AC-11/AC-12: legacy gate_required stages migrate into typed exit actions.

Fixtures that already embed ``legacy-stage-sign-off`` only pin the post-migration
shape. This module starts from the pre-migration flag and exercises
``_migrate_legacy_stage`` / ``m_runtime_exit_actions`` directly.
"""

from __future__ import annotations

import json

from loregarden.db.migrations_exit_actions import (
    _migrate_legacy_stage,
    m_runtime_exit_actions,
)
from sqlalchemy import text
from sqlmodel import Session


def _legacy_stages() -> list[dict]:
    return [
        {
            "key": "implement",
            "name": "Implement",
            "order": 1,
            "agent_id": "backend_implementer",
            "gate_required": False,
        },
        {
            "key": "gate",
            "name": "Quality Gate",
            "order": 2,
            "agent_id": "gatekeeper",
            "gate_required": True,
        },
    ]


def _assert_migrated(stages: list[dict]) -> None:
    by_key = {stage["key"]: stage for stage in stages}

    implement = by_key["implement"]
    assert "gate_required" not in implement
    assert implement["exit_actions_enabled"] is False
    assert implement["exit_actions"] == []

    gate = by_key["gate"]
    assert "gate_required" not in gate
    assert gate["exit_actions_enabled"] is True
    assert len(gate["exit_actions"]) == 1
    action = gate["exit_actions"][0]
    assert action["key"] == "legacy-stage-sign-off"
    assert action["label"] == "Approve Quality Gate completion"
    assert action["requirement"] == {
        "kind": "operator_judgment",
        "decision_prompt": "Approve completion of stage 'Quality Gate'.",
    }


def test_migrate_legacy_stage_disables_when_gate_required_false():
    migrated = _migrate_legacy_stage({"key": "plan", "name": "Plan", "gate_required": False})
    assert "gate_required" not in migrated
    assert migrated["exit_actions_enabled"] is False
    assert migrated["exit_actions"] == []


def test_migrate_legacy_stage_adds_operator_judgment_when_gate_required_true():
    migrated = _migrate_legacy_stage({"key": "gate", "name": "Quality Gate", "gate_required": True})
    assert "gate_required" not in migrated
    assert migrated["exit_actions_enabled"] is True
    assert migrated["exit_actions"] == [
        {
            "key": "legacy-stage-sign-off",
            "label": "Approve Quality Gate completion",
            "requirement": {
                "kind": "operator_judgment",
                "decision_prompt": "Approve completion of stage 'Quality Gate'.",
            },
        }
    ]


def test_m_runtime_exit_actions_rewrites_persisted_legacy_stages(isolated_db):
    legacy = json.dumps(_legacy_stages())
    with Session(isolated_db) as session:
        session.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, "
                "source_path, version, built_in, created_at) "
                "VALUES ('tpl-legacy', 'legacy-exit-actions', 'Legacy', '', "
                ":stages, '[]', 'test:legacy', 1, 0, '2026-01-01')"
            ),
            {"stages": legacy},
        )
        session.execute(
            text(
                "INSERT INTO studio_workflows "
                "(id, slug, name, description, stages_json, transitions_json, "
                "created_at, updated_at) "
                "VALUES ('draft-legacy', 'legacy-exit-actions', 'Legacy', '', "
                ":stages, '[]', '2026-01-01', '2026-01-01')"
            ),
            {"stages": legacy},
        )
        session.execute(
            text(
                "INSERT INTO workflow_template_versions "
                "(id, template_id, version, snapshot_json, created_by, "
                "change_note, created_at) "
                "VALUES ('ver-legacy', 'tpl-legacy', 1, :snapshot, "
                "'test', '', '2026-01-01')"
            ),
            {"snapshot": json.dumps({"stages_json": legacy})},
        )
        session.commit()

    with isolated_db.begin() as conn:
        m_runtime_exit_actions(conn)

    with Session(isolated_db) as session:
        for table, column, row_id in (
            ("workflow_templates", "stages_json", "tpl-legacy"),
            ("studio_workflows", "stages_json", "draft-legacy"),
        ):
            raw = session.execute(
                text(f"SELECT {column} FROM {table} WHERE id=:id"),
                {"id": row_id},
            ).scalar_one()
            _assert_migrated(json.loads(raw))

        snapshot = json.loads(
            session.execute(
                text("SELECT snapshot_json FROM workflow_template_versions WHERE id=:id"),
                {"id": "ver-legacy"},
            ).scalar_one()
        )
        _assert_migrated(json.loads(snapshot["stages_json"]))
