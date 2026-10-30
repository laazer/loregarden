"""`worktrees` rows whose `parked_at` names a path that is gone.

The re-entry half of lg-external-harness-1416. `directory_release._restore`
put the tree back at `worktree_path` and left `parked_at` pointing at a park
copy that is no longer on disk; the row still names the vanished locator.
Closing the live-path writer is the prevention census; this is what catches
the state anyway.

Its policy is `EXPECTED`, not `DEFECT`: a person moving the tree back by hand
before the next sweep is a legitimate way in (`directory_release._restore`'s
ERROR record asks for exactly that), and `DEFECT` would escalate every one of
those to a bug at the first ticket.

The checkout is PRESENT and still owned. Repair clears the false locator and
rewrites `unresolved_reason`; it does not settle the row.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from loregarden.models.domain import Worktree
from loregarden.services.directory_release import DirectoryState, directory_state
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: AC14.4. Own sentence: not AC7 RESTORED, not AC13 INTACT. Dash is U+2014.
#: Interpolate `worktree.worktree_path` only. Contains the word `parked`.
VANISHED_PARKED_AT_REASON = (
    "The directory is at {worktree_path} and is still owned; its parked copy is gone "
    "\u2014 delete it again, or remove the directory by hand."
)


def rows_whose_parked_at_is_gone(session: Session) -> list[Worktree]:
    """Every row whose parked locator is gone while the checkout is there.

    "Absent" is `directory_state`'s answer and not `os.path.exists`: a file
    where a park directory should be is as false a locator as an empty path,
    and an `exists()` test would call it healthy and leave the stale name.
    An errno the kernel will not explain is NOT an absence — it raises, and is
    kept rather than cleared, because "I will not tell you" is one step short
    of wiping the only record of a live park copy.

    Empty `worktree_path` is skipped before any probe: `os.stat("")` can read
    as cwd, and empty is not PRESENT. That state belongs to
    `active_worktree_rows_have_their_checkout`.
    """
    rows = session.exec(
        select(Worktree).where(Worktree.parked_at != "").order_by(Worktree.id)
    ).all()
    gone: list[Worktree] = []
    for row in rows:
        if not row.worktree_path:
            continue
        try:
            parked = directory_state(row.parked_at)
            checkout = directory_state(row.worktree_path)
        except OSError:
            logger.warning(
                "Worktree %s (ticket %s): the filesystem would not say whether "
                "parked_at %s or checkout %s is there",
                row.id,
                row.ticket_id,
                row.parked_at,
                row.worktree_path,
                exc_info=True,
            )
            continue
        if parked is DirectoryState.ABSENT and checkout is DirectoryState.PRESENT:
            gone.append(row)
    return gone


def clear_vanished_parked_at(session: Session, rows: Sequence[Worktree]) -> int:
    """Clear each vanished locator. Returns how many rows were written.

    Two column writes in one `session.add` + `session.commit`, matching
    `_record_refusal`: a crash must not leave `parked_at` empty while
    `unresolved_reason` still describes the old locator. Does not change
    `Worktree.state`. The checkout is PRESENT and still owned.
    """
    written = 0
    for worktree in rows:
        worktree.parked_at = ""
        worktree.unresolved_reason = VANISHED_PARKED_AT_REASON.format(
            worktree_path=worktree.worktree_path
        )
        session.add(worktree)
        session.commit()
        written += 1
    return written
