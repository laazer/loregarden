from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import AgentRun, StageStatus, Ticket
from loregarden.services.interruption_messages import (
    INTERRUPTED_RUN_MESSAGE,
    INTERRUPTION_MESSAGES,
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    STRANDED_STAGE_MESSAGE,
    SUPERSEDED_RUN_MESSAGE,
)
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from sqlmodel import Session, select


def blocked_by_interruption(ticket: Ticket) -> bool:
    """Whether a reload artifact, rather than a real failure, blocks this ticket."""
    return (
        ticket.state not in StateMachine.TERMINAL_TICKET_STATES
        and ticket.blocking_issues in INTERRUPTION_MESSAGES
    )


def interrupted_stage_key(
    session: Session,
    ticket: Ticket,
    stage_map: dict[str, StageStatus],
) -> str | None:
    """Resolve the exact interrupted stage instead of trusting a reconciled cursor."""
    blocked_keys = {
        stage_key for stage_key, status in stage_map.items() if status == StageStatus.BLOCKED
    }
    if not blocked_keys:
        return None

    runs = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at.desc())
    ).all()
    for run in runs:
        # Triage turns are a side channel, not workflow runs — see run_service.fail_interrupted_runs for why (602).
        if run.agent_id == TRIAGE_AGENT_ID or run.stage_key not in blocked_keys:
            continue
        if (
            ticket.blocking_issues == INTERRUPTED_RUN_MESSAGE
            and run.stderr != INTERRUPTED_RUN_MESSAGE
        ):
            continue
        return run.stage_key
    return None


# Re-exported for the callers that have always imported them from here; the
# definitions live in `interruption_messages` so the transient-failure
# classifier can reach them without closing an import cycle.
__all__ = [
    "INTERRUPTED_RUN_MESSAGE",
    "INTERRUPTION_MESSAGES",
    "ORPHAN_OF_TERMINAL_ORCH_MESSAGE",
    "STRANDED_STAGE_MESSAGE",
    "SUPERSEDED_RUN_MESSAGE",
    "blocked_by_interruption",
    "interrupted_stage_key",
]
