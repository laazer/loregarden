"""The agent a ticket's stage would dispatch, derived rather than remembered.

`ticket.next_agent` is a pin: written once, honoured where the stage offers it,
cleared at dispatch (lg-workflow-integrity-441). Nine readers treated it as a
standing fact, which is wrong in a way that does not announce itself — for most
of a ticket's life the field is empty, and a reader that prices, labels or
routes off an empty string produces a plausible answer computed from nothing
rather than an error. The queue cost estimate was the sharp case: it returned a
number for an agent of `""`.

This module answers the question those readers were actually asking. It is the
one seam between them and `studio_routing.resolve_display_agent`, so the
resolution rule lives in one place rather than being rebuilt nine times.

Read-only by construction. Dispatch resolves through
`orchestration._resolve_run_agent`, which must keep raising on a stage that
resolves no agent — a reader may show nothing, but a dispatcher may not guess.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, WorkflowStageDef
from loregarden.services.studio_routing import resolve_display_agent
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session


def ticket_stage_definition(
    session: Session, ticket: Ticket, stage_key: str = ""
) -> WorkflowStageDef | None:
    """`ticket`'s definition of `stage_key`, defaulting to its current stage.

    `stage_key` is explicit for the callers that must not use the cursor: a
    queue entry is priced against the stage IT will run, which is not
    necessarily the stage the ticket is parked on now.
    """
    key = stage_key or ticket.workflow_stage_key
    if not key:
        return None
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return None
    return next((stage for stage in stages if stage.key == key), None)


def ticket_stage_agent(session: Session, ticket: Ticket, stage_key: str = "") -> str:
    """The agent `ticket` would dispatch for `stage_key`, or "" if none resolves.

    An empty answer means "nothing to show" and callers must render it as a gap,
    not as an agent named "". A ticket with no workflow, no stage map, or a
    stage key its template does not define all answer "" — those are the cases
    the stored field used to paper over.
    """
    stage = ticket_stage_definition(session, ticket, stage_key)
    if stage is None:
        return ""
    return resolve_display_agent(ticket, stage)
