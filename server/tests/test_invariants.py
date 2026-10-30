"""The invariant registry: every fix for a bad state repairs, watches, and proves.

Three groups. The registry's contract, which is what makes a registration more
than a comment: scheduled, named, and pointing at a prevention test that exists.
The runner's split of backlog from re-entry, which is the whole reason the
registry exists. And convergence on the production-shaped scenario: one pass
leaves nothing for a second to find.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest
from loregarden.models.domain import (
    Artifact,
    BlockKind,
    InvariantReentry,
    MonitorArtifactKind,
    MonitorCondition,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.invariant_registry import EVERY_BLOCK_IS_SETTLED, INVARIANTS
from loregarden.services.invariants import Invariant, Violation, run_invariant
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.testing.factories import make_ticket
from loregarden.testing.prod_shape import build_prod_shape
from sqlmodel import Session, col, select

REPO_ROOT = Path(__file__).resolve().parents[2]
#: A ticket named by external id, never by a bare number (`ticket_ids.resolve_one`).
EXTERNAL_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)+-\d+$")


# ---- the registry's contract ----------------------------------------------


def test_every_invariant_is_scheduled_exactly_once():
    scheduled = [step.name for step in PERIODIC_STEPS]
    for invariant in INVARIANTS:
        assert scheduled.count(invariant.name) == 1, (
            f"{invariant.name} is registered but scheduled {scheduled.count(invariant.name)} "
            "times; add `_invariant(...)` to reconciliation.PERIODIC_STEPS"
        )


def test_invariant_names_are_unique():
    names = [invariant.name for invariant in INVARIANTS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda i: i.name)
def test_every_invariant_names_its_ticket_by_external_id(invariant: Invariant):
    assert EXTERNAL_ID.match(invariant.ticket), invariant.ticket


def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)  # py-org: allow-isinstance
        and node.name.startswith("test_")
    }


@pytest.mark.parametrize(
    "invariant",
    [i for i in INVARIANTS if i.prevented_by],
    ids=lambda i: i.name,
)
def test_every_defect_invariant_points_at_a_prevention_test_that_exists(invariant: Invariant):
    """`prevented_by` is the claim a re-entry falsifies, so it must name a real test.

    DEFECT requires the field; EXPECTED may set it. Parametrize on
    `bool(invariant.prevented_by)` so an EXPECTED pointer cannot rot either.
    `__post_init__` is unchanged: EXPECTED still does not require the field.
    The test itself runs with the suite; this only proves the pointer is not stale,
    which a rename would otherwise make silently.
    """
    path, _, name = invariant.prevented_by.partition("::")
    file = REPO_ROOT / path
    assert file.is_file(), f"{invariant.name}: {path} does not exist"
    assert name in _test_functions(file), f"{invariant.name}: {path} has no {name}"


def test_a_defect_invariant_with_no_prevention_test_is_refused():
    with pytest.raises(ValueError, match="prevented_by"):
        _invariant(InvariantReentry.DEFECT, prevented_by="")


def test_an_expected_invariant_with_no_reason_is_refused():
    with pytest.raises(ValueError, match="expected_because"):
        _invariant(InvariantReentry.EXPECTED, expected_because="")


def test_a_report_only_invariant_must_say_what_a_person_does():
    probe = _invariant(InvariantReentry.DEFECT)
    with pytest.raises(ValueError, match="unrepaired_action"):
        Invariant(**{**probe.__dict__, "repair": None})


def test_an_expected_invariant_may_omit_prevented_by():
    """AC14.6. Do not change Invariant.__post_init__: EXPECTED still does not
    require the field. A pointer that is set is existence-checked; one that
    is not set is still legal.
    """
    invariant = Invariant(
        name="probe_invariant",
        ticket="lg-probe-1",
        statement="probe rows do not exist",
        detect=lambda _session: [],
        repair=lambda _session, _violations: 0,
        reentry=InvariantReentry.EXPECTED,
        since=date(2026, 9, 1),
        prevented_by="",
        expected_because="the world moves",
    )
    assert invariant.prevented_by == ""


# ---- backlog versus re-entry ------------------------------------------------

SINCE = date(2026, 9, 1)


class _Rows:
    """A detector over a mutable list, and a repair that empties it."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        self.repaired: list[str] = []

    def detect(self, _session: Session) -> list[Violation]:
        return list(self.violations)

    def repair(self, _session: Session, violations: Sequence[Violation]) -> int:
        self.repaired.extend(v.subject_id for v in violations)
        self.violations = []
        return len(violations)


def _invariant(
    reentry: InvariantReentry,
    *,
    rows: _Rows | None = None,
    prevented_by: str = "server/tests/test_invariants.py::test_invariant_names_are_unique",
    expected_because: str = "the world moves",
) -> Invariant:
    rows = rows or _Rows([])
    return Invariant(
        name="probe_invariant",
        ticket="lg-probe-1",
        statement="probe rows do not exist",
        detect=rows.detect,
        repair=rows.repair,
        reentry=reentry,
        since=SINCE,
        prevented_by=prevented_by if reentry is InvariantReentry.DEFECT else "",
        expected_because=expected_because if reentry is InvariantReentry.EXPECTED else "",
    )


def _violation(ticket: Ticket, *, days_after_since: int | None) -> Violation:
    entered = (
        None
        if days_after_since is None
        else utcnow().replace(year=SINCE.year, month=SINCE.month, day=SINCE.day)
        + timedelta(days=days_after_since)
    )
    return Violation(subject_id=ticket.id, ticket_id=ticket.id, entered_at=entered, detail="probe")


def _findings(session: Session) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                col(Artifact.kind) == MonitorArtifactKind.FINDING.value,
                col(Artifact.title).startswith(MonitorCondition.INVARIANT_REENTERED.value),
            )
        ).all()
    )


def _escalations(session: Session) -> list[Ticket]:
    return list(
        session.exec(
            select(Ticket).where(
                col(Ticket.work_item_type) == WorkItemType.BUG,
                col(Ticket.title).contains("probe_invariant"),
            )
        ).all()
    )


@pytest.fixture(name="probe_tickets")
def probe_tickets_fixture(db_session: Session) -> tuple[Ticket, Ticket]:
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    return (
        make_ticket(db_session, workspace_id=workspace.id, external_id="lg-probe-old-1"),
        make_ticket(db_session, workspace_id=workspace.id, external_id="lg-probe-new-2"),
    )


def test_backlog_is_repaired_without_a_finding(db_session: Session, probe_tickets):
    old, _ = probe_tickets
    rows = _Rows([_violation(old, days_after_since=-10)])

    check = run_invariant(db_session, _invariant(InvariantReentry.DEFECT, rows=rows))

    assert (check.found, check.reentered, check.repaired) == (1, 0, 1)
    assert rows.repaired == [old.id]
    assert _findings(db_session) == []
    assert _escalations(db_session) == []


def test_a_reentry_is_recorded_and_escalated_at_the_first_ticket(
    db_session: Session, probe_tickets
):
    old, new = probe_tickets
    rows = _Rows([_violation(old, days_after_since=-10), _violation(new, days_after_since=3)])

    check = run_invariant(db_session, _invariant(InvariantReentry.DEFECT, rows=rows))

    assert (check.found, check.reentered, check.repaired) == (2, 1, 2)
    findings = _findings(db_session)
    assert [f.ticket_id for f in findings] == [new.id]
    payload = json.loads(findings[0].content_json or "{}")
    assert payload["evidence"]["invariant"] == "probe_invariant"
    assert "test_invariant_names_are_unique" in payload["summary"]
    bugs = _escalations(db_session)
    assert len(bugs) == 1, "one re-entry falsifies the guard; it must not wait for a second"
    assert new.external_id in bugs[0].description


def test_a_violation_with_no_entry_time_counts_as_reentry(db_session: Session, probe_tickets):
    _, new = probe_tickets
    rows = _Rows([_violation(new, days_after_since=None)])

    check = run_invariant(db_session, _invariant(InvariantReentry.DEFECT, rows=rows))

    assert check.reentered == 1


def test_reentry_is_recorded_before_repair_can_fail(db_session: Session, probe_tickets):
    """The record of what was wrong must survive a repair that raises."""
    _, new = probe_tickets
    rows = _Rows([_violation(new, days_after_since=3)])

    def broken(_session: Session, _violations: Sequence[Violation]) -> int:
        raise RuntimeError("repair blew up")

    invariant = _invariant(InvariantReentry.DEFECT, rows=rows)
    invariant = Invariant(**{**invariant.__dict__, "repair": broken})

    with pytest.raises(RuntimeError):
        run_invariant(db_session, invariant)
    assert [f.ticket_id for f in _findings(db_session)] == [new.id]


def test_an_expected_invariant_repairs_and_reports_nothing(db_session: Session, probe_tickets):
    _, new = probe_tickets
    rows = _Rows([_violation(new, days_after_since=3)])

    check = run_invariant(db_session, _invariant(InvariantReentry.EXPECTED, rows=rows))

    assert (check.found, check.reentered, check.repaired) == (1, 0, 1)
    assert _findings(db_session) == []


def test_a_block_written_around_settle_block_is_reported_as_a_missed_writer(
    db_session: Session, probe_tickets
):
    """The real DEFECT invariant, end to end: a writer that set blocking text and
    skipped `settle_block` is found, reported against its ticket, and settled."""
    _, ticket = probe_tickets
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = "Orchestration lease expired for run orch_dead"
    ticket.block_kind = None
    db_session.add(ticket)
    db_session.commit()

    check = run_invariant(db_session, EVERY_BLOCK_IS_SETTLED)

    assert (check.found, check.reentered, check.repaired) == (1, 1, 1)
    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.HARNESS
    assert [f.ticket_id for f in _findings(db_session)] == [ticket.id]
    assert run_invariant(db_session, EVERY_BLOCK_IS_SETTLED).found == 0


# ---- convergence on production's shape ---------------------------------------


@pytest.fixture(name="prod_shape_session")
def prod_shape_session_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
    with Session(isolated_db) as session:
        yield session


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda i: i.name)
def test_one_pass_leaves_nothing_for_the_next(prod_shape_session: Session, invariant: Invariant):
    """A repair that does not converge would refire forever and, for a DEFECT
    invariant, file a bug on every pass. Run on production's proportions, not a
    fixture of one row, because that is where an unhandled shape lives."""
    first = run_invariant(prod_shape_session, invariant)
    if invariant.report_only:
        # Nothing to converge: a person resolves each one. What must hold is
        # that every violation was reported, none repaired behind their back.
        assert first.repaired == 0
        assert first.reentered + first.unrepaired == first.found, f"{invariant.name}: {first}"
        return
    assert first.repaired == first.found, f"{invariant.name}: {first}"
    assert list(invariant.detect(prod_shape_session)) == []
