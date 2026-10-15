"""Handing a stage to something this process does not supervise.

A human's own terminal (`terminal_handoff_command`) or their own harness
session (`external_harness_prompt`). Both render what a supervised run would
have received and answer the same question about the tree it will run in, so
they sit together in their own router rather than in `tickets.py`, which is a
hotspot (lg-milestone-that-917).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from loregarden.agents.cli_adapters import render_terminal_handoff_command
from loregarden.db.session import get_session
from loregarden.models.domain import AgentRun, RunStatus, StageStatus, Ticket, Workspace
from loregarden.models.domain.schemas import (
    ExternalHarnessPromptRequest,
    ExternalHarnessPromptView,
    StartRunRequest,
)
from loregarden.services.external_harness import build_external_harness_prompt
from loregarden.services.handoff_tree_status import (
    describe_handoff_failure,
    describe_handoff_tree,
)
from loregarden.services.run_service import RunService
from loregarden.services.stage_retry_budget import (
    StageRetryBudgetExceeded,
    refund_stage_dispatch_charged_before,
)
from loregarden.services.target_branch import TargetBranchError, resolve_target_branch
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("/{ticket_id}/terminal_handoff_command")
def build_terminal_handoff_command(
    ticket_id: str,
    body: StartRunRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Prepare a stage run for a human to execute in their own terminal.

    Creates the same AgentRun/workflow-running state as POST /start, but returns a
    ready-to-paste CLI command instead of spawning the agent as a child of this process —
    so the run survives an app server restart.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    # Deliberately not admission-gated: this returns a command for a human to
    # paste, which may happen minutes or hours later. Capacity now says nothing
    # about capacity then, and holding a slot in the meantime would strand it.
    run_svc = RunService(session)
    try:
        run = run_svc.start_stage_execution(
            ticket,
            stage_key=body.stage_key,
            auto_approve=body.auto_approve,
            timeout_seconds=body.timeout_seconds,
            force=body.force,
        )
    except StageRetryBudgetExceeded as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not run:
        raise HTTPException(400, "This stage does not run a CLI agent")
    try:
        invocation, cleanup_path, repo_root = run_svc.executor.prepare_terminal_handoff(run, ticket)
        command = render_terminal_handoff_command(
            invocation, cleanup_path=cleanup_path, run_id=run.id
        )
    except ValueError as exc:
        detail = describe_handoff_failure(session, ticket, exc)
        _settle_failed_handoff(session, run_svc, run, ticket, detail=detail)
        raise HTTPException(400, detail) from exc
    target_branch = _handoff_target_branch(session, ticket)
    # Read at the boundary, after the refresh: the operator's question is not
    # "is there a command" but "can I paste this and trust the tree it will run
    # in?", and a server-side log hands them the stale tree and tells them
    # nothing (lg-milestone-that-917).
    status = describe_handoff_tree(
        repo_root, target_branch=target_branch, ticket_external_id=ticket.external_id
    )
    if status.warning:
        logger.warning("Terminal handoff for %s: %s", ticket.external_id, status.warning)
    return {
        "run_id": run.id,
        "adapter": invocation.adapter,
        "command": command,
        "repo_path": str(repo_root),
        "target_branch": target_branch,
        "tree_behind": status.behind,
        "tree_warning": status.warning,
    }


def _handoff_target_branch(session: Session, ticket: Ticket) -> str:
    """The branch the handed-over tree was brought up to, or "" if it cannot be named.

    Resolved against the shared checkout, like every other caller: resolution
    writes refs, and refs are shared by every worktree of a repository.
    """
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        return ""
    try:
        return resolve_target_branch(
            session, ticket, workspace, repo_root=resolve_workspace_root(workspace)
        )
    except TargetBranchError as exc:
        # The tree was prepared, so this cannot be what failed the handoff —
        # report the command without naming a branch rather than 500 on it.
        logger.warning("Could not name %s's target branch for its handoff: %s", ticket.id, exc)
        return ""


def _settle_failed_handoff(
    session: Session,
    run_svc: RunService,
    run: AgentRun,
    ticket: Ticket,
    *,
    detail: str,
) -> None:
    """Close out the run `start_stage_execution` opened one call earlier.

    Without this a failed tree preparation leaves a stage reading RUNNING with
    no agent behind it, for `fail_interrupted_runs` to kill on somebody else's
    pass. Three steps, because the orphan shape in `run_service` settles the
    *run* and hands back its charge but says nothing about the stage: the stage
    is put back to PENDING, which is what it was before a dispatch that never
    attempted the work.
    """
    logger.warning("Terminal handoff for %s failed: %s", ticket.external_id, detail)
    run_svc.orchestration.complete_run(
        run, status=RunStatus.FAILED, stderr=detail, advance_workflow=False
    )
    if run.stage_key:
        refund_stage_dispatch_charged_before(session, ticket.id, run.stage_key, run.created_at)
        run_svc.orchestration.finalize_stage(ticket, run.stage_key, status=StageStatus.PENDING)
    session.refresh(ticket)


@router.post("/{ticket_id}/external_harness_prompt", response_model=ExternalHarnessPromptView)
def build_external_harness_prompt_endpoint(
    ticket_id: str,
    body: ExternalHarnessPromptRequest,
    session: Session = Depends(get_session),
) -> ExternalHarnessPromptView:
    """Render the prompt an operator pastes into their own Claude Code / Codex session.

    Read-only: no run is opened here. The pasted prompt opens one when the harness
    actually starts, so a prompt copied and never used costs nothing and the
    recorded start time is the real one.
    """
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    try:
        return build_external_harness_prompt(session, ticket, harness=body.harness)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
