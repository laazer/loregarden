"""0149: an agentless stage that was a human gate stays one.

Before exit actions, a non-terminal stage with no agent parked for a person by
construction. Exit actions gate only on an authored action, and `0138`
converted only the explicit `gate_required` flag — so without 0149,
`loregarden-tdd`'s `approval` and `blobert-tdd`'s `playtest` would complete
themselves with nobody asked.
"""

from __future__ import annotations

import json

import yaml
from loregarden.config import settings
from loregarden.db.migrations_agentless_gates import (
    _gate_agentless_stages,
    m_agentless_stage_exit_actions,
)
from loregarden.models.domain import WorkflowStageDef
from loregarden.services.exit_actions import resolve_exit_actions
from sqlalchemy import text

APPROVAL = {"key": "approval", "name": "Awaiting Approval", "agent_id": "", "optional": True}


def _stages_after(stages: list[dict]) -> dict[str, dict]:
    rewritten = _gate_agentless_stages(json.dumps(stages))
    return {stage["key"]: stage for stage in json.loads(rewritten or json.dumps(stages))}


def test_an_agentless_stage_gains_an_operator_judgment_action():
    stage = _stages_after([dict(APPROVAL)])["approval"]

    assert stage["exit_actions_enabled"] is True
    resolution = resolve_exit_actions(WorkflowStageDef.model_validate(stage), None)
    assert [a.action_key for a in resolution.human_required_actions] == ["legacy-stage-sign-off"]
    assert resolution.human_required_actions[0].requirement.decision_prompt == (
        "Approve completion of stage 'Awaiting Approval'."
    )


def test_stages_that_were_never_human_gates_are_untouched():
    stages = [
        {"key": "implement", "name": "Implement", "agent_id": "backend_implementer"},
        {"key": "review", "name": "Review", "agent_id": "", "stage_type": "parallel"},
        {"key": "route", "name": "Route", "agent_id": "", "stage_type": "classify"},
        {"key": "gate", "name": "Gate", "agent_id": "", "stage_type": "gate"},
        {"key": "verify", "name": "Verify", "agent_id": "", "stage_type": "verify"},
        {"key": "done", "name": "Done", "agent_id": ""},
        {"key": "finish", "name": "Finish", "agent_id": "", "terminal": True},
    ]

    assert _gate_agentless_stages(json.dumps(stages)) is None


def test_a_stage_that_already_authors_exit_actions_is_left_alone():
    authored = {
        **APPROVAL,
        "exit_actions_enabled": True,
        "exit_actions": [
            {
                "key": "ship-it",
                "label": "Ship it",
                "requirement": {"kind": "authority", "authority_scope": "release:publish"},
            }
        ],
    }

    assert _gate_agentless_stages(json.dumps([authored])) is None


def test_the_migration_rewrites_templates_and_version_snapshots_once(isolated_db):
    stages = json.dumps([dict(APPROVAL), {"key": "done", "name": "Done", "agent_id": ""}])
    with isolated_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, "
                "source_path, version, built_in, created_at) "
                "VALUES ('tpl', 'loregarden-tdd', 'L', '', :stages, '[]', 'test', 1, 0, "
                "'2026-01-01')"
            ),
            {"stages": stages},
        )
        conn.execute(
            text(
                "INSERT INTO workflow_template_versions "
                "(id, template_id, version, snapshot_json, created_by, change_note, created_at) "
                "VALUES ('ver', 'tpl', 1, :snapshot, 'test', '', '2026-01-01')"
            ),
            {"snapshot": json.dumps({"stages_json": stages})},
        )

    def _read() -> tuple[str, str]:
        with isolated_db.connect() as conn:
            template = conn.execute(
                text("SELECT stages_json FROM workflow_templates WHERE id='tpl'")
            ).scalar_one()
            version = conn.execute(
                text("SELECT snapshot_json FROM workflow_template_versions WHERE id='ver'")
            ).scalar_one()
        return template, version

    with isolated_db.begin() as conn:
        m_agentless_stage_exit_actions(conn)
    first = _read()
    template_stages, version_snapshot = first
    for raw in (template_stages, json.loads(version_snapshot)["stages_json"]):
        by_key = {stage["key"]: stage for stage in json.loads(raw)}
        assert by_key["approval"]["exit_actions_enabled"] is True
        assert "exit_actions_enabled" not in by_key["done"]

    with isolated_db.begin() as conn:
        m_agentless_stage_exit_actions(conn)
    assert _read() == first


def test_every_seed_workflow_authors_its_human_gates():
    """Seeds load after migrations on a fresh database, so 0149 never sees them."""
    paths = sorted(settings.workflow_templates_dir.glob("*.yaml"))
    assert paths, settings.workflow_templates_dir
    for path in paths:
        stages = yaml.safe_load(path.read_text())["stages"]
        assert _gate_agentless_stages(json.dumps(stages)) is None, path.name
