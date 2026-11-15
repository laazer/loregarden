"""Whether a ticket is held to the criteria check, and what it would leave unanswered.

Its own module, below `invariants`, because two sides ask it: the detector
(`unsettled_criteria_runs`), which reaches the orchestrator through the
invariant machinery, and the rollup (`ticket_rollup`), which the orchestrator
imports.
"""

from __future__ import annotations

from datetime import date, datetime

from loregarden.models.domain import OrchestrationRun, Ticket
from loregarden.services.criterion_gate_stages import runs_check
from loregarden.services.criterion_ledger import delegation_gaps, unsettled
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session, col, select

#: The first whole day the check before the gate was in force. Tickets driven
#: before it were never asked to settle their criteria, and recording them as
#: met now would invent evidence, so only tickets driven since are held to it.
#: One constant, read by the detector's own bound and by the invariant's
#: `since`: two copies is two answers to "what is backlog?".
#:
#: The day AFTER the check landed, not the day of it. #813 merged at 18:29 UTC
#: on 2026-11-07, and a midnight bound on that day held to it four tickets
#: whose last run started that morning, before it existed: measured on the live
#: database read-only 2026-11-15, lg-milestone-that-2230, -2232 and -2215
#: (finished 03:11 to 08:55 through a workflow that never asked) and
#: lg-milestone-that-1216 (last run 11:44), each reported as re-entry and
#: escalated as a falsified guard. `LAND_VERIFICATION_SINCE` records the same
#: mistake made once before.
CRITERIA_CHECKED_SINCE = date(2026, 11, 8)
CHECKED_SINCE = datetime(
    CRITERIA_CHECKED_SINCE.year, CRITERIA_CHECKED_SINCE.month, CRITERIA_CHECKED_SINCE.day
)


def _checked_by_workflow(session: Session, ticket: Ticket) -> bool:
    """Whether this ticket's workflow runs the check: it has a stage that settles criteria."""
    _, stages = resolve_ticket_stages(session, ticket)
    return runs_check(stages)


def _driven_since_the_check(session: Session, ticket: Ticket) -> bool:
    return (
        session.exec(
            select(OrchestrationRun.id)
            .where(OrchestrationRun.ticket_id == ticket.id)
            .where(col(OrchestrationRun.started_at) >= CHECKED_SINCE)
            .limit(1)
        ).first()
        is not None
    )


def criteria_left_unanswered(session: Session, ticket: Ticket) -> list[str]:
    """The criteria this ticket would finish with neither met nor delegated.

    [] for a ticket not held to the check: no criteria, not driven since it
    landed, or a workflow with no stage that settles criteria. The detector
    below and the rollup's hold on a parent (`ticket_rollup`) ask the same
    question through this, so a parent the rollup closes is one the detector
    would not report.
    """
    if ticket.acceptance_criteria_json == "[]":
        return []
    if not _driven_since_the_check(session, ticket) or not _checked_by_workflow(session, ticket):
        return []
    return unsettled(session, ticket) + [g.criterion for g in delegation_gaps(session, ticket)]
