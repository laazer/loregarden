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
- **A budget.** A run may file `MAX_TICKETS_PER_RUN`. A reviewer that wants
  more is enumerating a defect family, which is one ticket, not many.

lg-milestone-that-1216 added the rest. A filing is also recorded as a directed,
undecided finding (`services.discovered_work`): its parent neither runs it nor
waits for it until its source ticket's spec decides its scope (`scope_round`). The budget counts those rows, not the
relation: a relation is stored once per *pair*, so a filing against a ticket
the run's own ticket was already related to wrote no row and went uncounted.
One more cap stops a fan-out a per-run limit cannot: one per drive.

Per-run caps did not bound a ticket. Replayed over all 153 findings on
2026-11-18: 89 were filed by a second or later run on the same ticket, and 78
were filed against a ticket that was itself a finding, in chains up to six
deep (lg-verdict-outcome-1646 → … → lg-gate-studio-1673), so an initiative
whose work was finishing kept growing. Three caps bound it, and the replay of
all three together kept 45 of the 153:

- **A lifetime budget.** A ticket's runs may file `MAX_TICKETS_PER_SOURCE`
  in all, decided or not. It replaces the undecided cap (5), which a lifetime
  budget of 3 makes unreachable.
- **The first round files.** A run whose agent already finished this stage on
  this ticket is a re-review; what it finds goes in its stage report, as a
  recheck round's new findings already do.
- **A finding of a finding is the end of the line.** A ticket two filings
  from planned work may not file a third.

A refused filing is not lost: the refusal tells the agent to record it in the
stage report's `findings`, which keeps every non-blocking finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import (
    VALID_HIERARCHY,
    AgentRun,
    RunStatus,
    Ticket,
    TicketDiscovery,
    WorkItemType,
)
from loregarden.services.ticket_relations import TicketRelationService
from sqlalchemy import func
from sqlmodel import Session, col, select

#: Tickets one agent run may file. Three covers a review that found a defect,
#: its sibling, and the follow-up both point at.
MAX_TICKETS_PER_RUN = 3
#: Tickets one drive (one orchestration run's stages together) may file. The
#: most any drive had filed was 8 (live database, 2026-11-07), all of them
#: review findings on one milestone child.
MAX_TICKETS_PER_DRIVE = 6
#: Tickets one ticket's runs may file over its life. 35 of the 53 tickets that
#: had filed any were within it (live database, 2026-11-18).
MAX_TICKETS_PER_SOURCE = 3
#: How many filings from planned work a ticket may be and still file: planned
#: work files findings (1), and a finding may file (2); a finding of a finding may not.
MAX_FINDING_DEPTH = 1

_FAMILY = (
    "If the findings are instances of one defect, add them to a ticket already filed with "
    "loregarden_update_ticket; otherwise name them in your stage report."
)
_REPORT_IT = (
    'Record it in your stage report\'s `findings` instead, with `"blocking": false` and a '
    "disposition: it is kept there, and it does not become a ticket."
)


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


def _filed(session: Session, *where) -> int:
    return session.exec(select(func.count()).select_from(TicketDiscovery).where(*where)).one()


def check_filing_budget(session: Session, run: AgentRun) -> None:
    """Refuse a run's filing past any of the caps, naming which one."""
    by_run = _filed(session, TicketDiscovery.agent_run_id == run.id)
    if by_run >= MAX_TICKETS_PER_RUN:
        raise FilingRefused(
            f"This run has already filed {by_run} tickets, the most one run may file. {_FAMILY}"
        )
    if run.orchestration_run_id:
        by_drive = _filed(session, TicketDiscovery.orchestration_run_id == run.orchestration_run_id)
        if by_drive >= MAX_TICKETS_PER_DRIVE:
            raise FilingRefused(
                f"This drive has already filed {by_drive} tickets, the most one drive may "
                f"file. {_FAMILY}"
            )
    if run.ticket_id:
        _check_source(session, run, run.ticket_id)


def _check_source(session: Session, run: AgentRun, ticket_id: str) -> None:
    """The caps on the ticket the run works on: its lifetime budget, its round, its depth."""
    by_ticket = _filed(session, TicketDiscovery.source_ticket_id == ticket_id)
    if by_ticket >= MAX_TICKETS_PER_SOURCE:
        raise FilingRefused(
            f"This ticket's runs have already filed {by_ticket} tickets, the most one ticket "
            f"may file over its life. {_REPORT_IT}"
        )
    if _is_a_rerun(session, run, ticket_id):
        raise FilingRefused(
            f"{run.agent_id} already finished '{run.stage_key}' on this ticket, so this run is "
            f"a re-review, and a re-review files no tickets. {_REPORT_IT}"
        )
    depth = _finding_depth(session, ticket_id)
    if depth > MAX_FINDING_DEPTH:
        raise FilingRefused(
            f"This ticket is a finding of a finding ({depth} filings from planned work), and "
            f"such a ticket files no tickets of its own. {_REPORT_IT}"
        )


def _is_a_rerun(session: Session, run: AgentRun, ticket_id: str) -> bool:
    """Whether the same agent already finished the same stage on this ticket before ``run``.

    Finished means succeeded: a run that crashed reported nothing, so the retry
    is still the first look.
    """
    earlier = session.exec(
        select(AgentRun.id)
        .where(
            AgentRun.ticket_id == ticket_id,
            AgentRun.stage_key == run.stage_key,
            AgentRun.agent_id == run.agent_id,
            AgentRun.status == RunStatus.SUCCEEDED,
            AgentRun.id != run.id,
            col(AgentRun.created_at) < run.created_at,
        )
        .limit(1)
    ).first()
    return earlier is not None


def _finding_depth(session: Session, ticket_id: str) -> int:
    """How many filings separate ``ticket_id`` from planned work: 0 when it was not filed.

    Stops one past the cap: the answer only has to say whether it is over.
    """
    depth = 0
    current: str | None = ticket_id
    while current is not None and depth <= MAX_FINDING_DEPTH:
        current = session.exec(
            select(TicketDiscovery.source_ticket_id).where(
                TicketDiscovery.filed_ticket_id == current
            )
        ).first()
        if current is not None:
            depth += 1
    return depth


def record_filing(session: Session, run: AgentRun, filed: Ticket) -> None:
    """Relate a filed ticket to the ticket the run was working on.

    Its discovery row is written with the ticket itself (`record_discovery`
    as `TicketService.create_ticket`'s ``on_insert``), so a failure here
    leaves a finding without its relation, never a ticket without its finding.
    """
    TicketRelationService(session).add_relation(run.ticket_id, filed.id, created_by=filed_by(run))
    # `add_relation` returns an existing pair without committing.
    session.commit()


@dataclass(frozen=True)
class FindingPlacement:
    """Where a run's filing goes, and in words how that differs from what it asked."""

    parent_ticket_id: str | None
    work_item_type: WorkItemType
    moved: str = ""


def place_finding(
    session: Session, run: AgentRun, parent_ticket_id: str | None, work_item_type: WorkItemType
) -> FindingPlacement:
    """A filing under the run's own ticket goes beside it, under that ticket's parent.

    Filed under its source, a finding made the source a parent, and a parent
    never runs its own stages: lg-milestone-that-1420's verify, review and
    gate could never run once 1431 was filed under it (lg-milestone-that-2604).
    Nor could a `follow_up` decision ever move it, since a bug holds only
    tasks and no parent of a bug holds a task. Beside its source, the
    discovery row still names the source, the source's parent holds it out
    until the scope is decided, and `follow_up` is true where it already is.

    The type asked for is kept where that parent can hold it, else it is filed
    as a bug where the parent holds bugs; otherwise it is refused, naming what
    the parent holds.
    """
    source = session.get(Ticket, run.ticket_id) if run.ticket_id else None
    if source is None or parent_ticket_id != source.id or source.parent_ticket_id is None:
        return FindingPlacement(parent_ticket_id, work_item_type)
    above = session.get(Ticket, source.parent_ticket_id)
    if above is None:
        return FindingPlacement(parent_ticket_id, work_item_type)
    allowed = VALID_HIERARCHY.get(above.work_item_type, [])
    if work_item_type in allowed:
        kind = work_item_type
    elif WorkItemType.BUG in allowed:
        kind = WorkItemType.BUG
    else:
        raise FilingRefused(
            f"A finding of {source.external_id} is filed beside it, under "
            f"{above.external_id}, which holds only "
            f"{', '.join(t.value for t in allowed) or 'nothing'}. File it as one of those."
        )
    retyped = f", as a {kind.value}" if kind is not work_item_type else ""
    return FindingPlacement(
        above.id,
        kind,
        f"filed under {above.external_id}, beside its source {source.external_id}{retyped}, "
        "so the source keeps running its own stages",
    )
