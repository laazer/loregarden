"""The branch a Home Baxter thread's acting turns write to, for the Changes card.

An acting turn runs in the thread's own worktree (``Worktree.chat_session_id``),
and publishing that work happens after the reply (``baxter_chat_run_service``).
The card names the branch and checkout so the operator can find the work
without reading back through the thread for the publish note.
"""

from __future__ import annotations

from loregarden.models.domain import BaxterChatSession, Worktree, WorktreeState
from sqlmodel import Session, col, select


def chat_branch_view(session: Session, chat_session: BaxterChatSession) -> dict | None:
    """The thread's active worktree, or None while no turn has acted."""
    worktree = session.exec(
        select(Worktree)
        .where(Worktree.chat_session_id == chat_session.id)
        .where(Worktree.state == WorktreeState.ACTIVE)
        .order_by(col(Worktree.created_at).desc())
    ).first()
    if worktree is None:
        return None
    return {
        "branch": worktree.branch,
        "parent_branch": worktree.parent_branch,
        "worktree_path": worktree.worktree_path,
        "has_conflicts": worktree.has_conflicts,
    }
