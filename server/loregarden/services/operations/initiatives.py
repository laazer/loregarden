"""An initiative's membership and plan, as operations: track a ticket or stop,
read the plan, switch the autopilot, start ready work, mark work for a person.

These were written twice, as the planner's MCP tools and as routes under
`/api/initiatives/{id}/`, and the two had drifted. The tools took an
initiative's external id and the routes did not; the tools let any ticket's
UUID stand in for an initiative when starting work or marking it, where the
routes refused anything but an initiative; and the initiatives route turned a
refused autopilot switch into a 500 where the ticket route said 409. Each
operation now resolves its tickets the one way and refuses the same things on
every transport.

The edit is one; the reply is the transport's. An agent has always been
answered with a short JSON summary, and the Initiatives page with its views.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from loregarden.models.domain import (
    AutopilotUpdate,
    InitiativePlanView,
    InitiativeView,
    SubtreeAutopilotView,
    Ticket,
)
from loregarden.services.initiative_autopilot import (
    mark_needs_person,
    run_autopilot,
    set_autopilot,
    start_ready_work,
)
from loregarden.services.initiative_membership import add_member, remove_member
from loregarden.services.initiative_plan_service import plan_view
from loregarden.services.initiative_service import get_initiative, load_initiative
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.subtree_autopilot import subtree_autopilot_view
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

#: The planner's tools have always refused a key they do not read.
_STRICT = ConfigDict(extra="forbid")

#: Who an edit made on the Initiatives page is attributed to.
_OPERATOR = "human"


class PlanNotFound(ValueError):
    """The initiative, plan root or membership a request names is not there."""


class MemberChange(BaseModel):
    """``ticket_id`` joins or leaves ``initiative_id``; each a UUID or an external id."""

    model_config = _STRICT

    initiative_id: str
    ticket_id: str


class InitiativeRef(BaseModel):
    model_config = _STRICT

    initiative_id: str


class AutopilotSwitch(AutopilotUpdate):
    """The autopilot of ``initiative_id``: a UUID or an external id."""

    model_config = _STRICT

    initiative_id: str


class TicketSelection(BaseModel):
    """Plan tickets of ``initiative_id``, by the UUIDs the plan's nodes carry."""

    model_config = _STRICT

    initiative_id: str
    ticket_ids: list[str]


class NeedsPersonMark(TicketSelection):
    needs_person: bool = True


@dataclass(frozen=True)
class AutopilotSwitched:
    root_id: str
    enabled: bool
    max_parallel: int
    queued_now: int


@dataclass(frozen=True)
class NeedsPersonMarked:
    initiative_id: str
    changed: list[str]
    needs_person: bool


@contextmanager
def _found() -> Iterator[None]:
    """The services say "not there" with `LookupError`; a route says 404."""
    try:
        yield
    except LookupError as exc:
        raise PlanNotFound(str(exc)) from exc


def _ticket(session: Session, ref: str) -> Ticket:
    return OrchestrationCallbackService(session).resolve_ticket(ticket_id=ref)


def _initiative(session: Session, ref: str) -> Ticket:
    ticket = _ticket(session, ref)
    with _found():
        return load_initiative(session, ticket.id)


def _by(caller: Caller, agent: str) -> str:
    return _OPERATOR if caller.transport is Transport.REST else agent


def _add_member(session: Session, args: MemberChange, caller: Caller) -> str:
    initiative = _initiative(session, args.initiative_id)
    ticket = _ticket(session, args.ticket_id)
    with _found():
        add_member(session, initiative.id, ticket.id, actor=_by(caller, "agent"))
    return initiative.id


def _remove_member(session: Session, args: MemberChange, _caller: Caller) -> str:
    initiative = _initiative(session, args.initiative_id)
    ticket = _ticket(session, args.ticket_id)
    with _found():
        remove_member(session, initiative.id, ticket.id)
    return initiative.id


def _plan(session: Session, args: InitiativeRef, _caller: Caller) -> InitiativePlanView:
    initiative = _initiative(session, args.initiative_id)
    return plan_view(session, initiative.id)


def _switch(
    session: Session, root: Ticket, update: AutopilotUpdate, caller: Caller
) -> AutopilotSwitched:
    with _found():
        plan = set_autopilot(session, root.id, update, actor=_by(caller, "planner"))
    # Turning it on queues the first batch now rather than at the next tick.
    queued = run_autopilot(session, root.id) if plan.autopilot else 0
    return AutopilotSwitched(
        root_id=root.id,
        enabled=plan.autopilot,
        max_parallel=plan.max_parallel,
        queued_now=queued,
    )


def _set_plan_autopilot(
    session: Session, args: AutopilotSwitch, caller: Caller
) -> AutopilotSwitched:
    """Any plan root: an initiative, a milestone or a parent ticket."""
    return _switch(session, _ticket(session, args.initiative_id), args, caller)


def _set_initiative_autopilot(
    session: Session, args: AutopilotSwitch, caller: Caller
) -> AutopilotSwitched:
    return _switch(session, _initiative(session, args.initiative_id), args, caller)


def _start_work(session: Session, args: TicketSelection, caller: Caller) -> dict[str, str]:
    initiative = _initiative(session, args.initiative_id)
    with _found():
        return start_ready_work(
            session, initiative.id, args.ticket_ids, actor=_by(caller, "planner")
        )


def _mark_needs_person(
    session: Session, args: NeedsPersonMark, _caller: Caller
) -> NeedsPersonMarked:
    initiative = _initiative(session, args.initiative_id)
    with _found():
        changed = mark_needs_person(
            session, initiative.id, args.ticket_ids, needs_person=args.needs_person
        )
    return NeedsPersonMarked(initiative.id, changed, args.needs_person)


def _members_reply(session: Session, initiative_id: str) -> dict[str, Any]:
    """The initiative's top-level tickets, as an agent has always read them."""
    view = get_initiative(session, initiative_id)
    return {
        "initiative": view.external_id,
        "items": [
            {
                "id": m.id,
                "external_id": m.external_id,
                "work_item_type": m.work_item_type.value,
                "workspace_slug": m.workspace_slug,
                "member": m.member,
                "home_milestone": m.home_milestone,
            }
            for m in view.milestones
        ],
    }


def _initiative_reply(session: Session, initiative_id: str) -> InitiativeView:
    return get_initiative(session, initiative_id)


def _no_body(_session: Session, _initiative_id: str) -> None:
    """The page's removal is a 204; the route answers it."""
    return None


def _plan_payload(_session: Session, view: InitiativePlanView) -> dict[str, Any]:
    return view.model_dump(mode="json")


def _switched_payload(_session: Session, switched: AutopilotSwitched) -> dict[str, Any]:
    return {
        "enabled": switched.enabled,
        "max_parallel": switched.max_parallel,
        "queued_now": switched.queued_now,
    }


def _switched_plan(session: Session, switched: AutopilotSwitched) -> InitiativePlanView:
    return plan_view(session, switched.root_id)


def _switched_subtree(session: Session, switched: AutopilotSwitched) -> SubtreeAutopilotView:
    return subtree_autopilot_view(session, switched.root_id)


def _marked_payload(_session: Session, marked: NeedsPersonMarked) -> dict[str, Any]:
    return {"changed": marked.changed, "needs_person": marked.needs_person}


def _marked_plan(session: Session, marked: NeedsPersonMarked) -> InitiativePlanView:
    return plan_view(session, marked.initiative_id)


def _agent_and_page(
    agent: Callable[[Session, Any], Any], page: Callable[[Session, Any], Any] | None
) -> Mapping[Transport, Callable[[Session, Any], Any]]:
    replies: dict[Transport, Callable[[Session, Any], Any]] = {
        Transport.MCP: agent,
        Transport.CLI: agent,
    }
    if page is not None:
        replies[Transport.REST] = page
    return replies


INITIATIVES = (
    Operation(
        id=OperationId.INITIATIVE_MEMBER_ADD,
        effect=Effect.WRITE,
        args=MemberChange,
        handler=_add_member,
        replies=_agent_and_page(_members_reply, _initiative_reply),
    ),
    Operation(
        id=OperationId.INITIATIVE_MEMBER_REMOVE,
        effect=Effect.WRITE,
        args=MemberChange,
        handler=_remove_member,
        replies=_agent_and_page(_members_reply, _no_body),
    ),
    Operation(
        id=OperationId.INITIATIVE_PLAN,
        effect=Effect.READ,
        args=InitiativeRef,
        handler=_plan,
        replies=_agent_and_page(_plan_payload, None),
    ),
    Operation(
        id=OperationId.PLAN_AUTOPILOT_SET,
        effect=Effect.WRITE,
        args=AutopilotSwitch,
        handler=_set_plan_autopilot,
        replies=_agent_and_page(_switched_payload, _switched_subtree),
    ),
    Operation(
        id=OperationId.INITIATIVE_AUTOPILOT_SET,
        effect=Effect.WRITE,
        args=AutopilotSwitch,
        handler=_set_initiative_autopilot,
        unavailable_on={
            Transport.MCP: (
                "an agent switches any plan root's autopilot, an initiative's included, "
                "through plan_autopilot_set"
            ),
            Transport.CLI: (
                "the CLI runs MCP's tools, which switch an initiative's autopilot "
                "through plan_autopilot_set"
            ),
        },
        replies={Transport.REST: _switched_plan},
    ),
    Operation(
        id=OperationId.INITIATIVE_WORK_START,
        effect=Effect.WRITE,
        args=TicketSelection,
        handler=_start_work,
    ),
    Operation(
        id=OperationId.INITIATIVE_NEEDS_PERSON,
        effect=Effect.WRITE,
        args=NeedsPersonMark,
        handler=_mark_needs_person,
        replies=_agent_and_page(_marked_payload, _marked_plan),
    ),
)
