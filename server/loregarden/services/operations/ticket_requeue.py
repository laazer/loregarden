"""Requeue: clear a ticket's block and hand the stage back its dispatch budget.

Written twice before. The MCP tool called `requeue_stage`, which demands a
reason, records it on the ticket and leaves the state unpinned. The inbox's
"run" on a human-action card did the same job by hand: it cleared
`blocking_issues`, chose IN_PROGRESS and refreshed the budget, but recorded no
reason, left the stage BLOCKED in the stage map and its `block_kind` set. The
card's route now runs this operation for that part, with a reason derived from
the card, and keeps resolving the card itself (approvals are a person's).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import StageStatus, Ticket, TicketState
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.requeue import requeue_stage
from loregarden.services.run_concurrency import find_active_stage_run
from loregarden.services.ticket_state_payload import ticket_state_payload
from loregarden.services.workflow_state import parse_stage_map
from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore")


class RequeueCall(BaseModel):
    model_config = _LENIENT

    #: A UUID or an external id.
    ticket_id: str
    #: Required by `requeue_stage`, which says so: it is the record of why.
    reason: str = ""
    #: Defaults to the ticket's current workflow stage.
    stage_key: str | None = None
    #: Where the ticket waits; BACKLOG when absent. Not pinned (`auto_state`).
    state: TicketState | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        return value or None


@dataclass(frozen=True)
class Requeued:
    ticket: Ticket
    stage_key: str


def _actor(caller: Caller) -> str:
    """Who cleared the block, as the history rail prints it beside the reason.

    Not "triage" over MCP: operators' sessions and agents both call the tool.
    """
    return "human" if caller.transport is Transport.REST else "mcp"


def _requeue(session: Session, call: RequeueCall, caller: Caller) -> Requeued:
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=call.ticket_id)
    stage_key = (call.stage_key or ticket.workflow_stage_key or "").strip()
    requeue_stage(
        session,
        OrchestrationService(session),
        ticket,
        stage_key=stage_key,
        reason=call.reason.strip(),
        actor=_actor(caller),
        state=call.state or TicketState.BACKLOG,
    )
    return Requeued(ticket=ticket, stage_key=stage_key)


def requeue_outcome(session: Session, ticket: Ticket, stage_key: str) -> dict[str, Any]:
    """What a requeue actually arranged, said out loud.

    A requeue makes a stage ELIGIBLE to run; it does not cause it to run. It
    mints no orchestration and enqueues no dispatch. That is a defensible
    design - clearing a breaker is usually the prelude to a HUMAN decision, and
    auto-dispatching would spend an agent run on that decision's behalf - but it
    was invisible, and "requeued" reads as "it will run again". At least one
    recovery has been lost to the difference (lg-workflow-integrity-694).

    `scheduled` is DERIVED, never asserted: it is read back from the same tables
    a dispatcher would consult. A hardcoded "nothing is scheduled" would be a
    claim that could drift out of agreement with the code that makes it true.

    `blocked_stages` is read back the same way, and for the same reason: a
    requeue clears the block on the stage it was asked about, and a stage
    *elsewhere* in the workflow can still hold the ticket BLOCKED. Reporting the
    requeue without them reads as "the block is gone" when it is not.
    """
    svc = OrchestrationCallbackService(session)
    active = svc.get_active_orchestration_run(ticket.id)
    in_flight = find_active_stage_run(session, ticket.id)
    scheduled = active is not None or in_flight is not None

    instance, stages = svc.orch._resolve_stages(ticket)
    blocked_stages = (
        [
            key
            for key, status in parse_stage_map(instance, stages).items()
            if status is StageStatus.BLOCKED
        ]
        if instance and stages
        else []
    )

    outcome: dict[str, Any] = {
        "stage_key": stage_key,
        "cursor_stage_key": ticket.workflow_stage_key,
        "ticket_state": ticket.state.value,
        "scheduled": scheduled,
    }
    if blocked_stages:
        outcome["blocked_stages"] = blocked_stages
        listed = ", ".join(repr(key) for key in blocked_stages)
        subject = "stage" if len(blocked_stages) == 1 else "stages"
        outcome["note"] = (
            f"Stage '{stage_key}' is pending, but {subject} {listed} still blocked, so the "
            "ticket is not clear to run. Resolve those too."
        )
        return outcome
    if scheduled:
        outcome["note"] = (
            f"Stage '{stage_key}' is pending and something is already running against "
            "this ticket; it will be picked up."
        )
        return outcome
    outcome["note"] = (
        f"Stage '{stage_key}' is pending. Nothing is scheduled to run it - a requeue "
        "clears the block, it does not dispatch."
    )
    outcome["start_with"] = McpTool.START_ORCHESTRATION.value
    return outcome


def _agent_reply(session: Session, requeued: Requeued) -> dict[str, Any]:
    payload = ticket_state_payload(session, requeued.ticket.id)
    payload["requeued"] = requeue_outcome(session, requeued.ticket, requeued.stage_key)
    return payload


TICKET_REQUEUE = (
    Operation(
        id=OperationId.TICKET_REQUEUE,
        effect=Effect.WRITE,
        args=RequeueCall,
        handler=_requeue,
        # REST's caller is the inbox's human-action route, which words its own
        # answer from the ticket, so it takes the result as it is.
        replies={Transport.MCP: _agent_reply, Transport.CLI: _agent_reply},
    ),
)
