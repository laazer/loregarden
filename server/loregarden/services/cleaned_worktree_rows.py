"""CLEANUP `worktrees` rows whose recorded checkout is still on disk.

The re-entry half of lg-external-harness-954. A removal that was attempted and
failed used to read as a removal, so the row was settled (or the chat that
owned it dropped) while the directory stayed, owned by nothing and reaped by no
sweeper. Every writer of CLEANUP now stamps it only once the checkout is gone
(`WorktreeService._mark_cleaned` after the release, `_retire_missing` for one
already vanished, `disk_usage.cleanup._forget_worktree` after the move to the
trash), so a CLEANUP row with a directory at its path is a writer failing.

Report-only: whether that directory holds work is a person's question, and
deleting it from a timer is the act this ticket exists to stop guessing at.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import Worktree, WorktreeState
from loregarden.services.directory_release import DirectoryState, directory_state
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def cleaned_rows_with_their_checkout(session: Session) -> list[Worktree]:
    """Every CLEANUP row whose recorded path is still a directory, in id order.

    A path an ACTIVE row records is skipped: a later checkout cut at the same
    spelling owns that directory, and the old row's CLEANUP was true when it
    was written. An errno the kernel will not explain raises inside
    `directory_state`; it is logged and the row skipped, because "I will not
    tell you" is not evidence of a directory any more than of its absence.
    """
    rows = session.exec(
        select(Worktree).where(Worktree.state == WorktreeState.CLEANUP).order_by(Worktree.id)
    ).all()
    owned = {
        Path(path).resolve()
        for path in session.exec(
            select(Worktree.worktree_path).where(Worktree.state == WorktreeState.ACTIVE)
        ).all()
        if path
    }
    present: list[Worktree] = []
    for row in rows:
        if not row.worktree_path or Path(row.worktree_path).resolve() in owned:
            continue
        try:
            if directory_state(row.worktree_path) is not DirectoryState.PRESENT:
                continue
        except OSError:
            logger.warning(
                "Worktree %s at %s: the filesystem would not say whether it is there",
                row.id,
                row.worktree_path,
                exc_info=True,
            )
            continue
        present.append(row)
    return present
