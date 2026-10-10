"""Every run-scoped MCP tool takes the run as the agent was told it.

A stage prompt names its run twice: `# Run: run_xxx` (the agent run's code) at the
top, and `orchestration_run_id` further down. The tools resolved only the second,
so 62% of agent runs that attached anything were first refused with "Orchestration
run not found" — 1,874 failed calls across 48 tickets — and some of those runs
ended mid-detour without a stage report.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Artifact
from sqlmodel import select
from tests.factories import make_agent_run, make_orchestration_run, make_workspace_ticket


@pytest.fixture(name="runs")
def runs_fixture(db_session):
    ticket = make_workspace_ticket(db_session, "run-ref-ticket")
    orch = make_orchestration_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="orch_a1b2c3",
    )
    agent = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="run_d4e5f6",
        orchestration_run_id=orch.id,
        stage_key="implement",
        skill_name="",
        status="running",
        command="",
        stdout="",
        stderr="",
    )
    standalone = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="run_0a0b0c",
        stage_key="plan",
        skill_name="",
        status="running",
        command="",
        stdout="",
        stderr="",
    )
    return {
        "ticket": ticket,
        "orch_id": orch.id,
        "orch_code": orch.run_code,
        "agent_id": agent.id,
        "agent_code": agent.run_code,
        "standalone_code": standalone.run_code,
    }


def _attach(db_session, run_ref: str) -> dict:
    return json.loads(
        execute_tool(
            db_session,
            "loregarden_attach_artifact",
            {"run_id": run_ref, "kind": "log", "title": f"via {run_ref}"},
        )
    )


@pytest.mark.parametrize("form", ["orch_id", "orch_code", "agent_id", "agent_code"])
def test_attach_artifact_accepts_every_name_the_prompt_gives_a_run(db_session, runs, form):
    result = _attach(db_session, runs[form])

    artifact = db_session.get(Artifact, result["artifact_id"])
    assert artifact is not None
    assert artifact.ticket_id == runs["ticket"].id


def test_a_standalone_stage_run_can_attach_to_its_ticket(db_session, runs):
    """103 ticketed agent runs this month had no orchestration; they still own a ticket."""
    result = _attach(db_session, runs["standalone_code"])

    artifact = db_session.get(Artifact, result["artifact_id"])
    assert artifact is not None
    assert artifact.ticket_id == runs["ticket"].id


@pytest.mark.parametrize("form", ["orch_id", "agent_id", "agent_code"])
def test_lifecycle_tools_resolve_an_agent_run_to_its_orchestration(db_session, runs, form):
    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_complete_orchestration",
            {"run_id": runs[form], "status": "failed", "message": "test"},
        )
    )

    assert result["id"] == runs["orch_id"]


def test_lifecycle_tool_on_a_standalone_run_says_why_it_cannot(db_session, runs):
    with pytest.raises(ValueError, match="no orchestration run"):
        execute_tool(
            db_session,
            "loregarden_complete_orchestration",
            {"run_id": runs["standalone_code"], "status": "failed"},
        )


def test_an_unknown_run_names_the_forms_it_accepts(db_session, runs):
    with pytest.raises(ValueError, match="orchestration_run_id") as raised:
        _attach(db_session, "run_ffffff")

    assert "run_ffffff" in str(raised.value)
    assert (
        db_session.exec(select(Artifact).where(Artifact.title == "via run_ffffff")).first() is None
    )


def test_an_ambiguous_run_code_is_refused_rather_than_guessed(db_session, runs):
    ticket = runs["ticket"]
    make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code=runs["agent_code"],
        stage_key="verify",
        skill_name="",
        status="running",
        command="",
        stdout="",
        stderr="",
    )

    with pytest.raises(ValueError, match="ambiguous"):
        _attach(db_session, runs["agent_code"])


# The run an attachment came from is stored on the row. Before, `run_id` was
# resolved to find the ticket and then dropped, so half the rows in `artifacts`
# could not be tied to the stage that produced them.


@pytest.mark.parametrize(
    ("form", "expected"), [("agent_id", "agent_id"), ("agent_code", "agent_id")]
)
def test_attach_artifact_records_the_agent_run_it_names(db_session, runs, form, expected):
    result = _attach(db_session, runs[form])

    assert db_session.get(Artifact, result["artifact_id"]).run_id == runs[expected]


def test_an_orchestration_id_names_no_single_run_so_none_is_recorded(db_session, runs):
    result = _attach(db_session, runs["orch_id"])

    assert db_session.get(Artifact, result["artifact_id"]).run_id is None


def test_the_supervised_run_is_preferred_to_the_orchestration_id(db_session, runs):
    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_attach_artifact",
            {"run_id": runs["orch_id"], "kind": "plan", "title": "plan"},
            orchestrated=True,
            run_id=runs["agent_id"],
        )
    )

    assert db_session.get(Artifact, result["artifact_id"]).run_id == runs["agent_id"]


def test_a_supervised_run_of_another_ticket_is_not_this_attachments_source(db_session, runs):
    other = make_workspace_ticket(db_session, "other-ticket")
    foreign = make_agent_run(
        db_session,
        workspace_id=other.workspace_id,
        ticket_id=other.id,
        run_code="run_99aa99",
        stage_key="plan",
        skill_name="",
        status="running",
        command="",
        stdout="",
        stderr="",
    )

    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_attach_artifact",
            {"run_id": runs["agent_code"], "kind": "plan", "title": "plan"},
            orchestrated=True,
            run_id=foreign.id,
        )
    )

    assert db_session.get(Artifact, result["artifact_id"]).run_id == runs["agent_id"]


def test_attach_evidence_records_the_agent_run(db_session, runs):
    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_attach_evidence",
            {
                "run_id": runs["agent_code"],
                "evidence_kind": "test_red_green",
                "title": "red then green",
            },
        )
    )

    assert db_session.get(Artifact, result["artifact_id"]).run_id == runs["agent_id"]
