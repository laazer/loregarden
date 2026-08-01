"""The lifecycle of a run that executes in parallel with others.

Split out of OrchestrationService, which is the workflow state machine and was
already at its size cap before this. What lives here is a different job: give a
run an isolated checkout, get it into a slot, and decide what happens to its
work when it finishes.

Composed with OrchestrationService rather than inheriting from it — this needs
exactly one thing from it (``start_run``), and the queue has no business
reaching the rest of the state machine.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import AgentRun, Ticket, Workspace, Worktree
from loregarden.services.git_automation import AutomationResult
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import GitAutomationConfig
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
            run, worktree = self._prepare_parallel_run(ticket, stage_key=stage_key)

            queue_result = await queue_service.queue_run(
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                run_id=run.id,
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
        from loregarden.services.worktree_service import (
            WorktreeService,
            repo_path_for_workspace,
        )

        run = self.orchestration.start_run(ticket, stage_key=stage_key)

        workspace = self.session.get(Workspace, ticket.workspace_id)
        config = resolve_git_automation(workspace, ticket) if workspace else None
        if not config or not config.worktree:
            return run, None

        worktree_service = WorktreeService(
            self.session,
            repo_path=repo_path_for_workspace(self.session, ticket.workspace_id),
        )
        worktree = worktree_service.create_worktree(
            workspace_id=ticket.workspace_id,
            agent_run_id=run.id,
            parent_branch=config.base_branch,
            branch=resolve_ticket_branch(ticket),
        )

        if worktree:
            run.worktree_id = worktree.id
            self.session.add(run)
            self.session.commit()
            self.session.refresh(run)

        return run, worktree

    async def on_parallel_run_complete(
        self,
        run: AgentRun,
        auto_merge: bool = False,
    ) -> dict:
        """
        Called when a parallel run completes.

        Handles:
        - Publishing the run's work per the workspace's git automation policy
        - Freeing the slot
        - Promoting the next run from the queue

        Args:
            run: Completed AgentRun
            auto_merge: Ignored; kept for callers that still pass it. Whether to
                merge is the workspace's `git.auto_merge` policy, so that one
                queue-wide setting governs every run rather than each call site
                deciding.

        Returns:
            {
                "status": "merged" | "failed" | "conflicts",
                "next_run": AgentRun (if promoted),
                "message": str
            }
        """
        from loregarden.services.parallel_queue import ParallelQueueService

        try:
            automation = self._publish_run_work(run)
            if automation and not automation.get("ok"):
                # Publishing failed (a rejected push, a conflicted merge). The
                # slot still has to be freed and the queue still has to drain —
                # holding a slot for work nobody can land stops the whole queue.
                await ParallelQueueService(self.session, max_concurrent=3).on_run_complete(
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                )
                return {
                    "status": "conflicts",
                    "automation": automation,
                    "message": automation.get("message", "Could not publish this run's work"),
                }

            # Free slot and promote from queue
            queue_service = ParallelQueueService(self.session, max_concurrent=3)
            promotion = await queue_service.on_run_complete(
                workspace_id=run.workspace_id,
                run_id=run.id,
            )

            if promotion and promotion.get("status") == "promoted":
                return {
                    "status": "merged",
                    "next_run": promotion.get("next_run"),
                    "message": promotion.get("message"),
                }
            else:
                return {
                    "status": "merged",
                    "message": "Run completed, slot freed, queue empty",
                }

        except Exception as e:
            import logging

            logging.error(f"Error on parallel run complete: {e}", exc_info=True)
            raise

    def _publish_run_work(self, run: AgentRun) -> dict | None:
        """Commit / push / PR / auto-merge this run's work, as configured.

        Returns None when there is nothing to do, and a result dict otherwise.
        A failure here is reported, never raised: the agent's work is already
        on disk, and losing the slot-freeing that follows would stall the queue
        over a publishing problem.
        """
        from loregarden.services.git_automation import run_git_automation
        from loregarden.services.git_automation_config import resolve_git_automation
        from loregarden.services.worktree_service import WorktreeService, repo_path_for_workspace

        ticket = self.session.get(Ticket, run.ticket_id)
        workspace = self.session.get(Workspace, run.workspace_id)
        if not ticket or not workspace:
            return None

        config = resolve_git_automation(workspace, ticket)
        result = run_git_automation(self.session, run, ticket, config)
        if not result.ok:
            failure = result.failure
            return {
                **result.as_dict(),
                "message": f"{failure.step} failed: {failure.detail}" if failure else "failed",
            }

        if not run.worktree_id or not config.auto_merge:
            return result.as_dict() if result.steps else None

        worktree_service = WorktreeService(
            self.session,
            repo_path=repo_path_for_workspace(self.session, run.workspace_id),
        )
        worktree = worktree_service.get_worktree(run.worktree_id)
        if not worktree:
            return result.as_dict() if result.steps else None

        # Conflicts are checked even on the PR path. `gh pr merge --auto` will
        # sit on a conflicted PR indefinitely rather than report anything back,
        # so without this the auto-resolve setting could never fire for the one
        # configuration anybody actually runs.
        if worktree_service.detect_conflicts(worktree, config.base_branch):
            return self._handle_merge_conflicts(run, ticket, workspace, worktree, config, result)

        # Clean. With a PR open, auto-merge lands it once checks pass — merging
        # here too would land the work twice and leave the PR merging an empty
        # diff. Without one, this is the only thing that lands it.
        if config.open_pr:
            return {**result.as_dict(), "merge": "deferred to pull request"}

        merged = worktree_service.merge_worktree(
            worktree,
            target_branch=config.base_branch,
            auto_resolve=False,
        )
        if merged:
            return {**result.as_dict(), "merged": True}

        return {
            **result.as_dict(),
            "ok": False,
            "worktree_id": worktree.id,
            "message": "Merge failed after a clean conflict check",
        }

    def _handle_merge_conflicts(
        self,
        run: AgentRun,
        ticket: Ticket,
        workspace: Workspace,
        worktree: Worktree,
        config: GitAutomationConfig,
        result: AutomationResult,
    ) -> dict:
        """Either hand the conflict to an agent, or report it and stop."""
        from loregarden.services.conflict_resolution import request_agent_resolution
        from loregarden.services.workspace_paths import resolve_run_root, resolve_workspace_root

        if not config.auto_resolve_conflicts:
            return {
                **result.as_dict(),
                "ok": False,
                "worktree_id": worktree.id,
                "conflict_files": worktree.conflict_files,
                "message": f"Merge conflicts in {len(worktree.conflict_files)} files",
            }

        repo_root = resolve_run_root(self.session, run, resolve_workspace_root(workspace))
        report = request_agent_resolution(
            self.session,
            run,
            ticket,
            worktree,
            repo_root,
            max_attempts=config.max_conflict_resolve_attempts,
        )
        if report:
            return {
                **result.as_dict(),
                "ok": True,
                "resolving_conflicts": True,
                "attempt": report.merge_attempt_number,
                "message": (
                    f"Merge conflicts in {len(report.conflicting_files)} files; "
                    f"resolution attempt {report.merge_attempt_number} dispatched"
                ),
            }

        return {
            **result.as_dict(),
            "ok": False,
            "worktree_id": worktree.id,
            "conflict_files": worktree.conflict_files,
            "message": "Merge conflicts remain after the resolution budget was spent",
        }
