"""Done parents closed as their children's summary with their own work unlanded.

`parent_own_work` now holds such a parent open before the rollup or the
aggregator finalise closes it. These are the ones closed before that, and any a
writer the guard does not cover closes after it. Nothing repairs one
automatically: reopening it re-drives work that may have gone stale or reached
the base another way, so a person lands its branch or says where the work went.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, TicketState
from loregarden.services.invariants import Violation
from loregarden.services.parent_own_work import unlanded_own_work
from loregarden.services.recorded_paths import ticket_recorded_paths
from sqlmodel import Session, col, select


def parents_closed_without_their_work(session: Session) -> list[Violation]:
    """Done parents with no landing on record whose own branch holds work found nowhere.

    Git is the witness, so the database narrows first: a parent with no landing
    on record whose runs recorded a path. A ticket whose runs wrote code always
    records one; the planning stages that also do only cost a git check here.
    """
    parents = select(Ticket.parent_ticket_id).where(col(Ticket.parent_ticket_id).is_not(None))
    done = session.exec(
        select(Ticket).where(
            Ticket.state == TicketState.DONE,
            col(Ticket.id).in_(parents),
            (col(Ticket.landed_sha).is_(None)) | (Ticket.landed_sha == ""),
        )
    ).all()
    found: list[Violation] = []
    for ticket in done:
        if not ticket_recorded_paths(session, ticket):
            continue
        why = unlanded_own_work(session, ticket)
        if why:
            found.append(
                Violation(
                    subject_id=ticket.id,
                    ticket_id=ticket.id,
                    entered_at=ticket.resolved_at,
                    detail=f"{ticket.external_id} is done, but {why}",
                )
            )
    return found
