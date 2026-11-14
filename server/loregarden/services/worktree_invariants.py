"""Invariants over worktree rows and the checkouts they name.

Split out of `invariant_registry`, which re-exports them and lists them in
`INVARIANTS`; each row family's detector and repair live in its own module.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import InvariantReentry
from loregarden.services.checkouts_without_their_branch import checkouts_without_their_branch
from loregarden.services.cleaned_worktree_rows import cleaned_rows_with_their_checkout
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.orphaned_worktree_rows import (
    rows_without_their_checkout,
    settle_rows_without_their_checkout,
)
from loregarden.services.stale_parked_at_rows import (
    clear_vanished_parked_at,
    rows_whose_parked_at_is_gone,
)
from sqlmodel import Session


def _rows_without_their_checkout(session: Session) -> list[Violation]:
    """`entered_at` is None: nothing stamps the moment a checkout left.

    `created_at` is when the row was cut, which is not when the directory went
    — and a `DEFECT`-style "entered after the guard landed" reading off it
    would call every pre-existing row a re-entry. The policy here is
    `EXPECTED` anyway, so the state is repaired rather than dated.

    The finding names the path, because that is what a person goes and looks
    at — and the row and its ticket too, because a row recording no path at
    all is also a violation, and "go and look at ''" is not something anyone
    can act on.
    """
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=None,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) is active and records a "
            f"checkout at '{row.worktree_path}', which is not there",
        )
        for row in rows_without_their_checkout(session)
    ]


def _settle_rows_without_their_checkout(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: settling a row is a write, and the
    # reading that justified it must be this pass's.
    wanted = {v.subject_id for v in violations}
    return settle_rows_without_their_checkout(
        session, [row for row in rows_without_their_checkout(session) if row.id in wanted]
    )


ACTIVE_WORKTREE_ROWS_HAVE_THEIR_CHECKOUT = Invariant(
    name="active_worktree_rows_have_their_checkout",
    ticket="lg-external-harness-1452",
    statement="no active worktrees row records a checkout that is not on disk",
    detect=_rows_without_their_checkout,
    repair=_settle_rows_without_their_checkout,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 23),
    expected_because=(
        "an operator removes a checkout by hand, outside the control plane — `git worktree "
        "remove` in a terminal leaves the row with nothing to speak for, and no writer can "
        "guard against it"
    ),
)


def _rows_whose_parked_at_is_gone(session: Session) -> list[Violation]:
    """`entered_at` is None: nothing stamps the moment a park copy left.

    The finding names both paths, because that is what a person goes and looks
    at — and the row and its ticket too, because a vanished locator with no
    ticket attached is still a false record.
    """
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=None,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) records parked_at "
            f"'{row.parked_at}', which is gone, while the checkout at "
            f"'{row.worktree_path}' is present",
        )
        for row in rows_whose_parked_at_is_gone(session)
    ]


def _clear_vanished_parked_at(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: clearing parked_at is a write, and
    # the reading that justified it must be this pass's. A PRESENT parked_at
    # at repair time is skipped — that locator is live.
    wanted = {v.subject_id for v in violations}
    return clear_vanished_parked_at(
        session, [row for row in rows_whose_parked_at_is_gone(session) if row.id in wanted]
    )


WORKTREE_PARKED_AT_NAMES_A_LIVE_PATH = Invariant(
    name="worktree_parked_at_names_a_live_path",
    ticket="lg-external-harness-1416",
    statement="no worktrees row records a parked_at that is gone while its checkout is present",
    detect=_rows_whose_parked_at_is_gone,
    repair=_clear_vanished_parked_at,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 25),
    expected_because=(
        "directory_release._restore's ERROR record asks a person to move it back by hand "
        "before the next sweep; doing that leaves parked_at naming a path that is gone "
        "while worktree_path is present"
    ),
    prevented_by=(
        "server/tests/test_worktree_lifecycle.py"
        "::test_every_writer_of_parked_at_sets_it_only_from_an_unrestored_release"
    ),
)


def _cleaned_rows_with_their_checkout(session: Session) -> list[Violation]:
    """`entered_at` is the row's `cleaned_at`: the moment a writer said it was gone."""
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=row.cleaned_at,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) is cleanup and records a "
            f"checkout at '{row.worktree_path}', which is still there and owned by no row",
        )
        for row in cleaned_rows_with_their_checkout(session)
    ]


CLEANED_WORKTREE_ROWS_LEAVE_NO_CHECKOUT = Invariant(
    name="cleaned_worktree_rows_leave_no_checkout",
    ticket="lg-external-harness-954",
    statement="no cleanup worktrees row records a checkout that is still on disk",
    detect=_cleaned_rows_with_their_checkout,
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 16),
    prevented_by=(
        "server/tests/test_remove_checkout_refusal.py"
        "::test_a_removal_that_raises_answers_remove_raised"
    ),
    unrepaired_action=(
        "Open the directory the finding names. If it holds work, commit or move it; then remove "
        "it (`git worktree remove --force <path>`, or delete the directory). Nothing owns it, so "
        "no sweep will, and the finding clears once it is gone."
    ),
)


def _checkouts_without_their_branch(session: Session) -> list[Violation]:
    """`entered_at` is None: nothing stamps the moment a branch was deleted."""
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=None,
            detail=f"worktree row {row.id} (ticket {row.ticket_id}) has a checkout at "
            f"'{row.worktree_path}' but its branch '{row.branch}' no longer exists",
        )
        for row in checkouts_without_their_branch(session)
    ]


CHECKOUTS_KEEP_THEIR_BRANCH = Invariant(
    name="checkouts_keep_their_branch",
    ticket="lg-external-harness-1166",
    statement="no worktrees row has a checkout on disk whose recorded branch is gone",
    detect=_checkouts_without_their_branch,
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 16),
    prevented_by=(
        "server/tests/test_stage_fanout_service.py"
        "::test_a_losing_branch_survives_a_failed_worktree_removal"
    ),
    unrepaired_action=(
        "Open the checkout the finding names. If its HEAD holds commits you want, recreate the "
        "branch there (`git branch <name> HEAD`); otherwise remove the checkout. Nothing else "
        "references those commits, so the next `git gc` may take them."
    ),
)
