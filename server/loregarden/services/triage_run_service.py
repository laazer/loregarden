"""Interactive triage turns: tool-using Baxter runs through the permission bridge.

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
from pathlib import Path
from tempfile import TemporaryDirectory

from loregarden.agents.cli_adapters import build_interactive_invocation
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
from loregarden.agents.registry import get_agent
from loregarden.db.session import engine
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TriageMessage, Workspace
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.cli_settings import (
    resolve_claude_model,
    resolve_effective_adapter,
)
from loregarden.services.run_concurrency import find_active_run
from loregarden.services.triage_service import (
    TRIAGE_AGENT_ID,
    TRIAGE_AGENT_NAME,
    apply_triage_runtime_overrides,
    build_triage_prompt,
    invoke_triage_model,
    list_triage_messages,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

TRIAGE_STAGE_KEY = "triage"


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
        agent = get_agent(TRIAGE_AGENT_ID) or {}
        selected = resolve_effective_adapter(
            agent_adapter=agent.get("adapter", "claude"), workspace=effective_workspace
        )

        if selected == "claude":
            self._execute_interactive(run, ticket, agent, effective_workspace)
        else:
            self._execute_one_shot(run, ticket)

    def _execute_one_shot(self, run: AgentRun, ticket: Ticket) -> None:
        history = list_triage_messages(self.session, ticket.id)
        latest_user_message = history[-1].content if history and history[-1].role == "user" else ""
        try:
            reply = invoke_triage_model(self.session, ticket, latest_user_message)
            self._finish(run, ticket, status=RunStatus.SUCCEEDED, reply=reply, stderr="")
        except Exception as exc:
            self._finish(
                run,
                ticket,
                status=RunStatus.FAILED,
                reply=f"{TRIAGE_AGENT_NAME} unavailable: {exc}",
                stderr=str(exc)[:4000],
            )

    def _execute_interactive(
        self, run: AgentRun, ticket: Ticket, agent: dict, workspace: Workspace
    ) -> None:
        repo_root = resolve_workspace_root(workspace)
        if not repo_root.is_dir():
            self._finish(
                run,
                ticket,
                status=RunStatus.FAILED,
                reply="",
                stderr=f"Workspace repo path does not exist: {repo_root}",
            )
            return

        history = list_triage_messages(self.session, ticket.id)
        latest_user_message = history[-1].content if history and history[-1].role == "user" else ""
        prompt = build_triage_prompt(
            ticket, history, latest_user_message, session=self.session, interactive=True
        )

        triage_claude_model = (
            os.environ.get("LOREGARDEN_TRIAGE_CLAUDE_MODEL", "").strip()
            or resolve_claude_model(workspace)
            or "haiku"
        )

        with TemporaryDirectory(prefix="loregarden-triage-") as tmp:
            prompt_file = Path(tmp) / "prompt.md"
            prompt_file.write_text(prompt, encoding="utf-8")
            invocation = build_interactive_invocation(
                adapter="claude",
                prompt_file=prompt_file,
                workspace_root=repo_root,
                claude_model=triage_claude_model,
            )
            timeout = int(os.environ.get("LOREGARDEN_TRIAGE_TIMEOUT") or agent.get("timeout", 1800))
            bridge = PermissionBridgeRunner(self.session, track_workflow_stage=False)
            result = bridge.run(
                run_id=run.id,
                ticket=ticket,
                invocation=invocation,
                prompt=prompt,
                timeout_seconds=timeout,
            )

        reply = extract_triage_reply(result.stdout)
        if result.status == RunStatus.SUCCEEDED and not reply:
            result.status = RunStatus.FAILED
            result.stderr = result.stderr or f"{TRIAGE_AGENT_NAME} returned an empty response"
        self._finish(run, ticket, status=result.status, reply=reply[:8000], stderr=result.stderr)

    def _finish(
        self, run: AgentRun, ticket: Ticket, *, status: RunStatus, reply: str, stderr: str
    ) -> None:
        run = self.session.get(AgentRun, run.id)
        if run:
            run.status = status
            run.stderr = stderr[:4000]
            run.finished_at = datetime.now(timezone.utc)
            self.session.add(run)
        assistant_message = TriageMessage(
            ticket_id=ticket.id,
            role="assistant",
            content=reply or (stderr[:2000] if status == RunStatus.FAILED else "(no reply)"),
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
                                content=f"{TRIAGE_AGENT_NAME} unavailable: {exc}",
                                run_id=run.id,
                            )
                        )
                    session.commit()
        except Exception:
            logger.exception(
                "Failed to mark triage run %s as failed after background error", run_id
            )


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
