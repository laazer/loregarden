"""Reconcile steps that resume a blocked stage whose cause clears by itself.

Two causes, both of which used to wait for a person who had nothing to decide:

- **A usage limit that has reset.** `run_completion` parks the stage behind a
  `USAGE_LIMIT_WAIT` marker holding the provider's reset time
  (`usage_limit_wait`); this resumes it once that time has passed.
- **A stranded stage.** `settle_stranded_stages` blocks a stage left RUNNING
  with no run and no drive behind it. Nothing judged the work: the run ended
  (a restart, a crash between committing the run and advancing the ticket)
  before its stage was settled. Boot already resumes these
  (`resume_interrupted_orchestrations`); the periodic pass settles them mid-day
  too, and they then sat until the next restart. This resumes them on the same
  pass, charged to the stage's transient-retry budget, so a stage that strands
  every time stops after `max_transient_retries` and stays with a person.

Both go through `requeue_and_plan_resume`, the step network waits use: a drive
that already owns the ticket is left alone, and an external harness or a
cancelled run is requeued for its own driver rather than driven from here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import StageStatus, Ticket
from loregarden.models.domain.enums import utcnow
from loregarden.services.interruption_messages import STRANDED_STAGE_MESSAGE
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    admit_resumes,
    requeue_and_plan_resume,
    schedule_interrupted_resumes,
)
from loregarden.services.stage_retry_budget import resolve_stage_retry_budget
from loregarden.services.stage_transient_retry import (
    count_transient_retries,
    record_transient_retry,
)
from loregarden.services.usage_limit_wait import (
    has_reset,
    pending_usage_limit_waits,
    read_wait,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

USAGE_LIMIT_RESUME_REASON = "The provider's usage limit has reset."
STRANDED_RESUME_REASON = (
    "The stage was left running with no run behind it; nothing judged the work, so it "
    "is re-run automatically."
)


def _parked_on(ticket: Ticket, stage_key: str) -> bool:
    return (
        ticket.workflow_stage_key == stage_key
        and ticket.workflow_stage_status is StageStatus.BLOCKED
    )


def _start(session: Session, plans: list[tuple[Ticket, InterruptionResume]], why: str) -> list[str]:
    requests, handled = admit_resumes(session, plans)
    schedule_interrupted_resumes(requests)
    if handled:
        logger.warning(
            "%s; resumed %d parked ticket(s) (%d started now): %s",
            why,
            len(handled),
            len(requests),
            ", ".join(handled),
        )
    return handled


def resume_usage_limit_waits(session: Session, *, now: datetime | None = None) -> list[str]:
    """Requeue and restart every stage whose usage limit has reset.

    A marker whose ticket is no longer blocked on its stage (a person got there
    first) is dropped without touching the ticket. One not yet due is kept.
    """
    now = now or utcnow()
    plans: list[tuple[Ticket, InterruptionResume]] = []
    for marker in pending_usage_limit_waits(session):
        wait = read_wait(marker)
        if wait is None or not has_reset(wait, now=now):
            continue
        ticket = session.get(Ticket, marker.ticket_id)
        session.delete(marker)
        session.commit()
        if ticket is None or not _parked_on(ticket, wait.stage_key):
            continue
        plan = requeue_and_plan_resume(
            session, ticket, wait.stage_key, reason=USAGE_LIMIT_RESUME_REASON
        )
        if plan is not None:
            plans.append((ticket, plan))
    return _start(session, plans, "Usage limits have reset")


def resume_stranded_stages(session: Session) -> list[str]:
    """Re-run every stranded stage that still has transient-retry budget."""
    plans: list[tuple[Ticket, InterruptionResume]] = []
    stranded = session.exec(
        select(Ticket).where(
            Ticket.workflow_stage_status == StageStatus.BLOCKED,
            Ticket.blocking_issues == STRANDED_STAGE_MESSAGE,
            col(Ticket.state).not_in(StateMachine.TERMINAL_TICKET_STATES),
        )
    ).all()
    for ticket in stranded:
        stage_key = ticket.workflow_stage_key
        config = resolve_stage_retry_budget(session, ticket)
        spent = count_transient_retries(session, ticket.id, stage_key)
        if spent >= config.max_transient_retries:
            # Stranded on every attempt: something is wrong beyond a lost run.
            # Its settlement already recorded why it waits for a person.
            continue
        plan = requeue_and_plan_resume(session, ticket, stage_key, reason=STRANDED_RESUME_REASON)
        if plan is None:
            continue
        record_transient_retry(
            session,
            ticket.id,
            stage_key,
            run_id="",
            reason="stranded_stage",
            message=(
                f"'{stage_key}' was stranded with no run behind it; re-run automatically "
                f"(retry {spent + 1} of {config.max_transient_retries})."
            ),
        )
        plans.append((ticket, plan))
    return _start(session, plans, "Stranded stages")
