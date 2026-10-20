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
from loregarden.models.domain import Ticket
from loregarden.models.domain.schemas import (
    ExternalHarnessPromptRequest,
    ExternalHarnessPromptView,
    StartRunRequest,
)
from loregarden.services.external_harness import build_external_harness_prompt
from loregarden.services.handoff_settle import settle_failed_handoff
from loregarden.services.handoff_tree_status import (
    HandoffTreePreparationError,
    describe_handoff_failure,
    describe_handoff_tree,
)
from loregarden.services.orchestration_profile import ProfileUnreadableError
from loregarden.services.run_service import RunService
from loregarden.services.stage_retry_budget import StageRetryBudgetExceeded
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
    # `ProfileUnreadableError` is a `RuntimeError`, so `except ValueError` stopped
    # reaching an unreadable profile the moment the load seam started classifying
    # one (lg-milestone-that-1215 S4). It is named here as the regression it
    # would otherwise be: this guard answered 400 for a schema-invalid profile
    # today, through pydantic's `ValidationError`.
    except (ProfileUnreadableError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not run:
        raise HTTPException(400, "This stage does not run a CLI agent")
    try:
        invocation, cleanup_path, tree = run_svc.executor.prepare_terminal_handoff(run, ticket)
        command = render_terminal_handoff_command(
            invocation, cleanup_path=cleanup_path, run_id=run.id
        )
    # Two arms, subclass first: `HandoffTreePreparationError` is a `ValueError`,
    # so a broader arm above it would swallow every tree failure. The split is
    # the whole of lg-milestone-that-975 on this edge — the tree sentence is
    # composed for a tree failure and for nothing else, and a failure nobody
    # classified keeps its own diagnosis as the lead sentence, because the toast
    # clamp makes the lead sentence the whole of what is read.
    except HandoffTreePreparationError as exc:
        detail = describe_handoff_failure(session, ticket, exc)
        settle_failed_handoff(session, run_svc, run, ticket, detail=detail)
        raise HTTPException(400, detail) from exc
    # The defect lg-milestone-that-1215 closes. Without the new name a profile
    # that will not parse propagates past this handler, the route 500s with
    # `{"detail": "Internal Server Error"}`, `settle_failed_handoff` on the next
    # line never runs, and the run this request stamped stays RUNNING with no
    # agent behind it. `ProfileUnreadableError` stays named here now that the
    # renderer wraps it: the name is the enumeration contract from
    # `orchestration_profile.py:44-51` — the sites that handle an unreadable
    # profile are the sites that name it — not a claim about reachability.
    except (ProfileUnreadableError, ValueError) as exc:
        detail = str(exc)
        settle_failed_handoff(session, run_svc, run, ticket, detail=detail)
        raise HTTPException(400, detail) from exc
    # Read at the boundary, after the refresh: the operator's question is not
    # "is there a command" but "can I paste this and trust the tree it will run
    # in?", and a server-side log hands them the stale tree and tells them
    # nothing (lg-milestone-that-917). The branch comes from the tree the
    # handoff prepared, never from a second resolution here: naming it is a
    # read, and the write-path resolver is not one (lg-milestone-that-974).
    status = describe_handoff_tree(tree, ticket_external_id=ticket.external_id)
    if status.warning:
        logger.warning("Terminal handoff for %s: %s", ticket.external_id, status.warning)
    return {
        "run_id": run.id,
        "adapter": invocation.adapter,
        "command": command,
        "repo_path": str(tree.repo_root),
        "target_branch": tree.target_branch,
        "tree_behind": status.behind,
        "tree_warning": status.warning,
    }


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
