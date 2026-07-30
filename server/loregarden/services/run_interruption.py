from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import AgentRun, StageStatus, Ticket
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from sqlmodel import Session, select

INTERRUPTED_RUN_MESSAGE = (
    "Agent run interrupted before completion (server reload or worker stopped). "
    "Re-run the stage to continue."
)

STRANDED_STAGE_MESSAGE = (
    "Stage was left running with no agent run behind it (the run ended before its "
    "stage was settled). Re-run the stage to continue."
)

INTERRUPTION_MESSAGES = frozenset({INTERRUPTED_RUN_MESSAGE, STRANDED_STAGE_MESSAGE})


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
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .order_by(AgentRun.created_at.desc())
    ).all()
    for run in runs:
        if run.agent_id == TRIAGE_AGENT_ID or run.stage_key not in blocked_keys:
            continue
        if (
            ticket.blocking_issues == INTERRUPTED_RUN_MESSAGE
            and run.stderr != INTERRUPTED_RUN_MESSAGE
        ):
            continue
        return run.stage_key
    return None
