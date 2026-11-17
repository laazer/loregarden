"""Filing a ticket and editing one, as operations.

These were written twice, as MCP tools (`loregarden_create_ticket`,
`loregarden_update_ticket`, `loregarden_set_ticket_workflow`) and as
`POST /api/tickets` and `PATCH /api/tickets/{id}`, and the two had drifted:

- the routes found a ticket (and a parent) by UUID alone, where the tools took
  an external id too;
- a PATCH carrying ``stage_status`` with no ``stage_key`` answered 200 and set
  nothing, where the tool refused it.

Each operation now holds the one behaviour. What stays a transport's own is
what its callers were promised: an agent's filing on a supervised run is
budgeted, placed and recorded (`caller.orchestrated`); an agent's edit that
changes nothing is refused, where the page's PATCH of nothing has always
answered with the ticket; and each transport is answered in its own shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkItemType,
)
from loregarden.services.acceptance_criteria import CRITERIA_MODES, load_criteria, merge_criteria
from loregarden.services.agent_ticket_filing import (
    check_filing_budget,
    filing_run,
    place_finding,
    record_filing,
)
from loregarden.services.discovered_work import record_discovery
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_dependencies import resolve_prerequisites
from loregarden.services.ticket_detail_view import ticket_detail
from loregarden.services.ticket_revision_guard import guard_ticket_revision
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_state_payload import ticket_state_payload
from loregarden.services.ticket_tags import normalize_tags
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore")


# --- filing a ticket -------------------------------------------------------------------


class TicketCreateArgs(BaseModel):
    """A new ticket. MCP names its parent ``parent``; REST's ``parent_ticket_id``
    arrives here too. Either is a UUID or an external id."""

    model_config = _LENIENT

    workspace_slug: str = ""
    title: str = ""
    work_item_type: str = WorkItemType.TASK.value
    parent: str = ""
    description: str = ""
    acceptance_criteria: list[str] = []
    priority: int = 3
    #: The page's field; MCP's schema has never offered it.
    milestone: str = ""
    external_id: str = ""
    #: Prerequisites it waits for, as UUIDs or external ids.
    depends_on: list[str] = []


@dataclass(frozen=True)
class FiledTicket:
    ticket: Ticket
    #: Where a supervised run's filing was moved to, when it was ("" when not).
    placed: str = ""


def _work_item_type(raw: str) -> WorkItemType:
    try:
        return WorkItemType(raw or WorkItemType.TASK.value)
    except ValueError as exc:
        raise ValueError(f"Unknown work_item_type: {raw}") from exc


def _parent_id(session: Session, parent: str) -> str | None:
    """The parent's UUID. Not workspace-scoped here: `TicketService.create_ticket`
    already refuses a parent from another workspace, and scoping twice would only
    risk the two checks disagreeing."""
    ref = parent.strip()
    if not ref:
        return None
    try:
        return OrchestrationCallbackService(session).resolve_ticket(ticket_id=ref).id
    except ValueError as exc:
        raise ValueError(f"Parent ticket not found: {ref}") from exc


def _prerequisites(session: Session, refs: list[str]) -> list[Ticket]:
    try:
        return resolve_prerequisites(session, refs)
    except ValueError as exc:
        # The type is kept (a missing one is still a 404 on REST); the message
        # says which list the reference came from.
        exc.args = (f"Prerequisite: {exc}",)
        raise


def _create(session: Session, args: TicketCreateArgs, caller: Caller) -> FiledTicket:
    work_item_type = _work_item_type(args.work_item_type)
    parent_ticket_id = _parent_id(session, args.parent)
    prerequisites = _prerequisites(session, args.depends_on)

    # A supervised run's filing is a finding: budgeted, placed beside its source,
    # and recorded with the ticket, so neither exists without the other.
    run = filing_run(session, caller.run_id) if caller.orchestrated else None
    placed = ""
    on_insert = None
    if run is not None:
        check_filing_budget(session, run)
        on_insert = partial(record_discovery, session, run)
        placement = place_finding(session, run, parent_ticket_id, work_item_type)
        parent_ticket_id, work_item_type, placed = (
            placement.parent_ticket_id,
            placement.work_item_type,
            placement.moved,
        )

    ticket = TicketService(session).create_ticket(
        workspace_slug=args.workspace_slug,
        title=args.title,
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
        description=args.description,
        acceptance_criteria=args.acceptance_criteria,
        priority=args.priority,
        milestone=args.milestone,
        external_id=args.external_id,
        depends_on=prerequisites,
        created_by="agent" if run is not None else "user",
        on_insert=on_insert,
    )
    if run is not None:
        record_filing(session, run, ticket)
    return FiledTicket(ticket=ticket, placed=placed)


def _filed_reply(_session: Session, filed: FiledTicket) -> dict[str, Any]:
    ticket = filed.ticket
    return {
        "id": ticket.id,
        "external_id": ticket.external_id,
        # Echoed back so a caller that supplied an id of its own can see where it
        # landed, rather than reading `external_id` and finding a different string.
        "legacy_external_id": ticket.legacy_external_id,
        "title": ticket.title,
        **({"placed": filed.placed} if filed.placed else {}),
    }


def _filed_detail(session: Session, filed: FiledTicket) -> Any:
    return ticket_detail(session, filed.ticket)


# --- editing a ticket ------------------------------------------------------------------


class TicketUpdateArgs(BaseModel):
    """An edit to one ticket. ``None`` is "not supplied" throughout.

    The union of both transports' fields: MCP's schema offers the content
    fields, ``mode`` and ``parent``; the page's PATCH model offers the content
    fields and the panel's (branch, stages, checks, overrides). Each transport's
    own model keeps the other's fields out.
    """

    model_config = _LENIENT

    ticket_id: str
    state: str | None = None
    auto_state: bool | None = None
    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    #: How ``acceptance_criteria`` combines with the stored list.
    mode: str | None = None
    out_of_scope: list[str] | None = None
    tags: list[str] | None = None
    priority: int | None = None
    #: UUID or external id; "" detaches (legal only for a milestone).
    parent: str | None = None
    #: The revision the caller's draft was seeded from; refused if the row moved.
    expected_revision: int | None = None
    # The page's panel.
    checked_acceptance_criteria: list[str] | None = None
    branch: str | None = None
    workflow_stage_key: str | None = None
    workflow_stage_status: StageStatus | None = None
    workflow_template_slug: str | None = None
    stage_key: str | None = None
    stage_status: StageStatus | None = None
    stage_updates: dict[str, StageStatus] | None = None
    compatibility_posture: str | None = None
    git_automation: dict[str, bool | int | str] | None = None


#: Fields that reach `UpdateTicketRequest` as they arrive.
_AS_SENT = frozenset(
    {
        "auto_state",
        "title",
        "description",
        "out_of_scope",
        "checked_acceptance_criteria",
        "branch",
        "workflow_stage_key",
        "workflow_stage_status",
        "workflow_template_slug",
        "stage_key",
        "stage_status",
        "stage_updates",
        "compatibility_posture",
        "git_automation",
    }
)


def refuse_unpaired_stage_status(stage_key: str | None, stage_status: object) -> None:
    """A status names no stage on its own; setting it on whichever stage is
    current would assert an outcome nobody chose."""
    if stage_status and not (stage_key or "").strip():
        raise ValueError("stage_status was given without stage_key.")


def _criteria_fields(ticket: Ticket, args: TicketUpdateArgs) -> dict[str, Any]:
    """The acceptance-criteria half of an edit: the list and its ``mode``.

    `mode` is meaningless without the criteria it combines, so naming it alone
    is refused rather than ignored.
    """
    mode = args.mode or "replace"
    if mode not in CRITERIA_MODES:
        raise ValueError(f"mode must be one of {', '.join(CRITERIA_MODES)} (got {mode!r})")
    if args.acceptance_criteria is None:
        if args.mode is not None:
            raise ValueError("mode was given without acceptance_criteria")
        return {}
    return {
        "acceptance_criteria": merge_criteria(
            load_criteria(ticket.acceptance_criteria_json), args.acceptance_criteria, mode
        )
    }


def collect_update_fields(ticket: Ticket, args: TicketUpdateArgs) -> dict[str, Any]:
    """The `UpdateTicketRequest` fields an edit names, validated.

    Excludes ``parent``, which needs a resolver, and ``expected_revision``,
    which is an edge concern and must never make an empty edit look like one.
    """
    refuse_unpaired_stage_status(args.stage_key, args.stage_status)
    fields = args.model_dump(include=_AS_SENT, exclude_none=True)
    if args.stage_key and args.stage_status is None and args.workflow_stage_key is None:
        # A stage with no status moves the cursor to it, as `set_ticket_workflow`
        # does; `update_ticket_manual` would otherwise drop the key and answer 200.
        del fields["stage_key"]
        fields["workflow_stage_key"] = args.stage_key.strip()
    if args.state is not None:
        fields["state"] = TicketState(args.state)
    fields.update(_criteria_fields(ticket, args))
    if args.tags is not None:
        fields["tags"] = normalize_tags(args.tags)
    if args.priority is not None:
        if args.priority < 1 or args.priority > 3:
            raise ValueError(f"Invalid priority {args.priority}: must be in [1, 3]")
        fields["priority"] = args.priority
    return fields


def _update(session: Session, args: TicketUpdateArgs, caller: Caller) -> Ticket:
    resolver = OrchestrationCallbackService(session)
    ticket = resolver.resolve_ticket(ticket_id=args.ticket_id)
    fields = collect_update_fields(ticket, args)
    if args.parent is not None:
        # "" is a deliberate detach, so it must reach the applier, not be
        # dropped as falsy.
        parent = args.parent.strip()
        fields["parent_ticket_id"] = resolver.resolve_ticket(ticket_id=parent).id if parent else ""
    if not fields and caller.transport is not Transport.REST:
        # An agent told "done" for a call that changed nothing is the failure
        # this refusal exists for. The page's empty PATCH answers with the
        # ticket, as it always has.
        raise ValueError(
            "Nothing to update — supply at least one of: state, auto_state, title, "
            "description, priority, acceptance_criteria, out_of_scope, tags, parent."
        )
    # After the fields guard, so a refused call does not claim the row. Not in
    # `update_ticket_manual`: its system writers must never fail on contention.
    if args.expected_revision is not None:
        guard_ticket_revision(
            session, ticket_id=ticket.id, expected_revision=args.expected_revision
        )
    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(**fields))
    return ticket


class TicketWorkflowArgs(BaseModel):
    """Put a ticket on a workflow, or move it to a stage (with a status, or not)."""

    model_config = _LENIENT

    ticket_id: str
    #: "" takes the ticket off its workflow.
    workflow_template_slug: str | None = None
    stage_key: str | None = None
    stage_status: str | None = None


def _set_workflow(session: Session, args: TicketWorkflowArgs, _caller: Caller) -> Ticket:
    """Both halves go through `update_ticket_manual`, so a stage reset gets the
    retry-budget refresh and state reconciliation the panel's own edit gets."""
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=args.ticket_id)
    fields: dict[str, Any] = {}
    if args.workflow_template_slug is not None:
        fields["workflow_template_slug"] = args.workflow_template_slug

    stage_key = (args.stage_key or "").strip()
    stage_status = (args.stage_status or "").strip()
    refuse_unpaired_stage_status(stage_key, stage_status)
    if stage_key and stage_status:
        fields["stage_key"] = stage_key
        fields["stage_status"] = StageStatus(stage_status)
    elif stage_key:
        # Alone, a stage key moves the cursor and keeps the stage's status.
        fields["workflow_stage_key"] = stage_key

    if not fields:
        raise ValueError("Nothing to change — supply workflow_template_slug, stage_key, or both.")
    OrchestrationService(session).update_ticket_manual(ticket, UpdateTicketRequest(**fields))
    return ticket


def _state_reply(session: Session, ticket: Ticket) -> dict[str, Any]:
    return ticket_state_payload(session, ticket.id)


def _detail_reply(session: Session, ticket: Ticket) -> Any:
    session.refresh(ticket)
    return ticket_detail(session, ticket)


#: An agent's reply is the state payload every MCP write returns; the page's is
#: the detail it renders.
_EDIT_REPLIES: Mapping[Transport, Callable[[Session, Any], Any]] = {
    Transport.MCP: _state_reply,
    Transport.CLI: _state_reply,
    Transport.REST: _detail_reply,
}

TICKET_WRITES = (
    Operation(
        id=OperationId.TICKET_CREATE,
        effect=Effect.WRITE,
        args=TicketCreateArgs,
        handler=_create,
        replies={
            Transport.MCP: _filed_reply,
            Transport.CLI: _filed_reply,
            Transport.REST: _filed_detail,
        },
    ),
    Operation(
        id=OperationId.TICKET_UPDATE,
        effect=Effect.WRITE,
        args=TicketUpdateArgs,
        handler=_update,
        replies=_EDIT_REPLIES,
    ),
    Operation(
        id=OperationId.TICKET_WORKFLOW_SET,
        effect=Effect.WRITE,
        args=TicketWorkflowArgs,
        handler=_set_workflow,
        replies={Transport.MCP: _state_reply, Transport.CLI: _state_reply},
        unavailable_on={
            Transport.REST: "no route of its own: the page sets the same fields through "
            "PATCH /api/tickets/{id} (ticket_update), with the same stage pairing check",
        },
    ),
)
