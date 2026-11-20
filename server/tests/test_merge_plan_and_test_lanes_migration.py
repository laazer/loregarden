"""v3 runs one planner instead of three lanes plus synthesis, and test-design breaks its own tests."""

from __future__ import annotations

import json

import pytest
from loregarden.config import settings
from loregarden.db.versions.merge_plan_and_test_lanes import (
    PLAN_SKILL,
    TEST_SKILL,
    m_merge_plan_and_test_lanes,
    merge_stages,
    merge_transitions,
)
from loregarden.models.domain import AgentRun, RunStatus, TicketState
from loregarden.services.skill_service import parse_skill_markdown
from loregarden.skills.registry import get_skill
from sqlalchemy import text
from tests.factories import make_workspace_ticket

TEMPLATE = "studio-loregarden-tdd-v3"
SIGN_OFF = [
    {
        "key": "legacy-stage-sign-off",
        "label": "Approve {name} completion",
        "requirement": {
            "kind": "operator_judgment",
            "decision_prompt": "Approve completion of stage '{name}'.",
        },
    }
]


def _sign_off(name: str) -> list[dict]:
    return json.loads(json.dumps(SIGN_OFF).replace("{name}", name))


#: The live v3 shape (version 20) in the fields this migration reads or writes.
STAGES = [
    {"key": "triage", "name": "Triage", "order": 1, "stage_type": "classify",
     "agent_id": "ticket_scoper"},
    {"key": "plan", "name": "Plan", "order": 2, "stage_type": "parallel", "agent_id": "planner",
     "skill_name": "",
     "parallel_agents": [{"agent_id": "planner", "skill_name": f"plan-{lens}"}
                         for lens in ("simplest", "risk", "seams")]},
    {"key": "plan-synthesis", "name": "Plan synthesis", "order": 3, "stage_type": "agent",
     "agent_id": "planner", "skill_name": "plan-synthesis", "exit_actions_enabled": True,
     "exit_actions": _sign_off("Plan synthesis")},
    {"key": "ui-design", "name": "UI design", "order": 4, "stage_type": "agent",
     "agent_id": "ui_design_decision"},
    {"key": "spec", "name": "Spec", "order": 5, "stage_type": "agent", "agent_id": "spec"},
    {"key": "test-design", "name": "Design Tests", "order": 6, "stage_type": "agent",
     "agent_id": "test_designer", "skill_name": "", "timeout_seconds": 1500},
    {"key": "test-break", "name": "Break Tests", "order": 7, "stage_type": "agent",
     "agent_id": "test_breaker", "timeout_seconds": 1200, "exit_actions_enabled": True,
     "exit_actions": _sign_off("Break Tests")},
    {"key": "implement", "name": "Implement", "order": 8, "stage_type": "classify",
     "agent_id": "backend_implementer"},
    {"key": "gate", "name": "Gate", "order": 9, "stage_type": "gate", "agent_id": "gatekeeper"},
    {"key": "done", "name": "Done", "order": 10, "stage_type": "agent", "terminal": True},
]  # fmt: skip
TRANSITIONS = [
    {"from": "triage", "to": "plan"},
    {"from": "plan", "to": "plan-synthesis"},
    {"from": "ui-design", "to": "spec"},
    {"from": "spec", "to": "test-design"},
    {"from": "test-design", "to": "test-break"},
    {"from": "test-break", "to": "implement"},
    {"from": "test-design", "to": "spec", "when": "reject"},
    {"from": "test-break", "to": "spec", "when": "reject"},
    {"from": "plan-synthesis", "to": "ui-design", "when": "pass"},
    {"from": "gate", "to": "done", "when": "pass"},
]
OLD_KEYS = [stage["key"] for stage in STAGES]


def _insert_template(isolated_db, stages: list[dict] = STAGES) -> None:
    with isolated_db.begin() as conn:
        conn.execute(text("DELETE FROM workflow_templates WHERE slug=:slug"), {"slug": TEMPLATE})
        conn.execute(
            text(
                "INSERT INTO workflow_templates "
                "(id, slug, name, description, stages_json, transitions_json, "
                "source_path, version, built_in, created_at) "
                "VALUES ('tpl', :slug, 'v3', '', :stages, :transitions, 'test', 20, 0, "
                "'2026-01-01')"
            ),
            {
                "slug": TEMPLATE,
                "stages": json.dumps(stages),
                "transitions": json.dumps(TRANSITIONS),
            },
        )


def _pin(isolated_db, ticket_id: str, *, version: int | None = 19, statuses=None) -> None:
    statuses = statuses or {}
    stage_list = [{"key": key, "status": statuses.get(key, "pending")} for key in OLD_KEYS]
    with isolated_db.begin() as conn:
        conn.execute(text("DELETE FROM workflow_instances WHERE ticket_id=:t"), {"t": ticket_id})
        conn.execute(
            text(
                "INSERT INTO workflow_instances (id, ticket_id, template_id, template_version, "
                "current_stage_key, stages_json, created_at, updated_at) "
                "VALUES (:id, :t, 'tpl', :v, 'triage', :st, '2026-01-01', '2026-01-01')"
            ),
            {"id": f"wi-{ticket_id}", "t": ticket_id, "v": version, "st": json.dumps(stage_list)},
        )


def _instance(isolated_db, ticket_id: str) -> tuple[int | None, list[str]]:
    with isolated_db.connect() as conn:
        version, raw = conn.execute(
            text("SELECT template_version, stages_json FROM workflow_instances WHERE ticket_id=:t"),
            {"t": ticket_id},
        ).one()
    return version, [entry["key"] for entry in json.loads(raw)]


def _template(isolated_db) -> tuple[int, dict[str, dict], list[dict], int]:
    with isolated_db.connect() as conn:
        version, stages, transitions = conn.execute(
            text(
                "SELECT version, stages_json, transitions_json FROM workflow_templates WHERE id='tpl'"
            )
        ).one()
        snapshots = conn.execute(
            text("SELECT COUNT(*) FROM workflow_template_versions WHERE template_id='tpl'")
        ).scalar_one()
    by_key = {stage["key"]: stage for stage in json.loads(stages)}
    return version, by_key, json.loads(transitions), snapshots


def _migrate(isolated_db) -> None:
    with isolated_db.begin() as conn:
        m_merge_plan_and_test_lanes(conn)


# ---- the template --------------------------------------------------------------------


def test_plan_becomes_one_planner_run_carrying_the_synthesis_sign_off():
    plan = {stage["key"]: stage for stage in merge_stages(STAGES)}["plan"]

    assert (plan["stage_type"], plan["agent_id"], plan["skill_name"]) == (
        "agent",
        "planner",
        "plan-lenses",
    )
    assert plan["parallel_agents"] == []
    assert plan["exit_actions_enabled"] is True
    assert plan["exit_actions"] == _sign_off("Plan")
    assert plan["timeout_seconds"] == 2400


def test_test_design_carries_the_break_pass_its_sign_off_and_both_budgets():
    test_design = {stage["key"]: stage for stage in merge_stages(STAGES)}["test-design"]

    assert (test_design["agent_id"], test_design["skill_name"]) == (
        "test_designer",
        "test-break-pass",
    )
    assert test_design["exit_actions"] == _sign_off("Design Tests")
    assert test_design["timeout_seconds"] == 2700


def test_the_merged_stages_are_gone_and_the_rest_keep_their_order():
    merged = merge_stages(STAGES)

    keys = [stage["key"] for stage in merged]
    assert keys == [key for key in OLD_KEYS if key not in ("plan-synthesis", "test-break")]
    assert [stage["order"] for stage in merged] == list(range(1, len(merged) + 1))


def test_transitions_route_past_the_removed_stages():
    merged = merge_transitions(TRANSITIONS)

    assert merged == [
        {"from": "triage", "to": "plan"},
        {"from": "ui-design", "to": "spec"},
        {"from": "spec", "to": "test-design"},
        {"from": "test-design", "to": "implement"},
        {"from": "test-design", "to": "spec", "when": "reject"},
        {"from": "plan", "to": "ui-design", "when": "pass"},
        {"from": "gate", "to": "done", "when": "pass"},
    ]
    named = {edge["from"] for edge in merged} | {edge["to"] for edge in merged}
    assert not named & {"plan-synthesis", "test-break"}


def test_the_migration_bumps_the_version_once_and_snapshots_it(isolated_db, caplog):
    _insert_template(isolated_db)

    _migrate(isolated_db)
    _migrate(isolated_db)

    assert "leaving it as it is" not in caplog.text

    version, stages, _, snapshots = _template(isolated_db)
    assert version == 21
    assert "plan-synthesis" not in stages and "test-break" not in stages
    assert snapshots == 1


def test_a_template_a_person_reshaped_is_left_alone_and_said_so(isolated_db, caplog):
    _insert_template(isolated_db, [s for s in STAGES if s["key"] != "test-design"])

    _migrate(isolated_db)

    version, stages, _, snapshots = _template(isolated_db)
    assert (version, snapshots) == (20, 0)
    assert "test-break" in stages
    assert "leaving it as it is" in caplog.text


# ---- which tickets move to the new version --------------------------------------------


@pytest.fixture
def template(isolated_db, db_session):
    _insert_template(isolated_db)
    return db_session


def _ticket(session, name: str, state: TicketState = TicketState.BACKLOG):
    return make_workspace_ticket(session, f"merge-lanes-{name}", state=state)


def test_a_never_run_backlog_ticket_moves_to_the_merged_stages(isolated_db, template):
    ticket = _ticket(template, "fresh")
    _pin(isolated_db, ticket.id)

    _migrate(isolated_db)

    version, keys = _instance(isolated_db, ticket.id)
    assert version == 21
    assert keys == [key for key in OLD_KEYS if key not in ("plan-synthesis", "test-break")]


def test_an_unversioned_never_run_backlog_ticket_is_pinned_to_the_new_version(
    isolated_db, template
):
    ticket = _ticket(template, "unversioned")
    _pin(isolated_db, ticket.id, version=None)

    _migrate(isolated_db)

    assert _instance(isolated_db, ticket.id)[0] == 21


@pytest.mark.parametrize("state", [TicketState.IN_PROGRESS, TicketState.BLOCKED])
def test_a_started_ticket_keeps_its_pin(isolated_db, template, state):
    ticket = _ticket(template, f"started-{state.value}", state=state)
    _pin(isolated_db, ticket.id)

    _migrate(isolated_db)

    assert _instance(isolated_db, ticket.id) == (19, OLD_KEYS)


def test_a_backlog_ticket_that_has_run_an_agent_keeps_its_pin(isolated_db, template):
    ticket = _ticket(template, "ran")
    template.add(
        AgentRun(
            run_code="run_old",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="ticket_scoper",
            stage_key="triage",
            status=RunStatus.SUCCEEDED,
        )
    )
    template.commit()
    _pin(isolated_db, ticket.id)

    _migrate(isolated_db)

    assert _instance(isolated_db, ticket.id) == (19, OLD_KEYS)


def test_a_backlog_ticket_with_any_stage_past_pending_keeps_its_pin(isolated_db, template):
    ticket = _ticket(template, "progressed")
    _pin(isolated_db, ticket.id, statuses={"triage": "done"})

    _migrate(isolated_db)

    assert _instance(isolated_db, ticket.id) == (19, OLD_KEYS)


@pytest.mark.parametrize("skill", [PLAN_SKILL, TEST_SKILL])
def test_the_skills_the_merged_stages_name_are_registered(isolated_db, skill):
    """A stage naming an unregistered skill fails every dispatch (`SkillNotFoundError`)."""
    source = settings.agent_context_dir / "skills" / skill / "SKILL.md"
    parsed = parse_skill_markdown(source.read_text(encoding="utf-8"), slug=skill)

    assert parsed.body and get_skill(skill) == parsed.body
