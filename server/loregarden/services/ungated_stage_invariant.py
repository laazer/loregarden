"""Invariant: no stage is dispatched past one whose transition gate never ran.

Kept apart from `owed_gate` (the guard), which the orchestrator imports; the
invariant machinery reaches the orchestrator, so the guard must not import it.
"""

from __future__ import annotations

from datetime import date

from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    OrchestrationDriver,
    OrchestrationRun,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.owed_gate import finished_runs, gate_settled, tree_passed_since
from loregarden.services.studio_routing import is_terminal_stage
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, col, select


def stages_left_ungated(session: Session) -> list[Violation]:
    """Open tickets' DONE stages that a later stage was dispatched past, ungated.

    Entered when that later run was created. Clears when a gate passes on the
    ticket afterwards (it reads the same tree) or the stage runs again.
    """
    open_tickets = session.exec(
        select(Ticket).where(col(Ticket.state).not_in([TicketState.DONE, TicketState.WONT_DO]))
    ).all()
    return [v for ticket in open_tickets for v in _left_ungated(session, ticket)]


def _left_ungated(session: Session, ticket: Ticket) -> list[Violation]:
    runs = finished_runs(session, ticket.id)
    instance = workflow_instance_for(session, ticket.id) if runs else None
    if instance is None:
        return []
    _, stages = resolve_ticket_stages(session, ticket)
    stage_map = parse_stage_map(instance, stages)
    order = {stage.key: stage.order for stage in stages if not is_terminal_stage(stage)}
    latest: dict[str, AgentRun] = {}
    for run in runs:
        latest.setdefault(run.stage_key, run)

    found: list[Violation] = []
    for key, run in latest.items():
        if key not in order or stage_map.get(key) is not StageStatus.DONE:
            continue
        past = _dispatched_past(session, run, order)
        if past is None or gate_settled(session, run) or tree_passed_since(session, run):
            continue
        found.append(
            Violation(
                subject_id=run.id,
                ticket_id=ticket.id,
                entered_at=past.created_at,
                detail=f"{ticket.external_id}: '{past.stage_key}' ({past.run_code}) was dispatched "
                f"past '{key}' ({run.run_code}), whose gate never ran",
            )
        )
    return found


def _dispatched_past(session: Session, run: AgentRun, order: dict[str, int]) -> AgentRun | None:
    """The first builtin run of a later stage created after ``run`` finished."""
    later = session.exec(
        select(AgentRun)
        .join(OrchestrationRun, col(OrchestrationRun.id) == col(AgentRun.orchestration_run_id))
        .where(
            AgentRun.ticket_id == run.ticket_id,
            AgentRun.asked_by_run_id == "",
            col(AgentRun.created_at) >= run.finished_at,
            OrchestrationRun.driver == OrchestrationDriver.BUILTIN_AUTOPILOT,
        )
        .order_by(col(AgentRun.created_at))
    ).all()
    return next((r for r in later if order.get(r.stage_key, -1) > order[run.stage_key]), None)


NO_STAGE_RUNS_PAST_AN_UNRUN_GATE = Invariant(
    name="no_stage_runs_past_an_unrun_gate",
    ticket="lg-workflow-integrity-2777",
    statement="no stage is dispatched past a finished stage whose transition gate never ran",
    detect=stages_left_ungated,
    # A gate runs shell commands under a capacity lease and may send the stage
    # back to its agent; the reconcile pass is no place for either.
    repair=None,
    unrepaired_action=(
        "Let the ticket run: the report clears when its next gate passes, which reads the same "
        "tree. If nothing is left to run, requeue the stage the report names."
    ),
    reentry=InvariantReentry.DEFECT,
    # The day after the guard was written: the live server drives without it
    # until it restarts on the merged build.
    since=date(2026, 11, 25),
    prevented_by=(
        "server/tests/test_owed_gate.py"
        "::test_a_stage_that_finished_with_no_driver_is_gated_before_the_next_one"
    ),
)
