"""Interactive triage turns: tool-using Baxter via the shared agent-turn runner.

Deliberately separate from ``run_service.py``'s stage-run executor — a triage
turn must not check out a git branch, advance the workflow stage, or call
``OrchestrationService.complete_run()``. It is a side channel, not the active
workflow stage.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import datetime, timezone

from loregarden.agents.registry import get_agent
from loregarden.db.session import engine
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TriageMessage, Workspace
from loregarden.services.agent_turn_runner import (
    AgentTurnRequest,
    resolve_chat_intent,
    run_agent_turn,
)
from loregarden.services.chat_primitives import parts_json_for_reply
from loregarden.services.chat_thinking import (
    finish_chat_turn_thinking,
    with_thinking_part,
)
from loregarden.services.cli_agent_runner import stub_response
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.cli_settings import resolve_effective_adapter
from loregarden.services.run_concurrency import find_active_run
from loregarden.services.run_service import fail_stale_handoff_runs
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    TRIAGE_CLI_PROFILE,
    apply_triage_runtime_overrides,
    build_triage_prompt,
    list_triage_messages,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

TRIAGE_STAGE_KEY = "triage"

INTERRUPTED_TURN_MESSAGE = (
    f"{TRIAGE_AGENT_NAME} was interrupted by a server restart and did not finish this turn. "
    "Send the message again."
)


class TriageConflictError(ValueError):
    """Raised when a triage turn can't start because a run is already in flight."""


def _run_code() -> str:
    return f"run_{secrets.token_hex(3)}"


def start_triage_run(
    session: Session, ticket: Ticket, content: str, *, auto_approve: bool = False
) -> tuple[TriageMessage, AgentRun]:
    """Validate, guard concurrency, persist the user message, and queue a run.

    Does not execute anything — call ``schedule_triage_turn(run.id)`` next.
    """
    text = content.strip()
    if not text:
        raise ValueError("Message cannot be empty")

    # A terminal-handoff run that was never pasted (or whose terminal died) sits
    # RUNNING forever with no process behind it — reap provably dead ones so a
    # phantom run cannot lock the operator out of chat.
    fail_stale_handoff_runs(session, ticket_id=ticket.id)

    if find_active_run(session, ticket.id):
        raise TriageConflictError(
            "Another run is already active for this ticket — wait for it to finish."
        )

    user_message = TriageMessage(ticket_id=ticket.id, role="user", content=text)
    session.add(user_message)
    ticket.revision += 1
    ticket.updated_at = datetime.now(timezone.utc)
    session.add(ticket)

    run = AgentRun(
        run_code=_run_code(),
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=TRIAGE_AGENT_ID,
        stage_key=TRIAGE_STAGE_KEY,
        status=RunStatus.QUEUED,
        auto_approve=auto_approve,
    )
    session.add(run)
    session.commit()
    session.refresh(user_message)
    session.refresh(run)

    user_message.run_id = run.id
    session.add(user_message)
    session.commit()
    session.refresh(user_message)

    return user_message, run


class TriageTurnExecutor:
    def __init__(self, session: Session) -> None:
        self.session = session

    def execute(self, run: AgentRun, ticket: Ticket) -> None:
        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            self._finish(
                run, ticket, status=RunStatus.FAILED, reply="", stderr="Ticket workspace not found"
            )
            return

        effective_workspace = apply_triage_runtime_overrides(workspace, ticket)
        stub = stub_response(TRIAGE_CLI_PROFILE)
        if stub is not None:
            self._finish(run, ticket, status=RunStatus.SUCCEEDED, reply=stub[:8000], stderr="")
            return

        agent = get_agent(TRIAGE_AGENT_ID) or {}
        selected = resolve_effective_adapter(
            agent_adapter=agent.get("adapter", "claude"), workspace=effective_workspace
        )
        # Capability map — not adapter name. Codex/Cursor/LM Studio execute via
        # writable oneshot; Claude via the permission bridge.
        intent = resolve_chat_intent(selected)
        history = list_triage_messages(self.session, ticket.id)
        latest_user_message = history[-1].content if history and history[-1].role == "user" else ""
        prompt = build_triage_prompt(
            ticket,
            history,
            latest_user_message,
            session=self.session,
            interactive=intent == "execute",
        )
        try:
            turn = run_agent_turn(
                AgentTurnRequest(
                    session=self.session,
                    workspace=effective_workspace,
                    prompt=prompt,
                    profile=TRIAGE_CLI_PROFILE,
                    agent=agent,
                    intent=intent,
                    adapter=selected,
                    turn_id=run.id,
                    run_id=run.id,
                    manage_run=False,
                    ticket=ticket if intent == "execute" else None,
                    claude_model_env="LOREGARDEN_TRIAGE_CLAUDE_MODEL",
                    track_workflow_stage=False,
                )
            )
            self._finish(
                run, ticket, status=RunStatus.SUCCEEDED, reply=turn.reply[:8000], stderr=""
            )
        except Exception as exc:
            self._finish(
                run,
                ticket,
                status=RunStatus.FAILED,
                reply=format_agent_unavailable(TRIAGE_AGENT_NAME, exc),
                stderr=str(exc)[:4000],
            )

    def _finish(
        self, run: AgentRun, ticket: Ticket, *, status: RunStatus, reply: str, stderr: str
    ) -> None:
        # Held before the re-read: the run row is what identifies this turn's
        # thinking channel, and a re-read that comes back empty must still
        # release it rather than leave the row behind.
        turn_id = run.id
        run = self.session.get(AgentRun, run.id)
        if run:
            run.status = status
            run.stderr = stderr[:4000]
            run.finished_at = datetime.now(timezone.utc)
            self.session.add(run)
        content = reply or (stderr[:2000] if status == RunStatus.FAILED else "(no reply)")
        # Always taken, including on the failure paths: what the agent was
        # working through when it died is the most useful thing it produced.
        thinking = finish_chat_turn_thinking(self.session, turn_id)
        assistant_message = TriageMessage(
            ticket_id=ticket.id,
            role="assistant",
            content=content,
            # Resolved at write time so the thread renders its cards after a
            # reload instead of degrading to the raw fenced JSON.
            parts_json=with_thinking_part(
                parts_json_for_reply(self.session, content, workspace_id=ticket.workspace_id),
                thinking,
            ),
            run_id=run.id if run else None,
        )
        self.session.add(assistant_message)
        self.session.commit()


def execute_triage_turn_background(run_id: str) -> None:
    """Fresh-session background execution; mirrors run_service.execute_agent_run_background."""
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            if not run:
                logger.error("Background triage run not found: %s", run_id)
                return
            ticket = session.get(Ticket, run.ticket_id)
            if not ticket:
                logger.error("Background triage run ticket not found: %s", run_id)
                return
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
            TriageTurnExecutor(session).execute(run, ticket)
    except Exception as exc:
        logger.exception("Background triage turn failed: %s", run_id)
        try:
            with Session(engine) as session:
                run = session.get(AgentRun, run_id)
                if run and run.status in {RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}:
                    run.status = RunStatus.FAILED
                    run.stderr = str(exc)[:4000]
                    run.finished_at = datetime.now(timezone.utc)
                    session.add(run)
                    ticket = session.get(Ticket, run.ticket_id)
                    if ticket:
                        session.add(
                            TriageMessage(
                                ticket_id=ticket.id,
                                role="assistant",
                                content=format_agent_unavailable(TRIAGE_AGENT_NAME, exc),
                                run_id=run.id,
                            )
                        )
                    session.commit()
        except Exception:
            logger.exception(
                "Failed to mark triage run %s as failed after background error", run_id
            )


def fail_interrupted_triage_turns(
    session: Session, *, message: str = INTERRUPTED_TURN_MESSAGE
) -> list[AgentRun]:
    """Settle triage turns orphaned by a restart, replying in the chat.

    Deliberately does not call ``OrchestrationService.complete_run()`` — a triage turn
    must not advance the workflow stage (see this module's docstring). Without the
    assistant message the operator's turn just goes silent and looks dropped.

    Home-chat and branch-triage also mint ``agent_id=triage`` runs, but with a null
    ``ticket_id``. Those surfaces have their own pending-message reapers; this
    path only writes ``triage_messages`` for ticket-scoped turns. Ticket-less
    orphans are still marked failed so they cannot hold the composer forever.
    """
    orphaned = session.exec(
        select(AgentRun).where(
            AgentRun.agent_id == TRIAGE_AGENT_ID,
            col(AgentRun.status).in_(
                [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]
            ),
        )
    ).all()
    settled: list[AgentRun] = []
    for run in orphaned:
        run.status = RunStatus.FAILED
        run.stderr = message[:4000]
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        if run.ticket_id:
            session.add(
                TriageMessage(
                    ticket_id=run.ticket_id,
                    role="assistant",
                    content=message,
                    run_id=run.id,
                )
            )
        settled.append(run)
    if settled:
        session.commit()
    return settled


def schedule_triage_turn(run_id: str) -> None:
    """Queue triage-turn execution without blocking the API request thread."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_triage_turn_background(run_id)
        return
    thread = threading.Thread(
        target=execute_triage_turn_background,
        args=(run_id,),
        name=f"loregarden-triage-{run_id[:8]}",
        daemon=True,
    )
    thread.start()
