"""Every state invariant the reconciler enforces, and the adapters that feed it.

Adding a fix for a bad state? Register it here (`services.invariants` says
why): a detector, a repair, and either the test that proves the guard
(`DEFECT`) or the reason the state is entered legitimately (`EXPECTED`).
`test_invariants.py` fails a registration missing any of them, and the reconcile
pass picks it up from `INVARIANTS` by name (`reconciliation.PERIODIC_STEPS`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import InvariantReentry, Ticket
from loregarden.services.block_settlement import settle_unclassified_blocks, unclassified_blocks
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.squash_twin_sweep import record_twins, twinned_branches
from loregarden.services.terminal_stage_sweep import (
    finish_cleared_landings,
    tickets_whose_landing_cleared,
)
from loregarden.services.tree_publish_scheduler import (
    close_held_cards,
    published_cards_held,
)
from sqlmodel import Session


def _tickets(session: Session, violations: Sequence[Violation]) -> list[Ticket]:
    tickets = (session.get(Ticket, v.subject_id) for v in violations)
    return [ticket for ticket in tickets if ticket is not None]


def _unsettled_blocks(session: Session) -> list[Violation]:
    """`entered_at` is the ticket's `updated_at`: a block has no timestamp of its own."""
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=ticket.updated_at,
            detail=f"{ticket.external_id} on '{ticket.workflow_stage_key}': "
            f"{ticket.blocking_issues}",
        )
        for ticket in unclassified_blocks(session)
    ]


def _settle_blocks(session: Session, violations: Sequence[Violation]) -> int:
    return settle_unclassified_blocks(session, _tickets(session, violations))


def _cleared_landings(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=ticket.id,
            ticket_id=ticket.id,
            entered_at=None,
            detail=f"{ticket.external_id}'s landing would now succeed",
        )
        for ticket in tickets_whose_landing_cleared(session)
    ]


def _finish_landings(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: finishing is a write to git, and the
    # read that justified it must be this pass's.
    wanted = {v.subject_id for v in violations}
    tickets = [t for t in tickets_whose_landing_cleared(session) if t.id in wanted]
    return len(finish_cleared_landings(session, tickets))


def _held_cards(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.card.id,
            ticket_id=item.root.id,
            entered_at=None,
            detail=f"{item.root.external_id}: {item.held}",
        )
        for item in published_cards_held(session)
    ]


def _close_cards(session: Session, violations: Sequence[Violation]) -> int:
    wanted = {v.subject_id for v in violations}
    return close_held_cards(
        session, [item for item in published_cards_held(session) if item.card.id in wanted]
    )


EVERY_BLOCK_IS_SETTLED = Invariant(
    name="every_block_is_settled",
    ticket="lg-workflow-integrity-802",
    statement="every blocked ticket has a block kind and a recorded repair decision",
    detect=_unsettled_blocks,
    repair=_settle_blocks,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 9, 24),
    prevented_by=(
        "server/tests/test_block_settlement.py"
        "::test_every_writer_claiming_to_settle_actually_calls_the_helper"
    ),
)

LANDING_BLOCKS_CLEAR = Invariant(
    name="landing_blocks_clear_with_their_cause",
    ticket="lg-milestone-that-928",
    statement="no ticket stays blocked by a landing that would now succeed",
    detect=_cleared_landings,
    repair=_finish_landings,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 20),
    expected_because=(
        "a landing conflict is cleared outside the control plane: resolved and merged by "
        "hand, or the target branch moved under it"
    ),
)

PUBLISH_CARDS_CLOSE = Invariant(
    name="publish_cards_close_once_landed",
    ticket="lg-milestone-that-1032",
    statement="no publish card stays pending once its base holds the tree's work",
    detect=_held_cards,
    repair=_close_cards,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 10, 20),
    expected_because="a published PR is merged on GitHub by a person, not by the control plane",
)


def _twinned(session: Session) -> list[Violation]:
    return [
        Violation(
            subject_id=item.ticket.id,
            ticket_id=item.ticket.id,
            entered_at=None,
            detail=f"{item.branch} holds {item.twin.on_branch[:12]}, a copy of "
            f"{item.twin.on_target[:12]} on {item.target} with none of its history",
        )
        for item in twinned_branches(session)
    ]


def _record_twins(session: Session, violations: Sequence[Violation]) -> int:
    # Re-detect rather than trust the list: recording moves a ref, and the read
    # that justified it must be this pass's.
    wanted = {v.subject_id for v in violations}
    return record_twins(
        session, [item for item in twinned_branches(session) if item.ticket.id in wanted]
    )


TICKET_BRANCHES_HOLD_HISTORY = Invariant(
    name="ticket_branches_hold_their_targets_history",
    ticket="lg-external-harness-1318",
    statement=(
        "no open ticket's branch holds a squash of its integration branch in place of "
        "the branch's history"
    ),
    detect=_twinned,
    repair=_record_twins,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 20),
    prevented_by=(
        "server/tests/test_publish_tree.py"
        "::test_the_root_pushes_its_integration_branch_and_records_the_tip"
    ),
)

#: Ordered as `reconciliation.PERIODIC_STEPS` schedules them.
INVARIANTS: tuple[Invariant, ...] = (
    EVERY_BLOCK_IS_SETTLED,
    LANDING_BLOCKS_CLEAR,
    PUBLISH_CARDS_CLOSE,
    TICKET_BRANCHES_HOLD_HISTORY,
)
