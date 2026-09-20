"""The lifecycle of a run that executes in parallel with others.

Split out of OrchestrationService, which is the workflow state machine and was
already at its size cap before this. What lives here is a different job: give a
run an isolated checkout and get it into a slot. What happens to its work when
the ticket finishes is the landing at the terminal stage (`landing.py`).

Composed with OrchestrationService rather than inheriting from it — this needs
exactly one thing from it (``start_run``), and the queue has no business
reaching the rest of the state machine.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import AgentRun, Ticket, Workspace, Worktree
from loregarden.services.orchestration import OrchestrationService
from sqlmodel import Session

logger = logging.getLogger(__name__)


class ParallelRunService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)

    async def create_parallel_run(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        max_concurrent: int = 3,
        preferred_slot: int | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> dict:
        """
        Create a run with parallel execution support.

        Checks queue and either:
        - Starts immediately if slot available
        - Queues run if no slots available

        Args:
            ticket: Ticket to run
            stage_key: Stage to start (optional)
            max_concurrent: Max concurrent runs (default 3)
            preferred_slot: Slot the caller staged this ticket into, honoured
                when it is still free (optional)
            auto_approve: Auto-approve the CLI's permission prompts for this run
            timeout_seconds: Per-run timeout override, or None for the agent's
                own default

        These last two are per-run, not per-workspace: the queue board asks for
        them the same way the workflow's run dialog does, and a run started from
        either surface should honour them identically.

        Returns:
            {
                "status": "started" | "queued",
                "run": AgentRun (if started),
                "position": int (if queued),
                "message": str
            }
        """
        from loregarden.services.parallel_queue import ParallelQueueService
        from loregarden.services.run_service import schedule_agent_run

        try:
            queue_service = ParallelQueueService(self.session, max_concurrent=max_concurrent)

            # The run row is created now whether or not a slot is free. It used
            # to be deferred with `run_id=""` and a "created on promotion"
            # comment, but promotion never created it — so a queued ticket sat
            # forever, and the dashboard had nothing but a placeholder to show
            # for it. Creating it up front also means the queue snapshot can
            # name the ticket and agent behind every waiting row.
            run, worktree = self._prepare_parallel_run(
                ticket,
                stage_key=stage_key,
                auto_approve=auto_approve,
                timeout_seconds=timeout_seconds,
            )

            queue_result = await queue_service.queue_run(
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                run_id=run.id,
                preferred_slot=preferred_slot,
            )

            if queue_result.get("status") == "started":
                schedule_agent_run(run.id)
                return {
                    "status": "started",
                    "run": run,
                    "worktree_id": worktree.id if worktree else None,
                    "slot_number": queue_result.get("slot_number"),
                    "message": (
                        f"Started in {worktree.worktree_path}"
                        if worktree
                        else "Started in the workspace checkout"
                    ),
                }

            return {
                "status": "queued",
                "run": run,
                "worktree_id": worktree.id if worktree else None,
                "position": queue_result.get("position"),
                "queue_length": queue_result.get("queue_length"),
                "message": queue_result.get("message"),
            }

        except Exception as e:
            import logging

            logging.error(f"Error creating parallel run: {e}", exc_info=True)
            raise

    def _prepare_parallel_run(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> tuple[AgentRun, Worktree | None]:
        """Create the run row, and the worktree it will execute in.

        Whether it gets a worktree is the workspace's `git.worktree` policy (a
        ticket may override it). Without one the run executes in the shared
        workspace checkout, which is what every run did before this — fine for
        one run at a time, and the reason parallel runs used to trample each
        other's working tree.
        """
        from loregarden.services.git_automation_config import resolve_git_automation
        from loregarden.services.git_branch import resolve_ticket_branch
        from loregarden.services.target_branch import resolve_target_branch
        from loregarden.services.worktree_service import (
            WorktreeService,
            repo_path_for_workspace,
        )

        run = self.orchestration.start_run(
            ticket,
            stage_key=stage_key,
            auto_approve=auto_approve,
            timeout_override_seconds=timeout_seconds,
        )

        workspace = self.session.get(Workspace, ticket.workspace_id)
        config = resolve_git_automation(workspace, ticket) if workspace else None
        if not config or not config.worktree:
            return run, None

        repo_path = repo_path_for_workspace(self.session, ticket.workspace_id)
        worktree_service = WorktreeService(self.session, repo_path=repo_path)
        worktree = worktree_service.create_worktree(
            workspace_id=ticket.workspace_id,
            agent_run_id=run.id,
            parent_branch=resolve_target_branch(
                self.session, ticket, workspace, repo_root=Path(repo_path)
            ),
            branch=resolve_ticket_branch(ticket),
        )

        if worktree:
            run.worktree_id = worktree.id
            self.session.add(run)
            self.session.commit()
            self.session.refresh(run)

        return run, worktree
