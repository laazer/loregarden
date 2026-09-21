"""What the board shows for an occupied slot.

Two card shapes, one per occupant kind: a lane running a whole ticket, and a
single-stage run. Split out of `parallel_queue` by size; `_occupant_card`
there picks which to build and layers the repair hold on top.
"""

from __future__ import annotations

from loregarden.models.domain import AgentRun, OrchestrationRun
from sqlmodel import Session, col, select


def orchestration_card(session: Session, orchestration_run_id: str) -> dict | None:
    """A lane's card: the ticket it is running, described by its live stage.

    The status is the *lane's*, not the stage's. A ticket's pipeline spans
    many agent runs and the lane keeps holding the slot between them, so
    reporting whichever run finished last would have the card read
    "succeeded" while the ticket is still going.
    """
    orch_run = session.get(OrchestrationRun, orchestration_run_id)
    if not orch_run:
        return None

    run = session.exec(
        select(AgentRun)
        .where(AgentRun.orchestration_run_id == orchestration_run_id)
        .order_by(col(AgentRun.started_at).desc())
    ).first()
    return {
        "run_id": run.id if run else "",
        "orchestration_run_id": orch_run.id,
        "ticket_id": orch_run.ticket_id,
        # The pool is shared, so which workspace a card belongs to has to
        # travel with the card.
        "workspace_id": orch_run.workspace_id,
        "agent_id": run.agent_id if run else "",
        "stage_key": run.stage_key if run else orch_run.current_stage_key,
        "status": orch_run.status.value,
    }


def run_card(run: AgentRun) -> dict:
    """A single-stage run's card."""
    return {
        "run_id": run.id,
        "orchestration_run_id": run.orchestration_run_id or "",
        "ticket_id": run.ticket_id,
        "workspace_id": run.workspace_id,
        "agent_id": run.agent_id,
        "stage_key": run.stage_key,
        "status": run.status.value,
    }
