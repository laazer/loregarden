"""A reject can name the specialist who should redo the work, and that name
steers exactly one dispatch.

Reproduces blob-procedural-sdf-39 (lg-workflow-integrity-765): a pure-Python
ticket whose negative acceptance criterion ("... editor UI ... Frontend ... out
of scope") scores `implementation_frontend` on content. The frontend agent
rejected eight times running, each time asking in prose for the backend agent;
nothing parsed the prose, and content routing re-dispatched the frontend agent
every round.
"""

import json

import pytest
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
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
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.stage_dispatch_prep import consume_scope_reroute_pin
from loregarden.services.stage_report import parse_stage_report
from loregarden.services.studio_routing import resolve_stage_execution
from loregarden.services.workflow_routing import apply_stage_route
from loregarden.services.workflow_state import stages_up_to_done_json
from sqlmodel import Session, select

FRONTEND = "implementation_frontend"
BACKEND = "implementation_backend"

# The live row's criterion, verbatim in substance: it names frontend work only
# to exclude it, which is exactly what the term matcher cannot tell apart.
_NEGATIVE_AC = (
    "AC-UI-39-00: No player-facing or editor UI surfaces; Frontend/R3F, "
    "HTTP bake UI are out of scope"
)


def _report(**fields: object) -> str:
    return (
        "Zero frontend edits — this is backend work.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f"{json.dumps(fields)}\n"
        "<<<END_STAGE_REPORT>>>\n"
    )


@pytest.fixture
def at_implement(db_session: Session) -> tuple[Ticket, list[WorkflowStageDef]]:
    """blob-procedural-sdf-39's shape: a blobert-tdd ticket sitting at implement."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).one()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="reject-names-agent",
        workspace_id=ws.id,
        title="Procedural SDF bake: Python pipeline",
        acceptance_criteria_json=json.dumps([_NEGATIVE_AC]),
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=stages_up_to_done_json(stages, "test-break"),
        )
    )
    db_session.commit()
    return ticket, stages


def _stage(stages: list[WorkflowStageDef], key: str) -> WorkflowStageDef:
    return next(stage for stage in stages if stage.key == key)


def _reject_from_implement(db_session: Session, ticket: Ticket, stdout: str) -> None:
    run = AgentRun(
        run_code="run_reject_names_agent",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=FRONTEND,
        skill_name="",
        stage_key="implement",
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()
    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=stdout, stderr=""
    )
    db_session.refresh(ticket)


def test_parse_surfaces_reroute_to_agent():
    report = parse_stage_report(
        _report(status="needs_rework", confidence=0.9, reroute_to_agent=f" {BACKEND} ")
    )
    assert report is not None
    assert report.reroute_to_agent == BACKEND


def test_parse_reads_a_null_reroute_to_agent_as_none():
    report = parse_stage_report(_report(status="pass", confidence=0.9, reroute_to_agent=None))
    assert report is not None
    assert report.reroute_to_agent is None


def test_content_routing_alone_picks_the_frontend_agent(at_implement):
    """The precondition the defect needs; if this stops holding, the repro below
    proves nothing about content routing."""
    ticket, stages = at_implement
    assert resolve_stage_execution(ticket, _stage(stages, "implement"))[0] == FRONTEND


def test_a_reject_naming_the_backend_agent_steers_the_next_implement_dispatch_once(
    db_session: Session, at_implement
):
    ticket, stages = at_implement
    implement = _stage(stages, "implement")

    _reject_from_implement(
        db_session,
        ticket,
        _report(
            status="needs_rework",
            confidence=0.9,
            reroute_to_stage="test-break",
            reroute_to_agent=BACKEND,
            reroute_context="Pure Python; the backend agent should implement this.",
            unmet_criteria=["AC-1"],
        ),
    )

    assert ticket.workflow_stage_key == "test-break"
    # The stage in between keeps its own agent: the pin is not offered there.
    assert resolve_stage_execution(ticket, _stage(stages, "test-break"))[0] == "test_breaker"
    # It outranks the content score that picked the frontend agent eight times.
    assert resolve_stage_execution(ticket, implement)[0] == BACKEND

    consume_scope_reroute_pin(ticket, BACKEND)

    assert ticket.scope_reroute_agent == ""
    assert resolve_stage_execution(ticket, implement)[0] == FRONTEND


def test_a_reject_naming_an_agent_no_stage_in_the_rework_span_runs_sets_no_pin(
    db_session: Session, at_implement
):
    ticket, stages = at_implement

    _reject_from_implement(
        db_session,
        ticket,
        _report(
            status="needs_rework",
            confidence=0.9,
            reroute_to_stage="test-break",
            reroute_to_agent="security_reviewer",
            reroute_context="Hand this to someone else.",
            unmet_criteria=["AC-1"],
        ),
    )

    assert ticket.workflow_stage_key == "test-break"
    assert ticket.scope_reroute_agent == ""
    assert "security_reviewer" in ticket.blocking_issues
    assert resolve_stage_execution(ticket, _stage(stages, "implement"))[0] == FRONTEND


def test_complete_stage_next_agent_and_the_stage_report_dispatch_the_same_agent(
    db_session: Session, at_implement
):
    """Both reject channels go through apply_stage_route; this pins the shared
    seam with the arguments `OrchestrationCallbacks.complete_stage` passes."""
    ticket, stages = at_implement
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()

    apply_stage_route(
        ticket,
        instance,
        stages,
        [],
        from_key="implement",
        outcome="reject",
        next_stage_key="test-break",
        next_agent=BACKEND,
        strict=True,
    )

    assert ticket.workflow_stage_key == "test-break"
    assert ticket.scope_reroute_agent == BACKEND
    assert resolve_stage_execution(ticket, _stage(stages, "implement"))[0] == BACKEND


def test_a_pass_cannot_pin_an_agent(db_session: Session, at_implement):
    """next_agent on a forward pass is ignored by design; the pin must be too."""
    ticket, stages = at_implement
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()

    apply_stage_route(
        ticket,
        instance,
        stages,
        [],
        from_key="implement",
        outcome="pass",
        next_agent=BACKEND,
    )

    assert ticket.scope_reroute_agent == ""
