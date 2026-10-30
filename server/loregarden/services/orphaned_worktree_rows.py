"""ACTIVE `worktrees` rows whose recorded checkout is not there any more.

The re-entry half of lg-external-harness-1452. The checkout self-repair
force-removed the directory and left the row ACTIVE, so the control plane went
on believing a checkout existed at a path that no longer held one; the fix
closes that writer, and this is what catches the state anyway.

Its policy is `EXPECTED`, not `DEFECT`: after the fix no writer in the tree
reaches it, but an operator running `git worktree remove` by hand is a
legitimate and un-guardable way in, and `DEFECT` would escalate every one of
those to a bug at the first ticket.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from loregarden.models.domain import Worktree, WorktreeState
from loregarden.services.directory_release import DirectoryState, directory_state
from loregarden.services.worktree_lifecycle import (
    parked_at_holds_live_directory,
    settle_row_whose_checkout_is_gone,
)
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


def rows_without_their_checkout(session: Session) -> list[Worktree]:
    """Every ACTIVE row whose recorded path holds no checkout, in id order.

    "Absent" is `directory_state`'s answer and not `os.path.exists`: a file
    where a checkout should be is as unusable as an empty path, and an
    `exists()` test would call it healthy and leave the row ACTIVE forever.
    An errno the kernel will not explain is NOT an absence — it raises, and is
    kept rather than settled, because "I will not tell you" is one step short
    of recording that somebody's checkout is gone.
    """
    rows = session.exec(
        select(Worktree).where(Worktree.state == WorktreeState.ACTIVE).order_by(Worktree.id)
    ).all()
    gone: list[Worktree] = []
    for row in rows:
        if row.worktree_path:
            try:
                if directory_state(row.worktree_path) is not DirectoryState.ABSENT:
                    continue
            except OSError:
                logger.warning(
                    "Worktree %s at %s: the filesystem would not say whether it is there",
                    row.id,
                    row.worktree_path,
                    exc_info=True,
                )
                continue
        if parked_at_holds_live_directory(row):
            continue
        gone.append(row)
    return gone


def settle_rows_without_their_checkout(session: Session, rows: Sequence[Worktree]) -> int:
    """Settle each row through `worktree_lifecycle`. Returns how many moved.

    Every row, not every path: nothing in the schema forbids two ACTIVE rows
    recording one directory, and settling "the row for this path" would leave
    the second one reported on every pass forever.
    """
    return sum(1 for row in rows if settle_row_whose_checkout_is_gone(session, row))
