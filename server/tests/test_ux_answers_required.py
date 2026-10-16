"""The design stage cannot pass without the UX answers the ticket's PR is built from.

`publish_tree` assembles a tree PR's "User-facing surfaces" section from
recorded answers. A `ui-design` pass that recorded none left the PR to fail
its check in CI. The pass is sent back to the same stage with the reason, under
the rework loop cap.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.agents.registry import UI_DESIGN_AGENT_ID
from loregarden.mcp.ux_answers_tools import HANDLERS
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.rework_feedback import MAX_REWORK_REROUTES, render_rework_feedback
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session

DESIGN = "ui-design"
IMPLEMENT = "implement"
STAGES = [
    WorkflowStageDef(key="plan", name="Plan", order=1, agent_id="planner"),
    WorkflowStageDef(key=DESIGN, name="UI design", order=2, agent_id=UI_DESIGN_AGENT_ID),
    WorkflowStageDef(key=IMPLEMENT, name="Implement", order=3, agent_id="frontend_implementer"),
    WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
]


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path) -> Ticket:
    workspace = Workspace(slug=f"ux-{uuid4()}", name="Ux", repo_path=str(tmp_path))
    template = WorkflowTemplate(
        slug=f"ux-tpl-{uuid4()}",
        name="Ux",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps(
            [
                {"from": "plan", "to": DESIGN, "when": "pass"},
                {"from": DESIGN, "to": IMPLEMENT, "when": "pass"},
                {"from": IMPLEMENT, "to": "done", "when": "pass"},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()
    workspace.workflow_template_id = template.id
    db_session.add(workspace)
    db_session.commit()
    ticket = Ticket(
        external_id=f"ux-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Group the monitor findings by ticket",
        state=TicketState.IN_PROGRESS,
        workflow_stage_key=DESIGN,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=DESIGN,
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()
    return ticket


def _passes(session: Session, ticket: Ticket, *, agent_id: str = UI_DESIGN_AGENT_ID) -> None:
    parent = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(parent)
    session.commit()
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=DESIGN,
        status=RunStatus.RUNNING,
        orchestration_run_id=parent.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    report = json.dumps({"status": "pass", "confidence": 0.9})
    OrchestrationService(session).complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=f"<<<LOREGARDEN_STAGE_REPORT>>>\n{report}\n<<<END_STAGE_REPORT>>>\n",
    )
    parent.status = OrchestrationRunStatus.BLOCKED
    session.add(parent)
    session.commit()
    session.refresh(ticket)


def _stages(session: Session, ticket: Ticket) -> dict[str, StageStatus]:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)


def _record(session: Session, ticket: Ticket, **answers: str) -> None:
    HANDLERS["loregarden_record_ux_answers"](session, {"ticket_id": ticket.external_id, **answers})


def test_a_design_pass_with_no_answers_goes_back_to_the_design_stage(db_session, ticket):
    _passes(db_session, ticket)

    assert ticket.workflow_stage_key == DESIGN
    assert _stages(db_session, ticket)[DESIGN] is StageStatus.PENDING, "re-armed, not passed"
    told = render_rework_feedback(db_session, ticket, DESIGN)
    assert "loregarden_record_ux_answers" in told, told
    assert ticket.next_agent == UI_DESIGN_AGENT_ID


def test_recorded_answers_let_the_design_stage_pass(db_session, ticket):
    _record(
        db_session,
        ticket,
        question="Which monitor findings need me now, and on which ticket?",
        action="Open the ticket a finding group names",
        real_data="138 findings grouped into 11 tickets on the sandbox snapshot",
    )

    _passes(db_session, ticket)

    assert _stages(db_session, ticket)[DESIGN] is StageStatus.DONE


def test_a_recorded_waiver_is_an_answer(db_session, ticket):
    _record(
        db_session,
        ticket,
        no_change_reason="Renames a prop inside the findings list; nothing on screen changes",
    )

    _passes(db_session, ticket)

    assert _stages(db_session, ticket)[DESIGN] is StageStatus.DONE


def test_other_agents_are_not_asked_for_answers(db_session, ticket):
    _passes(db_session, ticket, agent_id="planner")

    assert _stages(db_session, ticket)[DESIGN] is StageStatus.DONE


def test_a_design_stage_that_never_records_them_stops_at_the_loop_cap(db_session, ticket):
    for _ in range(MAX_REWORK_REROUTES + 1):
        _passes(db_session, ticket)

    assert _stages(db_session, ticket)[DESIGN] is StageStatus.BLOCKED
    assert ticket.state is TicketState.BLOCKED
