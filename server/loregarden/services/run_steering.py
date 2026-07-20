"""Messages sent to a run while it is still going.

A stage used to be a closed box: once it started, the only ways to affect it
were to wait or to kill it. When an operator can see from the log that an agent
has misread the task, the cheap correction is a sentence — not a rerun.

The channel already existed. `PermissionBridgeRunner` spawns the CLI with stdin
held open and speaks stream-json to it, so another user message can be written
into a live session. This module is the queue between the API and that write.

Only the claude adapter can be steered, and that is a limit of the tool rather
than a choice here: `cursor-agent` exposes `--output-format stream-json` but no
`--input-format`, so there is no way to write into a run it is executing. Callers
get that reason back rather than a silent no-op — an operator who believes they
corrected a run, and did not, is worse off than one who was told they could not.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from loregarden.agents.registry import get_agent
from loregarden.models.domain import AgentRun, RunMessage, RunStatus
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Adapters whose CLI accepts further input on stdin mid-run.
STEERABLE_ADAPTERS = frozenset({"claude"})
MAX_MESSAGE_CHARS = 2000
#: How often the bridge checks for new messages. Each check is a fresh session,
#: so it is a real query; the run loop spins fast enough that polling every
#: iteration would swamp the DB for a channel used a few times an hour.
POLL_INTERVAL_SECONDS = 1.0


def steer_refusal(run: AgentRun | None) -> str:
    """Why this run cannot be steered, or "" when it can be.

    A single reason string keeps the API and the UI saying the same thing.
    """
    if run is None:
        return "Run not found."
    if run.status != RunStatus.RUNNING:
        return f"Run is {run.status.value.lower()}, so there is nothing to steer."
    adapter = (get_agent(run.agent_id) or {}).get("adapter", "")
    if adapter not in STEERABLE_ADAPTERS:
        return (
            f"The {run.agent_id} agent runs on the {adapter or 'local'} adapter, "
            "which cannot receive input once started."
        )
    return ""


def queue_message(session: Session, run: AgentRun, content: str) -> RunMessage:
    """Record a message for delivery. Raises ValueError if the run cannot take one."""
    body = content.strip()
    if not body:
        raise ValueError("Message is empty.")
    refusal = steer_refusal(run)
    if refusal:
        raise ValueError(refusal)

    message = RunMessage(run_id=run.id, ticket_id=run.ticket_id, content=body[:MAX_MESSAGE_CHARS])
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def pending_messages(session: Session, run_id: str) -> list[RunMessage]:
    """Undelivered messages for a run, oldest first."""
    return list(
        session.exec(
            select(RunMessage)
            .where(RunMessage.run_id == run_id, RunMessage.delivered_at.is_(None))
            .order_by(RunMessage.created_at)
        ).all()
    )


def mark_delivered(session: Session, message: RunMessage) -> None:
    message.delivered_at = datetime.now(timezone.utc)
    session.add(message)
    session.commit()


def list_messages(session: Session, run_id: str) -> list[RunMessage]:
    return list(
        session.exec(
            select(RunMessage).where(RunMessage.run_id == run_id).order_by(RunMessage.created_at)
        ).all()
    )
