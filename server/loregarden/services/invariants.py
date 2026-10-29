"""A state invariant: one detector, used to repair, to watch, and to test.

A fix for a bad state has four jobs, and the repo used to do them in four
unconnected places, when it did them at all:

1. **Repair what is already there.** Rows that entered the state before the fix.
2. **Catch re-entry.** Rows that enter it anyway, after the fix.
3. **Prevent entry.** The guard at the write site, and a test proving it.
4. **Learn.** Why the state was reachable, written where the next agent reads.

The reconciler's sweeps did (1) and (2) together and could not tell them apart.
`sweep_unclassified_blocks` existed because twenty-two writers skipped
`settle_block`; once 802 routed every writer through it, each block the sweep
still found was a writer 802 missed, yet the sweep repaired it and returned a
count nobody read. A guard failing looked exactly like a guard working.

So an `Invariant` names its detector once and the three consumers share it:

- the reconcile pass calls `run_invariant`, which repairs every violation and,
  for a `DEFECT` invariant, reports each one that entered *after* the guard
  landed (`since`) as an `INVARIANT_REENTERED` finding, escalated to a bug at
  the first ticket (`monitor_escalation.escalate_invariant_reentries`);
- `test_invariants.py` runs every registered detector against the
  production-shaped scenario and requires repair to converge;
- the same test requires every `DEFECT` invariant to name the test that
  proves its guard (`prevented_by`), and that test must exist.

(4) lives in the fix coverage record (`services.fix_coverage`), which writes the
learning when it is recorded; the PR's "Fix coverage" section names the
invariant this registry holds.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from loregarden.models.domain import InvariantReentry, MonitorCondition
from loregarden.models.domain.workflow_monitor import MonitorFinding
from loregarden.services.monitor_escalation import escalate_invariant_reentries
from loregarden.services.workflow_monitor import record_findings
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Detail is evidence for a person; a stack of it is noise.
_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class Violation:
    """One row in the bad state.

    `entered_at` is when the row reached it, as far as the detector can tell. It
    decides backlog from re-entry, so a detector that only has a row's
    `updated_at` says so in its docstring: a backlog row edited after `since`
    will read as a re-entry, which errs towards reporting.
    """

    subject_id: str
    ticket_id: str | None
    entered_at: datetime | None
    detail: str


@dataclass(frozen=True)
class Invariant:
    """A state that should not exist, how to find it, and how to undo it."""

    #: Stable identifier; the finding's stage key and the PR section name it.
    name: str
    #: The ticket whose fix this is, by external id.
    ticket: str
    #: What must be true, as a sentence: "every blocked ticket has a block kind".
    statement: str
    detect: Callable[[Session], Sequence[Violation]]
    #: Undo the violations; returns how many it repaired. Repair re-checks each
    #: subject itself: the world may have moved between detect and repair.
    #: None for a state only a person can resolve (`unrepaired_action` says how):
    #: every violation is then reported on every pass, backlog included, since
    #: nothing else will ever clear it.
    repair: Callable[[Session, Sequence[Violation]], int] | None
    reentry: InvariantReentry
    #: When the guard landed (DEFECT) or the invariant was registered (EXPECTED).
    #: A violation that entered before this is backlog, not re-entry.
    since: date
    #: DEFECT only: `path/to/test_file.py::test_name` proving the state cannot be
    #: entered through the guarded writers.
    prevented_by: str = ""
    #: EXPECTED only: why the state is entered legitimately.
    expected_because: str = ""
    #: Report-only (no `repair`): what a person does to resolve one violation.
    unrepaired_action: str = ""

    def __post_init__(self) -> None:
        if self.reentry is InvariantReentry.DEFECT and not self.prevented_by:
            raise ValueError(f"DEFECT invariant {self.name!r} names no prevented_by test")
        if self.reentry is InvariantReentry.EXPECTED and not self.expected_because:
            raise ValueError(f"EXPECTED invariant {self.name!r} gives no expected_because")
        if self.repair is None and not self.unrepaired_action:
            raise ValueError(
                f"report-only invariant {self.name!r} says no unrepaired_action for a person"
            )

    @property
    def report_only(self) -> bool:
        return self.repair is None


@dataclass(frozen=True)
class InvariantCheck:
    """What one pass found and did."""

    name: str
    found: int
    reentered: int
    repaired: int
    #: Report-only: violations from before `since`, left for a person.
    unrepaired: int = 0


def _since(invariant: Invariant) -> datetime:
    return datetime(
        invariant.since.year, invariant.since.month, invariant.since.day, tzinfo=timezone.utc
    )


def _entered_after(violation: Violation, since: datetime) -> bool:
    """Unknown entry time counts as re-entry: a detector that cannot date a row
    must not be able to hide it."""
    entered = violation.entered_at
    if entered is None:
        return True
    if entered.tzinfo is None:  # SQLite hands back naive UTC
        entered = entered.replace(tzinfo=timezone.utc)
    return entered >= since


def reentries(invariant: Invariant, violations: Sequence[Violation]) -> list[Violation]:
    """The violations that falsify a DEFECT invariant's guard; [] for EXPECTED."""
    if invariant.reentry is not InvariantReentry.DEFECT:
        return []
    since = _since(invariant)
    return [v for v in violations if _entered_after(v, since)]


def _summary(invariant: Invariant, condition: MonitorCondition) -> str:
    if condition is MonitorCondition.INVARIANT_REENTERED:
        return (
            f"'{invariant.statement}' was violated again after its guard landed "
            f"({invariant.ticket}, {invariant.since.isoformat()}); "
            f"{invariant.prevented_by} does not cover the writer that did this."
        )
    return (
        f"'{invariant.statement}' is violated, and nothing repairs it automatically "
        f"({invariant.ticket}). {invariant.unrepaired_action}"
    )


def _finding(
    invariant: Invariant, violation: Violation, condition: MonitorCondition
) -> MonitorFinding:
    return MonitorFinding(
        condition=condition,
        ticket_id=violation.ticket_id or "",
        stage_key=invariant.name,
        summary=_summary(invariant, condition),
        evidence={
            "invariant": invariant.name,
            "subject_id": violation.subject_id,
            "entered_at": violation.entered_at.isoformat() if violation.entered_at else "unknown",
            "detail": violation.detail[:_DETAIL_LIMIT],
        },
    )


def _report(
    session: Session, invariant: Invariant, violations: list[Violation], condition: MonitorCondition
) -> None:
    """Record violations as findings (before any repair erases them), then escalate."""
    ticketless = [v for v in violations if not v.ticket_id]
    if ticketless:
        # A finding needs a ticket to hang on (artifacts.ticket_id is NOT NULL),
        # so these are reported here instead, at a level the handler shows.
        logger.warning(
            "Invariant %s (%s) on %d row(s) with no ticket: %s",
            invariant.name,
            condition.value,
            len(ticketless),
            ", ".join(v.subject_id for v in ticketless),
        )
    findings = [_finding(invariant, v, condition) for v in violations if v.ticket_id]
    if not findings:
        return
    record_findings(session, findings)
    escalate_invariant_reentries(session, invariant_name=invariant.name, condition=condition)
    logger.warning(
        "Invariant %s: %s on %d ticket(s): %s",
        invariant.name,
        condition.value,
        len(findings),
        _summary(invariant, condition),
    )


def run_invariant(session: Session, invariant: Invariant) -> InvariantCheck:
    """Detect, report, repair. The reconcile pass's one call per invariant."""
    violations = list(invariant.detect(session))
    if not violations:
        return InvariantCheck(invariant.name, found=0, reentered=0, repaired=0)
    reentered = reentries(invariant, violations)
    _report(session, invariant, reentered, MonitorCondition.INVARIANT_REENTERED)
    if invariant.repair is None:
        reentered_ids = {v.subject_id for v in reentered}
        backlog = [v for v in violations if v.subject_id not in reentered_ids]
        _report(session, invariant, backlog, MonitorCondition.INVARIANT_UNREPAIRED)
        return InvariantCheck(
            invariant.name,
            found=len(violations),
            reentered=len(reentered),
            repaired=0,
            unrepaired=len(backlog),
        )
    repaired = invariant.repair(session, violations)
    if repaired < len(violations):
        logger.warning(
            "Invariant %s: repaired %d of %d violation(s); the rest stay for the next pass",
            invariant.name,
            repaired,
            len(violations),
        )
    return InvariantCheck(
        invariant.name, found=len(violations), reentered=len(reentered), repaired=repaired
    )
