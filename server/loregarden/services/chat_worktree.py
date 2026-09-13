"""Which checkout a chat thread's acting turns execute in.

The conversational sibling of `ticket_worktree`. Stage runs were given their
own worktree because two tickets cannot share one working tree; chat acting
turns were left behind on that migration and kept executing in the shared
workspace checkout, because `agent_turn_runner` had nothing else to resolve to.

That is the same two failure modes plus a third: a chat turn's edits landed on
whatever branch the operator had checked out, uncommitted and attributed to no
run, where the next orchestration's tree sweep would fold them into an
unrelated ticket's commit.

Separate module from `ticket_worktree` rather than a branch inside it: the two
resolve different owners from different tables, and the only thing they share
is the fallback, which lives in `worktree_service`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import AgentRun, BaxterChatSession, Workspace
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session

logger = logging.getLogger(__name__)


def resolve_chat_thread_root(
    session: Session,
    chat_session: BaxterChatSession,
    workspace: Workspace,
) -> Path:
    """Where this thread's work already lives, without conjuring a tree.

    Read-only counterpart of :func:`resolve_chat_execution_root`, and the reason
    advisory turns are not simply pinned to the workspace root: a thread that
    has already acted must be *read* in the tree it wrote to, or "what did you
    change?" is answered from a checkout that does not contain the change.
    """
    workspace_root = resolve_workspace_root(workspace)
    service = WorktreeService(session, repo_path=str(workspace_root))
    worktree = service.active_worktree_for_chat_session(chat_session.id)
    if worktree and worktree.worktree_path and Path(worktree.worktree_path).is_dir():
        return Path(worktree.worktree_path)
    return workspace_root


def resolve_chat_execution_root(
    session: Session,
    run: AgentRun,
    chat_session: BaxterChatSession,
    workspace: Workspace,
) -> Path:
    """The directory this acting turn should execute in, creating a tree if needed.

    Falls back to the shared checkout rather than failing the turn, matching
    `ticket_worktree.resolve_execution_root`: a worktree that cannot be cut is a
    degraded turn, not a dead conversation. The caller is expected to say so —
    see `chat_publish.describe_chat_worktree`, which is what keeps the fallback
    from being a silent downgrade back to the behaviour this module exists to
    remove.
    """
    workspace_root = resolve_workspace_root(workspace)
    config = resolve_git_automation(workspace)
    if not config.worktree:
        return workspace_root

    service = WorktreeService(session, repo_path=str(workspace_root))
    worktree = service.get_or_create_for_chat_session(
        chat_session, run.id, parent_branch=config.base_branch
    )
    if not worktree:
        logger.warning(
            "Could not create a worktree for chat session %s; running in the shared checkout %s",
            chat_session.id,
            workspace_root,
        )
        return workspace_root

    if run.worktree_id != worktree.id:
        # The run has to carry the tree it ran in: `workspace_paths.resolve_run_root`
        # and `git_automation` both start from `run.worktree_id`, so a turn that
        # does not record it publishes from the wrong checkout.
        run.worktree_id = worktree.id
        session.add(run)
        session.commit()
    return Path(worktree.worktree_path)
