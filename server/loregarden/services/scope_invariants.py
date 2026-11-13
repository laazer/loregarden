"""Invariants that hold a ticket to its scope: findings decided, criteria answered for.

Kept out of `invariant_registry`, which sits at its line cap; it imports these
and schedules them in `INVARIANTS` like the rest.
"""

from __future__ import annotations

from datetime import date

from loregarden.models.domain import InvariantReentry
from loregarden.services.invariants import Invariant
from loregarden.services.orphaned_findings import (
    defer_findings_whose_source_finished,
    findings_whose_source_finished,
)
from loregarden.services.undecided_finding_runs import undecided_findings_that_ran
from loregarden.services.unsettled_criteria_runs import done_tickets_with_unsettled_criteria

UNDECIDED_FINDINGS_DO_NOT_RUN = Invariant(
    name="undecided_findings_do_not_run",
    ticket="lg-milestone-that-1216",
    statement="no open ticket a run filed is driven before triage promotes it",
    detect=undecided_findings_that_ran,
    # Report-only: the run already happened; promote or close is a person's call.
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 7),
    prevented_by="server/tests/test_discovered_work.py::test_no_dispatcher_can_start_an_undecided_finding",
    unrepaired_action="Decide it: loregarden_decide_finding, from its source ticket's spec or as an operator",
)


DONE_TICKETS_HAVE_SETTLED_CRITERIA = Invariant(
    name="done_tickets_have_settled_criteria",
    ticket="lg-milestone-that-1216",
    statement="no ticket finishes with an acceptance criterion neither met nor delegated",
    detect=done_tickets_with_unsettled_criteria,
    # Report-only: the ticket is done; whether to reopen it or hand the
    # criterion on is a person's call, and inventing a settlement would be a lie.
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 7),
    prevented_by="server/tests/test_criterion_gate.py::test_the_gate_is_not_entered_with_a_criterion_unanswered",
    unrepaired_action=(
        "Reopen it to verify, or settle the criterion with loregarden_settle_criterion "
        "(met with its tests, or delegated to the ticket that carries it)"
    ),
)


FINDINGS_OUTLIVE_THEIR_SOURCE = Invariant(
    name="findings_do_not_outlive_the_source_that_decides_them",
    ticket="lg-milestone-that-2606",
    statement="no open finding waits for a scope decision its source can no longer be asked for",
    detect=findings_whose_source_finished,
    # Repairable, and only one way: `defer`, which is what `scope_round` already
    # does for a round that decides nothing. Leaving it for a person is what the
    # bad state already was — the ticket is refused by every dispatcher until
    # somebody notices, and nothing surfaces it.
    repair=defer_findings_whose_source_finished,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 12),
    expected_because=(
        "a source ticket legitimately finishes without re-entering a gate: its parent's "
        "rollup settles it, an operator closes it, or a sweep does. `round_due` fires on "
        "stage entry, so nothing can force the round once there are no stages left to enter"
    ),
)
