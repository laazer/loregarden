"""What a stage *is*, said in words, so an agent does not infer it from agent_id.

A parallel stage and an agentless human gate both show ``agent_id: ""``; on
blobert ticket 26 that was read as "human gate" and a gate approval completed
a three-reviewer stage nothing had run (744). The shape line names the kind
and the next primitive, and it is the same helper behind get_ticket,
complete_stage and begin_external_stage, so the three cannot disagree.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.mcp.ticket_edit_tools import resolve_ticket_payload
from loregarden.models.domain import (
    AgentRun,
    ClassifyRoute,
    ParallelAgentSpec,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.stage_shape import current_stage_shape, describe_stage_shape
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session

STAGES = [
    WorkflowStageDef(key="spec", name="Spec", order=1, agent_id="spec"),
    WorkflowStageDef(
        key="implement",
        name="Implement",
        order=2,
        stage_type="classify",
        agent_id="backend_implementer",
        classify_routes=[
            ClassifyRoute(languages=["ts"], agent_id="frontend_implementer"),
            ClassifyRoute(agent_id="backend_implementer", default=True),
        ],
    ),
    WorkflowStageDef(
        key="script_review",
        name="Script review",
        order=3,
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id=a)
            for a in ("gdscript_reviewer", "static_qa", "architecture_reviewer")
        ],
    ),
    WorkflowStageDef(
        key="ac_gate", name="AC gate", order=4, stage_type="gate", agent_id="ac_gatekeeper"
    ),
    WorkflowStageDef(key="playtest", name="Playtest", order=5),  # a person is the stage
    WorkflowStageDef(key="done", name="Done", order=6, terminal=True),
]


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    workspace = Workspace(slug=f"shape-{uuid4()}", name="Shape", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    template = WorkflowTemplate(
        slug=f"shape-tpl-{uuid4()}",
        name="Shape",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json="[]",
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    ticket = Ticket(
        external_id=f"shape-{uuid4()}",
        workspace_id=workspace.id,
        title="Shape",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="script_review",
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()
    return ticket


def _stage(key: str) -> WorkflowStageDef:
    return next(s for s in STAGES if s.key == key)


def _shapes(session: Session, ticket: Ticket) -> dict[str, str]:
    return {s.key: describe_stage_shape(session, ticket, s) for s in STAGES}


def test_a_parallel_stage_and_a_human_gate_read_differently(db_session, ticket):
    shapes = _shapes(db_session, ticket)

    assert "parallel" in shapes["script_review"]
    assert "not a human gate" in shapes["script_review"]
    assert "gdscript_reviewer, static_qa, architecture_reviewer" in shapes["script_review"]
    assert "human gate" in shapes["playtest"]
    assert "no agent" in shapes["playtest"]


def test_every_shape_names_its_next_primitive(db_session, ticket):
    shapes = _shapes(db_session, ticket)

    assert "begin_external_stage" in shapes["script_review"]
    assert "begin_external_stage" in shapes["implement"]
    assert "begin_external_stage" in shapes["ac_gate"]
    assert "begin_external_stage" in shapes["spec"]
    assert "request_approval" in shapes["playtest"]
    assert "nothing runs" in shapes["done"]


def test_the_shape_says_what_has_run(db_session, ticket):
    assert "none has run" in describe_stage_shape(db_session, ticket, _stage("spec"))

    db_session.add(
        AgentRun(
            run_code="r1",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="spec",
            stage_key="spec",
            status=RunStatus.SUCCEEDED,
        )
    )
    db_session.commit()

    line = describe_stage_shape(db_session, ticket, _stage("spec"))
    assert "1 succeeded run" in line
    assert "request_approval" in line  # a sign-off is now an honest move


def test_get_ticket_carries_the_current_stage_shape(db_session, ticket):
    payload = resolve_ticket_payload(db_session, ticket_id=ticket.id)

    assert payload["current_stage_shape"] == current_stage_shape(db_session, ticket)
    assert payload["current_stage_shape"].startswith("script_review: parallel")
