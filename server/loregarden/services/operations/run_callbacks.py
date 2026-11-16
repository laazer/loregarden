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
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestrationRunView,
    Ticket,
)
from loregarden.services.artifact_content import parse_content_object
from loregarden.services.block_settlement import AGENT_ASKED_FOR_A_PERSON
from loregarden.services.dependency_hold import admit_named_start
from loregarden.services.external_harness import start_external_orchestration
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
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore")


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

    def target(self, session: Session) -> tuple[RunRef, Ticket]:
        ref = resolve_run_ref(session, self.run_id)
        return ref, OrchestrationCallbackService(session).resolve_ticket(ticket_id=ref.ticket_id)


class StageStart(RunCall):
    stage_key: str
    agent_id: str = ""
    force: bool = False
    ignore_prerequisites: bool = False


class StageComplete(RunCall):
    stage_key: str
    next_agent: str = ""
    next_stage_key: str = ""
    outcome: str = "pass"  # py-org: allow-string - pass | reject, read by complete_stage
    blocking_issues: str = ""
    advance: bool = True
    #: The agent's findings. None (a REST caller that sent none) leaves the
    #: outcome unexamined, as for a person; a list, even empty, is an agent's
    #: claim and a reject must carry a blocking finding (`settle_stage_claim`).
    #: MCP always sends a list.
    findings: list[Any] | None = None


class StageSkip(RunCall):
    stage_key: str
    reason: str = ""


class TicketBlock(RunCall):
    message: str
    stage_key: str = ""
    #: The prepared action, all optional; see `prepared_action_from`.
    tier: str | None = None
    attempted: str | None = None
    prepared: str | None = None
    command: str | None = None
    script_path: str | None = None
    captures: list[str] | None = None


class ArtifactAttach(RunCall):
    kind: str = "log"
    title: str = ""
    #: The content as an object (REST) or as JSON text (MCP); an object wins.
    content: dict[str, Any] | None = None
    content_json: str | None = None


class ApprovalRequest(RunCall):
    stage_key: str
    title: str = ""
    impact: str = ""


class OrchestrationComplete(RunCall):
    status: OrchestrationRunStatus = OrchestrationRunStatus.SUCCEEDED
    message: str = ""


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
