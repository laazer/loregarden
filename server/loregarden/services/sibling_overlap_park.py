"""Ask a person before a ticket starts writing files an in-flight sibling claims.

The prompt warning (`agents.sibling_claims_context`) tells an agent about a
sibling it overlaps, but cannot stop two siblings from both editing a file and
conflicting when the second lands. Ordering them can: if the later ticket waits
for the earlier one to land, it starts from a branch that already holds the
sibling's change. Whether that is worth the wait is a person's call, so the
stage parks with the question instead of being ordered silently.

- **Run anyway** (approve) arms the park's one-shot waiver, as every park does.
- **Wait** (reject) links the ticket to wait for each sibling, and the
  dependency hold (`services.dependency_hold`) keeps it until they land.

The question is asked once per ticket and sibling, at the moment it can still
help: when the ticket claims files (its plan named them) but no run of its own
has changed any yet. A ticket already writing gets the prompt warning only.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    TicketState,
)
from loregarden.models.domain.path_claim_enums import PathClaimSource, StageParkReason
from loregarden.services.path_claims import SiblingOverlap, sibling_overlaps, ticket_claims
from loregarden.services.sibling_overlap_resolution import SiblingOverlapPark
from loregarden.services.stage_parking import park_stage
from loregarden.services.ticket_dependencies import wait_path
from sqlmodel import Session, col, select

#: Paths named per sibling in the inbox card; the rest are counted.
MAX_PATHS_SHOWN = 5


def overlaps_to_park_on(session: Session, ticket: Ticket) -> list[SiblingOverlap]:
    """The in-flight siblings to ask about before ``ticket``'s next stage, or [].

    Empty unless ``ticket`` is about to start writing (it claims files and has
    changed none), and never a sibling that is still in the backlog (it will be
    asked when it starts, since this ticket is then in flight), already ordered
    with this one either way, or already answered "run anyway".
    """
    claims = ticket_claims(session, ticket)
    if not claims or PathClaimSource.OBSERVED in claims.values():
        return []
    answered = _run_anyway_siblings(session, ticket)
    return [
        overlap
        for overlap in sibling_overlaps(session, ticket)
        if overlap.sibling.state is not TicketState.BACKLOG
        and overlap.sibling.id not in answered
        and not already_ordered(session, ticket.id, overlap.sibling.id)
    ]


def park_for_sibling_overlap(
    session: Session, *, run: AgentRun, ticket: Ticket, overlaps: list[SiblingOverlap]
) -> Approval:
    """Park ``run``'s stage on the question of whether to wait for ``overlaps``."""
    names = ", ".join(o.sibling.external_id for o in overlaps)
    lines = [
        f"{ticket.external_id} is about to start writing files that open tickets in the "
        "same landing tree also claim. Whichever lands second will conflict.",
        "",
        *(_describe(o) for o in overlaps),
        "",
        f"Run anyway: start now; the agent is warned to keep its diff small in these files. "
        f"Wait: {ticket.external_id} waits for {names} to land, then starts from their work.",
    ]
    payload = SiblingOverlapPark(
        sibling_ids=[o.sibling.id for o in overlaps],
        sibling_external_ids=[o.sibling.external_id for o in overlaps],
    )
    return park_stage(
        session,
        run=run,
        ticket=ticket,
        title=f"{ticket.external_id} overlaps {names} on the same files",
        impact="\n".join(lines),
        reason=StageParkReason.SIBLING_OVERLAP,
        payload_json=payload.model_dump_json(),
    )


def _describe(overlap: SiblingOverlap) -> str:
    paths = [claim.path for claim in overlap.shared]
    shown = ", ".join(paths[:MAX_PATHS_SHOWN])
    extra = len(paths) - MAX_PATHS_SHOWN
    where = overlap.sibling.workflow_stage_key or overlap.sibling.state.value
    more = f" and {extra} more" if extra > 0 else ""
    return f"- {overlap.sibling.external_id} ({where}): {shown}{more}"


def _run_anyway_siblings(session: Session, ticket: Ticket) -> set[str]:
    rows = session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.STAGE_PARK,
            Approval.tool_name == StageParkReason.SIBLING_OVERLAP.value,
            col(Approval.status) == ApprovalStatus.APPROVED,
        )
    ).all()
    return {
        sibling_id
        for row in rows
        for sibling_id in SiblingOverlapPark.model_validate_json(row.tool_input_json).sibling_ids
    }


def already_ordered(session: Session, a: str, b: str) -> bool:
    """Whether either ticket already waits for the other, by edges or children."""
    return bool(wait_path(session, a, {b}) or wait_path(session, b, {a}))
