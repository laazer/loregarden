"""Name the ticket each monitor finding is about.

A finding carries only a ticket id, and a page of ids is a page nobody acts
on: the reader cannot tell which ticket, which workspace, or — the question
that decides whether to act at all — whether the ticket is still live. A
`stage_thrash` on a ticket that finished three weeks ago is history; the same
finding on a ticket in progress is a stage burning attempts right now.

One query for every ticket the findings mention, rather than a lookup per
finding: the all-tickets view carries 100+ findings.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, Workspace
from loregarden.models.domain.workflow_monitor import MonitorFindingView
from sqlmodel import Session, col, select


def attach_ticket_context(
    session: Session, findings: list[MonitorFindingView]
) -> list[MonitorFindingView]:
    """Return `findings` with ticket title, external id, state and workspace filled in.

    A finding whose ticket no longer exists keeps its blank context — the id is
    still shown, and a missing title is itself the signal that the row is stale.
    """
    ticket_ids = {finding.ticket_id for finding in findings if finding.ticket_id}
    if not ticket_ids:
        return findings

    rows = session.exec(
        select(Ticket, Workspace.slug)
        .join(Workspace, col(Workspace.id) == col(Ticket.workspace_id), isouter=True)
        .where(col(Ticket.id).in_(ticket_ids))
    ).all()
    by_id = {ticket.id: (ticket, slug or "") for ticket, slug in rows}

    enriched: list[MonitorFindingView] = []
    for finding in findings:
        match = by_id.get(finding.ticket_id)
        if match is None:
            enriched.append(finding)
            continue
        ticket, slug = match
        enriched.append(
            finding.model_copy(
                update={
                    "ticket_title": ticket.title,
                    "ticket_external_id": ticket.external_id,
                    "ticket_state": ticket.state,
                    "workspace_slug": slug,
                }
            )
        )
    return enriched
