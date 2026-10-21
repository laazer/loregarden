"""Tickets a pipeline agent files from inside a stage.

The review contract (`workflow_enforcement_v1.md`) tells a reviewer whose
finding meets no acceptance criterion to file it as a new ticket and pass. The
tool it names, `loregarden_create_ticket`, was denied to pipeline agents
(a9-create-ticket-mcp-tool), and the denial told them to "use the REST API".
On 2026-10-20 a review lane of lg-milestone-that-1314 did exactly that: it was
refused, POSTed to `/api/tickets` with curl, and filed lg-milestone-that-1315
with no approval and nothing recording which run filed it.

So the tool is the sanctioned path again, with the two things the curl lacked:

- **Provenance.** The filed ticket is related to the ticket the run was
  working on, and that relation names the run (`created_by="run:<id>"`).
- **A budget.** A run may file `MAX_TICKETS_PER_RUN`. The relation is the
  count, so the record and the limit cannot disagree. A reviewer that wants
  more is enumerating a defect family, which is one ticket, not many.
"""

from __future__ import annotations

from loregarden.models.domain import AgentRun, Ticket, TicketRelation
from loregarden.services.ticket_relations import TicketRelationService
from sqlalchemy import func
from sqlmodel import Session, select

#: Tickets one agent run may file. Three covers a review that found a defect,
#: its sibling, and the follow-up both point at.
MAX_TICKETS_PER_RUN = 3


class FilingRefused(ValueError):
    """A pipeline agent may not file this ticket. A `ValueError` for the MCP error path."""


def filed_by(run: AgentRun) -> str:
    """The `created_by` stamp on the relation that records a run's filing."""
    return f"run:{run.id}"


def filing_run(session: Session, run_id: str) -> AgentRun:
    """The supervised run filing a ticket, refused when the call names none.

    A pipeline agent's MCP config carries its run; a call marked orchestrated
    without one comes from a config this control plane did not build.
    """
    run = session.get(AgentRun, run_id) if run_id else None
    if run is None:
        raise FilingRefused(
            "loregarden_create_ticket from a pipeline agent must come from a supervised run, "
            "and this call names none. Mention the finding in your stage report instead."
        )
    return run


def check_filing_budget(session: Session, run: AgentRun) -> None:
    """Refuse a run's filing past `MAX_TICKETS_PER_RUN`."""
    filed = session.exec(
        select(func.count())
        .select_from(TicketRelation)
        .where(TicketRelation.created_by == filed_by(run))
    ).one()
    if filed >= MAX_TICKETS_PER_RUN:
        raise FilingRefused(
            f"This run has already filed {filed} tickets, the most one run may file. If the "
            "findings are instances of one defect, add them to a ticket you already filed "
            "with loregarden_update_ticket; otherwise name them in your stage report."
        )


def record_filing(session: Session, run: AgentRun, filed: Ticket) -> None:
    """Relate the filed ticket to the one the run was working on, naming the run."""
    TicketRelationService(session).add_relation(run.ticket_id, filed.id, created_by=filed_by(run))
