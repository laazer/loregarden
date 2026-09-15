"""A workflow-gate approval may not stand in for a stage's own agent run.

On blobert ticket 26 an external driver parked on the three-reviewer
`script_review` stage, raised a gate approval on it over MCP, a person
approved it, and the ticket reached `ac_gate` with zero reviewer runs. The
built-in driver already refuses to open a human gate on a stage that has an
agent; the MCP path and the resolver did not. Three shapes pinned here: an
unrun agent stage is refused at request time and at resolve time, a human
(agentless) gate is still fine, and a sign-off after the run is still fine.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
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
from loregarden.services.orchestration import ApprovalService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select

REVIEW = "script_review"
HUMAN_GATE = "playtest"
IMPLEMENT = "implement"


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    """A ticket parked on a three-reviewer parallel stage, nothing run yet."""
    workspace = Workspace(
        slug=f"gate-work-{uuid4()}", name="Gate needs work", repo_path=str(tmp_path)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    stages = [
        WorkflowStageDef(key=IMPLEMENT, name="Implement", order=1, agent_id="backend_implementer"),
        WorkflowStageDef(
            key=REVIEW,
            name="Script review",
            order=2,
            stage_type="parallel",
            parallel_agents=[
                ParallelAgentSpec(agent_id=a)
                for a in ("gdscript_reviewer", "static_qa", "architecture_reviewer")
            ],
        ),
        # A person is the stage: no agent, default stage_type — the live `playtest` shape.
        WorkflowStageDef(key=HUMAN_GATE, name="Playtest", order=3),
        WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"gate-work-tpl-{uuid4()}",
        name="Gate needs work",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(
            [
                {"from": IMPLEMENT, "to": REVIEW, "when": "pass"},
                {"from": REVIEW, "to": HUMAN_GATE, "when": "pass"},
                {"from": HUMAN_GATE, "to": "done", "when": "pass"},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"gate-work-{uuid4()}",
        workspace_id=workspace.id,
        title="Reviewed by nobody",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=REVIEW,
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=REVIEW,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()
    return ticket


def _succeeded_run(session: Session, ticket: Ticket, stage_key: str) -> None:
    session.add(
        AgentRun(
            run_code=f"r-{uuid4().hex[:6]}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="gdscript_reviewer",
            stage_key=stage_key,
            status=RunStatus.SUCCEEDED,
        )
    )
    session.commit()


def _pending_gate(session: Session, ticket: Ticket, stage_key: str) -> Approval:
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        title="Approve",
        stage_key=stage_key,
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval


def _stage_status(session: Session, ticket: Ticket, stage_key: str) -> StageStatus:
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    template = session.get(WorkflowTemplate, instance.template_id)
    stages = [WorkflowStageDef.model_validate(s) for s in json.loads(template.stages_json)]
    return parse_stage_map(instance, stages)[stage_key]


def test_request_approval_refuses_an_unrun_agent_stage(db_session: Session, ticket: Ticket):
    with pytest.raises(ValueError, match="none has succeeded"):
        OrchestrationCallbackService(db_session).request_approval(ticket, stage_key=REVIEW)

    assert db_session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).all() == []
    assert _stage_status(db_session, ticket, REVIEW) == StageStatus.PENDING


def test_request_approval_names_the_honest_moves(db_session: Session, ticket: Ticket):
    with pytest.raises(ValueError) as refused:
        OrchestrationCallbackService(db_session).request_approval(ticket, stage_key=REVIEW)
    assert "start_stage" in str(refused.value)
    assert "skip_stage" in str(refused.value)


def test_a_human_gate_still_opens(db_session: Session, ticket: Ticket):
    approval = OrchestrationCallbackService(db_session).request_approval(
        ticket, stage_key=HUMAN_GATE
    )
    assert approval.status is ApprovalStatus.PENDING
    assert _stage_status(db_session, ticket, HUMAN_GATE) == StageStatus.AWAITING


def test_a_sign_off_after_the_run_still_opens(db_session: Session, ticket: Ticket):
    _succeeded_run(db_session, ticket, REVIEW)

    approval = OrchestrationCallbackService(db_session).request_approval(ticket, stage_key=REVIEW)

    assert approval.status is ApprovalStatus.PENDING


def test_resolving_a_gate_on_an_unrun_agent_stage_is_refused(db_session: Session, ticket: Ticket):
    """Belt and braces: a row that reached the table by any other path."""
    approval = _pending_gate(db_session, ticket, REVIEW)

    with pytest.raises(ValueError, match="none has succeeded"):
        ApprovalService(db_session).resolve(approval.id, approved=True)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.PENDING
    assert _stage_status(db_session, ticket, REVIEW) == StageStatus.PENDING


def test_rejecting_such_a_gate_is_still_allowed(db_session: Session, ticket: Ticket):
    """Sending the work back never invents work; only approval does."""
    approval = _pending_gate(db_session, ticket, REVIEW)

    ApprovalService(db_session).resolve(approval.id, approved=False)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.REJECTED
