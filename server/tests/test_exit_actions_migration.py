"""AC-11/AC-12: legacy gate_required stages migrate into typed exit actions.

Fixtures that already embed ``legacy-stage-sign-off`` only pin the post-migration
shape. This module starts from the pre-migration flag and exercises
``_migrate_legacy_stage`` / ``m_runtime_exit_actions`` directly.
"""

from __future__ import annotations

import json

import pytest
from loregarden.db.migrations import apply_migrations
from loregarden.db.migrations_exit_actions import (
    _migrate_legacy_stage,
    m_runtime_exit_actions,
)
from loregarden.models.domain import WorkflowStageDef
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine


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


# --- 0138 runs last -------------------------------------------------------
#
# The live database applied `0138_runtime_exit_actions` from this branch before
# main had 0139..0148, so its id is registered out of numeric order, after
# 0148. On a fresh database it therefore runs after every migration that still
# writes the retired flag — 0133 on older bodies, and 0147's version snapshot,
# which records whatever the template held at the time. These apply the real
# MIGRATIONS list over a pre-migration template to prove the order holds.

_V3 = "studio-loregarden-tdd-v3"


def _legacy_v3_stages() -> list[dict]:
    return [
        {"key": "plan-synthesis", "name": "Plan synthesis", "order": 1, "gate_required": False},
        {"key": "ui-design", "name": "UI Design", "order": 2, "gate_required": False},
        {"key": "gate", "name": "Quality Gate", "order": 3, "gate_required": True},
        # Agentless: a human gate by construction before exit actions (0149).
        {"key": "approval", "name": "Awaiting Approval", "order": 4, "agent_id": ""},
        {"key": "done", "name": "Done", "order": 5, "terminal": True, "gate_required": False},
    ]


@pytest.fixture
def fully_migrated_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ordering.db'}")
    SQLModel.metadata.create_all(engine)
    legacy = json.dumps(_legacy_v3_stages())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, "
                "source_path, version, built_in, created_at) "
                "VALUES ('tpl-v3', :slug, 'v3', '', :stages, '[]', 'test:v3', 1, 0, "
                "'2026-01-01')"
            ),
            {"slug": _V3, "stages": legacy},
        )
        conn.execute(
            text(
                "INSERT INTO studio_workflows "
                "(id, slug, name, description, stages_json, transitions_json, "
                "created_at, updated_at) "
                "VALUES ('draft-v3', 'loregarden-tdd-v3', 'v3', '', :stages, '[]', "
                "'2026-01-01', '2026-01-01')"
            ),
            {"stages": legacy},
        )
    applied = apply_migrations(engine)
    return engine, applied


def _every_stage_list(engine) -> dict[str, list[dict]]:
    """Every persisted stage list, keyed by where it lives."""
    found: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for table in ("workflow_templates", "studio_workflows", "workflow_instances"):
            for row_id, raw in conn.execute(text(f"SELECT id, stages_json FROM {table}")):
                found[f"{table}:{row_id}"] = json.loads(raw or "[]")
        for row_id, raw in conn.execute(
            text("SELECT id, snapshot_json FROM workflow_template_versions")
        ):
            snapshot = json.loads(raw or "{}")
            if "stages_json" in snapshot:
                found[f"workflow_template_versions:{row_id}"] = json.loads(snapshot["stages_json"])
    return found


def _raw_rows(engine) -> dict[str, str]:
    rows: dict[str, str] = {}
    with engine.connect() as conn:
        for table, column in (
            ("workflow_templates", "stages_json"),
            ("studio_workflows", "stages_json"),
            ("workflow_instances", "stages_json"),
            ("workflow_template_versions", "snapshot_json"),
        ):
            for row_id, raw in conn.execute(text(f"SELECT id, {column} FROM {table}")):
                rows[f"{table}:{row_id}"] = raw
    return rows


def test_full_migration_list_leaves_no_stage_with_gate_required(fully_migrated_engine):
    engine, applied = fully_migrated_engine

    # Registered after 0148, and followed only by 0149, which depends on it.
    assert applied[-2:] == ["0138_runtime_exit_actions", "0149_agentless_stage_exit_actions"]
    assert applied.index("0148_chat_message_attachments") < applied.index(
        "0138_runtime_exit_actions"
    )

    stage_lists = _every_stage_list(engine)
    # The version snapshots are the case the ordering is really about: 0147
    # snapshots the template while `gate` still carries the old flag.
    assert any(key.startswith("workflow_template_versions:") for key in stage_lists)
    for where, stages in stage_lists.items():
        for stage in stages:
            assert "gate_required" not in stage, f"{where} kept gate_required: {stage}"
            if "name" in stage:  # instances store key/status only
                WorkflowStageDef.model_validate(stage)

    template = {stage["key"]: stage for stage in stage_lists["workflow_templates:tpl-v3"]}
    # 0147 still extended the design brief before 0138 rewrote the gates...
    assert "question it answers" in template["ui-design"].get("stage_brief", "")
    # ...and every stage that gated before, or that 0133 gated, gates now.
    for key in ("plan-synthesis", "ui-design", "gate", "approval"):
        assert template[key]["exit_actions_enabled"] is True, key
        assert [a["key"] for a in template[key]["exit_actions"]] == ["legacy-stage-sign-off"]
    assert template["done"]["exit_actions_enabled"] is False
    assert template["done"]["exit_actions"] == []


def test_reapplying_0138_to_migrated_stages_changes_nothing(fully_migrated_engine):
    engine, _ = fully_migrated_engine
    before = _raw_rows(engine)

    with engine.begin() as conn:
        m_runtime_exit_actions(conn)

    assert _raw_rows(engine) == before
