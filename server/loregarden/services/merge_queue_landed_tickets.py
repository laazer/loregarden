"""A pull request the queue lands closes the tickets it names.

A tree entry carries its ticket, and the tree's own landing settles it. A pull
request admitted by number (`enqueue_pull_request`) carries none: its ticket
is named only in its description, on a ``Ticket: lg-…`` line. Nothing read
that line, so every PR landed by number left its ticket open after the work
was on the base, to be found and closed by hand.

When such an entry merges, each ticket its body names is closed as ``done``,
the way an operator closing it would (`ticket_state_service.choose`, pinned
with `state_locked`; its open stages close with it in `closed_workflow_guard`),
and the change is put on the ticket's history naming the PR and merge commit.

Only the ids a ``Ticket:`` line opens with count. PR descriptions here name
other tickets in the prose after them, and name a milestone as often as the
work item: of the 36 ids those lines named on the last 200 merged PRs, 18
were tickets holding child work. So a named ticket that holds child work is left
to its rollup (`reconcile_ancestors`, which closing a child already runs), and
the cycle's report says so.

Two cases leave a named ticket open, each reported as an error rather than
skipped: the id resolves to no ticket, or to more than one; or an
orchestration is still driving the ticket, whose drive would otherwise run on
under a ticket that reads ``done``. A ticket already closed is left as it is.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    EventType,
    MergeQueueEntry,
    OrchestrationRun,
    Ticket,
    TicketState,
)
from loregarden.services.discovered_work import has_owned_children
from loregarden.services.merge_queue_reports import RESOLVED_BY_QUEUE
from loregarden.services.run_concurrency import ACTIVE_ORCHESTRATION_STATUSES
from loregarden.services.ticket_ids import AmbiguousTicketReference, TicketNotFound, resolve_one
from loregarden.services.ticket_rollup import reconcile_ancestors
from loregarden.services.ticket_state_service import RESOLVED_STATES, choose
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: A ``Ticket:`` (or ``Tickets:``) line, at the start of a line of the body.
_TICKET_LINE = re.compile(r"^[ \t>*-]*tickets?:[ \t]*(?P<ids>.+)$", re.IGNORECASE | re.MULTILINE)

#: One external id: lowercase words joined by hyphens, ending in a number.
_ID = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d+"
_EXTERNAL_ID = re.compile(_ID)

#: The list of ids a line opens with, optionally in backticks, joined by commas
#: or "and". Only that list counts: what follows is prose, and prose names other
#: tickets for context ("overlaps lg-…-596", "under lg-…-87").
_LEADING_IDS = re.compile(rf"`?{_ID}`?(?:\s*(?:,|&|\band\b)\s*`?{_ID}`?)*")


@dataclass
class LandedTickets:
    """What landing one pull request did to the tickets it names."""

    closed: list[str] = field(default_factory=list)
    already_closed: list[str] = field(default_factory=list)
    #: Named tickets that hold child work: their rollup closes them, not a PR.
    left_to_rollup: list[str] = field(default_factory=list)
    #: One line per named ticket left open, saying why.
    problems: list[str] = field(default_factory=list)


def named_tickets(body: str) -> list[str]:
    """The external ids a pull request body names on its ``Ticket:`` lines, in order."""
    ids: list[str] = []
    for line in _TICKET_LINE.finditer(body):
        leading = _LEADING_IDS.match(line.group("ids"))
        if leading is None:
            continue
        for found in _EXTERNAL_ID.findall(leading.group()):
            if found not in ids:
                ids.append(found)
    return ids


def close_landed_tickets(
    session: Session, entry: MergeQueueEntry, *, body: str, merge_commit: str
) -> LandedTickets:
    """Close every ticket `body` names, now that `entry` merged as `merge_commit`. Commits."""
    result = LandedTickets()
    for ref in named_tickets(body):
        try:
            ticket = resolve_one(session, ref, workspace_id=entry.workspace_id)
        except (TicketNotFound, AmbiguousTicketReference) as exc:
            result.problems.append(f"{ref}: {exc}")
            continue
        if ticket.state in RESOLVED_STATES:
            result.already_closed.append(ticket.external_id)
            continue
        if has_owned_children(session, ticket.id):
            result.left_to_rollup.append(ticket.external_id)
            continue
        if _driven(session, ticket):
            result.problems.append(
                f"{ticket.external_id}: left open, an orchestration is still driving it"
            )
            continue
        _close(session, ticket, entry, merge_commit)
        result.closed.append(ticket.external_id)
    for problem in result.problems:
        logger.warning("Merge queue: PR #%s landed; ticket %s", entry.pr_number, problem)
    return result


def _driven(session: Session, ticket: Ticket) -> bool:
    return (
        session.exec(
            select(OrchestrationRun.id).where(
                OrchestrationRun.ticket_id == ticket.id,
                col(OrchestrationRun.status).in_(ACTIVE_ORCHESTRATION_STATUSES),
            )
        ).first()
        is not None
    )


def _close(session: Session, ticket: Ticket, entry: MergeQueueEntry, merge_commit: str) -> None:
    choose(session, ticket, TicketState.DONE, actor=RESOLVED_BY_QUEUE, emit=False)
    # Pinned, as an operator's close is: the work is on the base whatever the
    # stage map says, so `derive` must not reopen it from a stage left pending.
    ticket.state_locked = True
    session.add(ticket)
    session.commit()
    event_bus.publish(
        session,
        EventType.TICKET_STATE_CHANGED,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        payload={
            "state": TicketState.DONE.value,
            "actor": RESOLVED_BY_QUEUE,
            "reason": f"PR #{entry.pr_number} landed as {merge_commit[:12]}",
            "pr_number": entry.pr_number,
            "pr_url": entry.pr_url,
            "merge_commit": merge_commit,
        },
    )
    logger.info(
        "Merge queue: PR #%s landed; closed %s", entry.pr_number, ticket.external_id or ticket.id
    )
    reconcile_ancestors(session, ticket)
