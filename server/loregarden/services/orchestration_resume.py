"""Carrying a ticket's orchestration on after a person's decision moved it.

Lives apart from `ApprovalService` because run completion and the invariant
pass resume too (an operator override queued behind a running stage is applied
there), and `run_completion` sits below `orchestration` in the import graph.
"""

from __future__ import annotations

from loregarden.models.domain import OrchestrationDriver, OrchestrationRun, Ticket
from loregarden.services.run_concurrency import find_active_orchestration_run
from loregarden.services.scheduling import schedule_orchestration
from sqlmodel import Session, select


def resume_orchestration(session: Session, ticket: Ticket, *, carry_driver: bool = False) -> None:
    """Start the ticket's pipeline again unless an orchestration is already driving it.

    `auto_approve` is carried over from the run that reached the decision.
    Resuming without it would silently downgrade an unattended run into one
    that stops at the next tool prompt.

    ``carry_driver`` keeps the harness too, for an exit-action continuation:
    it continues the stage the previous run was driving, and handing it to
    the workspace's default driver instead could give it to a harness that
    is not there. A manual run has no orchestration to carry.
    """
    if find_active_orchestration_run(session, ticket.id):
        return

    previous = session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket.id)
        .order_by(OrchestrationRun.created_at.desc())
    ).first()
    driver = (
        previous.driver
        if carry_driver
        and previous is not None
        and previous.driver != OrchestrationDriver.MANUAL_STAGE
        else None
    )
    schedule_orchestration(
        ticket.id,
        driver=driver,
        auto_approve=bool(previous and previous.auto_approve),
        approve_design_plans=previous.approve_design_plans if previous else True,
        auto_repair=previous.auto_repair if previous else True,
    )
