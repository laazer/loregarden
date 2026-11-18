"""Starting an orchestration and the calls a run makes back, as operations.

These were written twice, once as MCP tools and once as the REST callbacks
under `/api/orchestration/runs/{run_id}/`, and the two had drifted: the REST
`start_stage` reserved no slot and could not start past a prerequisite with a
recorded override, its `complete_stage` dropped findings, its `block` dropped
the prepared action and never assessed the handover, and its `attach_artifact`
recorded no source run.
Each is now one operation, offered on every transport with the same rules.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import (
    BlockOrigin,
    FindingDisposition,
    HumanActionTier,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestrationRunView,
    StageCompletionOutcome,
    Ticket,
)
from loregarden.services.artifact_content import parse_content_object
from loregarden.services.artifact_kind_owners import refuse_if_writer_owned
from loregarden.services.block_settlement import AGENT_ASKED_FOR_A_PERSON
from loregarden.services.dependency_hold import admit_named_start
from loregarden.services.external_harness import start_external_orchestration
from loregarden.services.operations.advertised import items_like, one_of
from loregarden.services.operations.spec import Caller, Effect, Operation, OperationId
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.prepared_action import assess_handover, prepared_action_from
from loregarden.services.run_resolution import RunRef, attributed_agent_run_id, resolve_run_ref
from loregarden.services.stage_report import findings_from
from loregarden.services.stage_shape import current_stage_shape
from loregarden.services.work_admission import (
    OrchestrationStart,
    WorkQueued,
    run_admitted,
    start_orchestration_admitted,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


def orchestration_run_view(run: OrchestrationRun) -> OrchestrationRunView:
    return OrchestrationRunView(
        id=run.id,
        run_code=run.run_code,
        ticket_id=run.ticket_id,
        driver=run.driver,
        profile_slug=run.profile_slug,
        external_harness=run.external_harness,
        status=run.status,
        current_stage_key=run.current_stage_key,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


class RunCall(BaseModel):
    """A call naming its run in any form an agent holds (`resolve_run_ref`)."""

    model_config = _LENIENT

    run_id: str
    """This run: the `agent_run` code from the prompt (run_…), the agent run's UUID, or
    `orchestration_run_id`. A standalone stage run is accepted.
    """

    def target(self, session: Session) -> tuple[RunRef, Ticket]:
        ref = resolve_run_ref(session, self.run_id)
        return ref, OrchestrationCallbackService(session).resolve_ticket(ticket_id=ref.ticket_id)


class StageStart(RunCall):
    stage_key: str
    """Workflow stage key."""
    agent_id: str = ""
    """Optional agent id override."""
    force: bool = False
    """Dispatch the stage even though it has reached its retry budget. The override is recorded."""
    ignore_prerequisites: bool = False
    """Start the stage although the ticket waits on prerequisites that are not done and landed. The
    override is recorded.
    """


class StageFinding(BaseModel):
    """One finding as `complete_stage` asks for it. `findings_from` parses what
    an agent actually sent, a missing part and all; this is what it is told."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    summary: str
    """The defect in one sentence."""
    blocking: bool = False
    """Whether it sends the work back."""
    trigger: str = ""
    """How a person or caller reaches it."""
    path: str = ""
    """What runs from the trigger to the defect."""
    impact: str = ""
    """What a person using it meets."""
    disposition: FindingDisposition | None = None
    """Where a finding that does not block went."""
    ticket: str = ""
    """The ticket, for assigned_ticket."""


class StageComplete(RunCall):
    stage_key: str
    """Workflow stage key."""
    next_agent: str = ""
    """Optional next agent hint."""
    next_stage_key: str = ""
    """Optional explicit target stage (for upstream rework)."""
    # Text, read by complete_stage, which words its own refusal.
    outcome: str = Field(
        default=StageCompletionOutcome.PASS.value, json_schema_extra=one_of(StageCompletionOutcome)
    )
    """Stage outcome."""
    blocking_issues: str = ""
    """Optional rework notes when routing upstream."""
    advance: bool = True
    #: The agent's findings. None (a REST caller that sent none) leaves the
    #: outcome unexamined, as for a person; a list, even empty, is an agent's
    #: claim and a reject must carry a blocking finding (`settle_stage_claim`).
    #: MCP always sends a list.
    findings: list[Any] | None = Field(default=None, json_schema_extra=items_like(StageFinding))
    """What a review, verify or gate stage found. A reject from one of those stands only on a
    finding with blocking=true and a non-empty trigger, path and impact; any other finding names
    its disposition.
    """


class StageSkip(RunCall):
    stage_key: str
    """Workflow stage key to skip."""
    reason: str = ""
    """Why this ticket does not need the stage. Recorded on the stage and shown in the workflow
    pane; a skip with no reason reads as an unexplained gap.
    """


class TicketBlock(RunCall):
    message: str
    """Blocking message for operators."""
    stage_key: str = ""
    """Optional stage key context."""
    #: The prepared action, all optional; see `prepared_action_from`.
    tier: str | None = Field(default=None, json_schema_extra=one_of(HumanActionTier))
    """How much of the human step you removed. 'agent_attempted': you ran it and the result does
    not answer the question. 'one_click': you committed a script the control plane can run on a
    person's say-so. 'manual': a person must be present (device, editor, judgement).
    """
    attempted: str | None = None
    """What you already ran, and why its result does not answer the question. 'I could not do this'
    is only complete paired with this.
    """
    prepared: str | None = None
    """What you built to reduce the remaining work — harness, fixture, scene, scripted capture."""
    command: str | None = None
    """The invocation to show the person. Shown for every tier; what actually runs is script_path.
    """
    script_path: str | None = None
    """Repository-relative path of the committed script `command` invokes. Required for tier
    'one_click' — the control plane runs this file, never the command string.
    """
    captures: list[str] | None = None
    """What running it produces, one per entry."""


class ArtifactAttach(RunCall):
    kind: str = "log"
    """Artifact kind, e.g. log, diff, test."""
    title: str = ""
    """Short artifact title."""
    #: The content as an object (REST) or as JSON text (MCP); an object wins.
    content: dict[str, Any] | None = None
    content_json: str | None = None
    """Optional JSON string payload."""


class ApprovalRequest(RunCall):
    stage_key: str
    """Workflow stage key."""
    title: str = ""
    """Approval title."""
    impact: str = ""
    """Impact / description for the operator."""


class OrchestrationComplete(RunCall):
    status: OrchestrationRunStatus = OrchestrationRunStatus.SUCCEEDED
    """Final orchestration status."""
    message: str = ""
    """Optional completion message."""


def _start_orchestration(
    session: Session, start: OrchestrationStart, _caller: Caller
) -> OrchestrationRunView:
    svc = OrchestrationCallbackService(session)
    if start.external_harness is not None:
        # An outside harness spawns nothing here, so it takes no slot: the lane
        # pool bounds the agents this machine runs, and reserving one would
        # idle it and queue the operator behind capacity nobody uses.
        ticket = svc.resolve_ticket(ticket_id=start.ticket_id)
        run = start_external_orchestration(session, ticket, harness=start.external_harness)
        return orchestration_run_view(run)
    reservation, run = start_orchestration_admitted(session, svc, start)
    if not reservation.admitted:
        raise WorkQueued(reservation, ticket_id=start.ticket_id)
    return orchestration_run_view(run)


def _start_stage(session: Session, call: StageStart, caller: Caller) -> dict[str, Any]:
    ref, ticket = call.target(session)
    run = ref.require_orchestration("stage_start")
    admit_named_start(
        session,
        ticket,
        stage_key=call.stage_key,
        ignore_prerequisites=call.ignore_prerequisites,
        actor=caller.transport.value,
    )
    svc = OrchestrationCallbackService(session)
    reservation, _ = run_admitted(
        session,
        ticket,
        stage_key=call.stage_key,
        force=call.force,
        orchestration_run_id=run.id,
        start=lambda: svc.start_stage(
            run, ticket, stage_key=call.stage_key, agent_id=call.agent_id, force=call.force
        ),
    )
    if not reservation.admitted:
        raise WorkQueued(reservation, stage_key=call.stage_key)
    # The slot binds to the orchestration column: `run` is an OrchestrationRun,
    # and the agent-run column would name a row absent from the table it
    # references (`run_admitted` leaves the choice to the caller for that reason).
    reservation.bind(orchestration_run_id=run.id)
    session.refresh(ticket)
    return {"ok": True, "stage_key": call.stage_key, "ticket_state": ticket.state.value}


def _complete_stage(session: Session, call: StageComplete, _caller: Caller) -> dict[str, Any]:
    ref, ticket = call.target(session)
    OrchestrationCallbackService(session).complete_stage(
        ref.require_orchestration("stage_complete"),
        ticket,
        stage_key=call.stage_key,
        next_agent=call.next_agent,
        next_stage_key=call.next_stage_key,
        outcome=call.outcome,
        blocking_issues=call.blocking_issues,
        advance=call.advance,
        findings=None if call.findings is None else findings_from(call.findings),
    )
    session.refresh(ticket)
    return {
        "ok": True,
        "stage_key": call.stage_key,
        "workflow_stage_key": ticket.workflow_stage_key,
        "ticket_state": ticket.state.value,
        # What the cursor now sits on, in words: a parallel stage and a human
        # gate look identical by agent_id alone (745).
        "next_stage_shape": current_stage_shape(session, ticket),
    }


def _skip_stage(session: Session, call: StageSkip, _caller: Caller) -> dict[str, Any]:
    ref, ticket = call.target(session)
    OrchestrationCallbackService(session).skip_stage(
        ref.require_orchestration("stage_skip"),
        ticket,
        stage_key=call.stage_key,
        reason=call.reason,
    )
    return {"ok": True, "stage_key": call.stage_key}


def _block_ticket(session: Session, call: TicketBlock, _caller: Caller) -> dict[str, Any]:
    ref, ticket = call.target(session)
    action = prepared_action_from(call.model_dump())
    OrchestrationCallbackService(session).block_ticket(
        ref.require_orchestration("ticket_block"),
        ticket,
        # The agent's own words: the one path where a block may legitimately
        # be a handover of human work.
        origin=BlockOrigin.AGENT,
        repair=AGENT_ASKED_FOR_A_PERSON,
        stage_key=call.stage_key,
        message=call.message,
        prepared_action=action,
    )
    # The findings come back in the reply rather than only onto the approval,
    # so an agent that handed over a chore is told while it can still act.
    assessment = assess_handover(message=call.message, action=action)
    return {
        "ok": True,
        "ticket_state": ticket.state.value,
        "handover_ok": assessment.ok,
        "handover_findings": assessment.findings,
    }


def _attach_artifact(session: Session, call: ArtifactAttach, caller: Caller) -> dict[str, Any]:
    # First, before the run is resolved or the payload is read: a writer-owned
    # kind is refused by NAME, so an agent cannot mint a record a detector reads
    # as proof of its own work (lg-milestone-that-1423). This operation is the
    # one door `loregarden_attach_artifact` and the REST callback share.
    refuse_if_writer_owned(call.kind)
    # Ticket-scoped: a standalone stage run, with no orchestration, may attach.
    ref, ticket = call.target(session)
    content = call.content if call.content is not None else parse_content_object(call.content_json)
    artifact = OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind=call.kind,
        title=call.title,
        content=content,
        run_id=attributed_agent_run_id(session, ref, transport_run_id=caller.run_id),
    )
    return {"ok": True, "artifact_id": artifact.id}


def _request_approval(session: Session, call: ApprovalRequest, _caller: Caller) -> dict[str, Any]:
    # Ticket-scoped, like attaching an artifact.
    _ref, ticket = call.target(session)
    approval = OrchestrationCallbackService(session).request_approval(
        ticket, stage_key=call.stage_key, title=call.title, impact=call.impact
    )
    return {"ok": True, "approval_id": approval.id}


def _complete_orchestration(
    session: Session, call: OrchestrationComplete, _caller: Caller
) -> OrchestrationRunView:
    ref, ticket = call.target(session)
    run = OrchestrationCallbackService(session).complete_orchestration(
        ref.require_orchestration("orchestration_complete"),
        ticket,
        status=call.status,
        message=call.message,
    )
    return orchestration_run_view(run)


def _write(operation_id: OperationId, args: type[BaseModel], handler: Any) -> Operation:
    return Operation(id=operation_id, effect=Effect.WRITE, args=args, handler=handler)


RUN_CALLBACKS = (
    _write(OperationId.ORCHESTRATION_START, OrchestrationStart, _start_orchestration),
    _write(OperationId.STAGE_START, StageStart, _start_stage),
    _write(OperationId.STAGE_COMPLETE, StageComplete, _complete_stage),
    _write(OperationId.STAGE_SKIP, StageSkip, _skip_stage),
    _write(OperationId.TICKET_BLOCK, TicketBlock, _block_ticket),
    _write(OperationId.ARTIFACT_ATTACH, ArtifactAttach, _attach_artifact),
    _write(OperationId.APPROVAL_REQUEST, ApprovalRequest, _request_approval),
    _write(OperationId.ORCHESTRATION_COMPLETE, OrchestrationComplete, _complete_orchestration),
)
