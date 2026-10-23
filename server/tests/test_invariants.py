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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    AgentSlot,
    Artifact,
    BlockKind,
    EventType,
    InvariantReentry,
    MonitorArtifactKind,
    MonitorCondition,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    QueuedRun,
    QueuePosition,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.invariant_registry import (
    EVERY_BLOCK_IS_SETTLED,
    EVERY_BUSY_LANE_HAS_AN_OCCUPANT,
    INVARIANTS,
)
from loregarden.services.invariants import Invariant, Violation, run_invariant
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.parallel_queue import ParallelQueueService, claim_free_slot
from loregarden.services.queue_slot_claims import RESERVATION_GRACE
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
    [i for i in INVARIANTS if i.reentry is InvariantReentry.DEFECT],
    ids=lambda i: i.name,
)
def test_every_defect_invariant_points_at_a_prevention_test_that_exists(invariant: Invariant):
    """`prevented_by` is the claim a re-entry falsifies, so it must name a real test.

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


# ---- a lane busy with nothing (lg-milestone-that-775) -----------------------


def _slots(session: Session) -> list[AgentSlot]:
    ParallelQueueService(session).initialize_slots()
    return list(session.exec(select(AgentSlot).order_by(col(AgentSlot.slot_number))).all())


def _ghost(session: Session, ticket: Ticket, slot: AgentSlot, *, orch_id: str | None = None):
    """Slot 1's observed shape: busy, no occupant, claimed long enough ago that
    the reservation grace has passed, with a settled lane entry behind it."""
    slot.is_available = False
    slot.current_orchestration_run_id = None
    slot.current_run_id = None
    slot.assigned_at = utcnow() - RESERVATION_GRACE - timedelta(seconds=1)
    entry = QueuedRun(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        slot_number=slot.slot_number,
        orchestration_run_id=orch_id,
        status=QueuePosition.STARTED,
        promoted_at=utcnow() - timedelta(minutes=5),
    )
    session.add(slot)
    session.add(entry)
    session.commit()
    return entry


def test_the_lane_invariant_is_registered_as_this_ticket_declares_it(db_session: Session):
    """The registration is the contract `fix_coverage` and the PR check resolve
    against, so its four decided fields are pinned rather than left to drift."""
    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT in INVARIANTS
    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.name == "every_busy_lane_has_an_occupant"
    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.ticket == "lg-milestone-that-775"
    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.reentry is InvariantReentry.DEFECT
    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.since == date(2026, 9, 21)
    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.prevented_by == (
        "server/tests/test_queue_lanes.py"
        "::test_completion_releases_the_slot_the_entry_names_even_with_its_ids_cleared"
    )


def test_a_lane_busy_with_nothing_is_detected_reported_and_given_back(
    db_session: Session, probe_tickets
):
    """The real invariant end to end. Slot 1 sat `is_available=0` with both ids
    null for 23 minutes while the board drew it busy and nothing ran in it."""
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    _ghost(db_session, ticket, slot)

    check = run_invariant(db_session, EVERY_BUSY_LANE_HAS_AN_OCCUPANT)

    assert (check.found, check.repaired) == (1, 1)
    assert check.reentered == 1, "a claim stamped after the guard landed falsifies it"
    db_session.refresh(slot)
    assert slot.is_available is True
    assert [f.ticket_id for f in _findings(db_session)] == [ticket.id], (
        "the slot names no ticket, so the finding hangs on the last entry that "
        "ran in that lane; without one it is a log line nobody reads"
    )
    assert list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session)) == []


def test_a_slot_claimed_and_not_yet_bound_is_not_a_violation(db_session: Session):
    """The first legal way a slot holds no ids. `claim_free_slot` takes the row
    with both null so two requests cannot read it as free, and the caller fills
    them in on `bind`. Reclaiming inside that window admits two orchestrations
    into one lane — which is why the DEFECT policy is honest only with this
    exclusion in place."""
    _slots(db_session)
    claimed = claim_free_slot(db_session)
    assert claimed is not None
    assert claimed.is_available is False
    assert claimed.current_orchestration_run_id is None and claimed.current_run_id is None

    assert list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session)) == []


def test_a_slot_held_for_repair_is_not_a_violation(db_session: Session, probe_tickets):
    """The second legal way. A provisional block keeps its lane rather than
    handing it to the next ticket, and `reconcile_slots` recognises the hold by
    the occupant id — so the detector must not undercut it."""
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    orch = OrchestrationRun(
        run_code="orch_repair", ticket_id=ticket.id, workspace_id=ticket.workspace_id
    )
    db_session.add(orch)
    db_session.commit()
    slot.is_available = False
    slot.current_orchestration_run_id = orch.id
    slot.assigned_at = utcnow() - RESERVATION_GRACE - timedelta(seconds=1)
    db_session.add(slot)
    db_session.add(
        QueuedRun(
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            slot_number=slot.slot_number,
            orchestration_run_id=orch.id,
            status=QueuePosition.REPAIRING,
            promoted_at=utcnow() - timedelta(minutes=5),
        )
    )
    db_session.commit()

    assert list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session)) == []
    db_session.refresh(slot)
    assert slot.is_available is False, "the hold survives a detection pass"


def test_the_repair_gives_back_only_the_lanes_this_pass_detected(
    db_session: Session, probe_tickets
):
    """The repair is the ghost reclaim narrowed to its subjects, not a call to
    the whole `reconcile_slots` sweep. A sweep would also reclaim rows this pass
    never detected and never reported, and the count would say it repaired one."""
    old, new = probe_tickets
    first, second = _slots(db_session)[:2]
    _ghost(db_session, old, first)
    _ghost(db_session, new, second)

    violations = list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session))
    mine = [v for v in violations if v.subject_id == first.id]
    assert len(violations) == 2 and len(mine) == 1

    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.repair(db_session, mine) == 1

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.is_available is True
    assert second.is_available is False, "repaired a lane this pass never detected"


def test_the_repair_rechecks_each_subject_before_it_writes(db_session: Session, probe_tickets):
    """Detect and repair are two reads of a world that moves between them: the
    lane this pass found empty may have been claimed since. Writing from the
    stale read is how a release hands out a lane that is already taken."""
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    _ghost(db_session, ticket, slot)
    violations = list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session))
    assert len(violations) == 1

    # A reservation took the lane between the two reads.
    slot.assigned_at = utcnow()
    db_session.add(slot)
    db_session.commit()

    assert EVERY_BUSY_LANE_HAS_AN_OCCUPANT.repair(db_session, violations) == 0
    db_session.refresh(slot)
    assert slot.is_available is False, "released a lane claimed since the pass detected it"


# ---- the corners the detector has to get right (test-break) -----------------


def test_a_ghost_lane_that_never_ran_anything_is_still_given_back(db_session: Session):
    """A slot can be busy with nothing and have no lane entry behind it at all —
    a reservation that died between `claim_free_slot` and `bind` leaves exactly
    that, and `_record_ghost_reclaim` already handles the shape ("and against
    nothing when the lane never ran anything").

    The lane is just as unusable, so it must still be found and released. A
    detector that reads the ticket by joining `queued_runs` drops this row
    silently, and the capacity is gone until someone restarts the server.
    """
    slot = _slots(db_session)[0]
    slot.is_available = False
    slot.current_orchestration_run_id = None
    slot.current_run_id = None
    slot.assigned_at = utcnow() - RESERVATION_GRACE - timedelta(seconds=1)
    db_session.add(slot)
    db_session.commit()

    violations = list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session))

    assert [v.subject_id for v in violations] == [slot.id]
    assert violations[0].ticket_id in (None, "")
    check = run_invariant(db_session, EVERY_BUSY_LANE_HAS_AN_OCCUPANT)
    assert (check.found, check.repaired) == (1, 1)
    db_session.refresh(slot)
    assert slot.is_available is True
    assert _findings(db_session) == [], (
        "a ticketless re-entry is logged, not filed (invariants._report)"
    )


def test_a_ghost_claimed_before_the_guard_landed_is_backlog_not_reentry(
    db_session: Session, probe_tickets
):
    """`since` splits the two, and the split is only real if the detector dates
    the row. `assigned_at` is the only date a slot carries and it is when the
    lane was taken, so a claim from before 2026-09-21 is backlog: repaired, and
    *not* reported as the guard having failed.

    A detector leaving `entered_at` None reports every pre-fix row as a
    re-entry (`invariants._entered_after` errs towards reporting), which would
    escalate a bug at the first ticket for a lane the fix was never asked to
    have prevented.
    """
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    entry = _ghost(db_session, ticket, slot)
    before_the_guard = datetime(2026, 9, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    slot.assigned_at = before_the_guard
    entry.promoted_at = before_the_guard - timedelta(minutes=5)
    db_session.add(slot)
    db_session.add(entry)
    db_session.commit()

    check = run_invariant(db_session, EVERY_BUSY_LANE_HAS_AN_OCCUPANT)

    assert (check.found, check.reentered, check.repaired) == (1, 0, 1)
    assert _findings(db_session) == []
    db_session.refresh(slot)
    assert slot.is_available is True, "backlog is still repaired"


def test_a_ghost_whose_claim_has_no_time_on_it_is_not_a_violation(
    db_session: Session, probe_tickets
):
    """The stricter answer, at the third caller. AC6 resolves an undatable claim
    towards leaving it alone, and the detector reads the same predicate as the
    lane and the sweep — so it must come to the same answer here rather than
    filing a DEFECT finding for a row the other two deliberately skip.
    """
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    _ghost(db_session, ticket, slot)
    slot.assigned_at = None
    db_session.add(slot)
    db_session.commit()

    assert list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session)) == []


def test_a_lane_whose_occupant_died_is_not_this_invariants_business(
    db_session: Session, probe_tickets
):
    """Scope. "Busy with a run that finished" is the ordinary residue
    `reconcile_slots` has swept since before this ticket; "busy with nothing" is
    the state nothing could ever release. A detector that merges them reports a
    DEFECT re-entry every time a run exits uncleanly — a guard that cries wolf
    is retired by whoever is on call, and then the real one is gone too.
    """
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    orch = OrchestrationRun(
        run_code="orch_dead",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.SUCCEEDED,
    )
    db_session.add(orch)
    db_session.commit()
    slot.is_available = False
    slot.current_orchestration_run_id = orch.id
    slot.assigned_at = utcnow() - RESERVATION_GRACE - timedelta(seconds=1)
    db_session.add(slot)
    db_session.commit()

    assert list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session)) == []


def test_detection_repairs_nothing_on_its_own(db_session: Session, probe_tickets):
    """`run_invariant` reports re-entries *before* repair erases them, so a
    detector that heals as it reads would file nothing and report a count that
    looks identical to a healthy pass."""
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    _ghost(db_session, ticket, slot)

    first = list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session))
    second = list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session))

    assert [v.subject_id for v in first] == [v.subject_id for v in second] == [slot.id]
    db_session.refresh(slot)
    assert slot.is_available is False, "detect wrote to the row it was asked about"


def test_the_repair_does_not_reach_for_the_whole_sweep(db_session: Session, probe_tickets):
    """AC10 as a mechanical check, not an outcome. The behavioural test below
    catches a sweep that reclaims a second ghost; it cannot catch one that
    sweeps and then happens to find nothing else, which still hands the next
    unlucky row to a pass that never detected or reported it.
    """
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    _ghost(db_session, ticket, slot)
    violations = list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session))

    with patch.object(ParallelQueueService, "reconcile_slots", autospec=True) as sweep:
        repaired = EVERY_BUSY_LANE_HAS_AN_OCCUPANT.repair(db_session, violations)

    assert sweep.call_count == 0, "the repair called reconcile_slots"
    assert repaired == 1
    db_session.refresh(slot)
    assert slot.is_available is True


def test_the_repair_says_where_a_person_reads_that_it_took_a_lane_back(
    db_session: Session, probe_tickets
):
    """The repair is the existing ghost reclaim, and the reclaim's visible half
    is the decision event: slot 1 sat busy with nothing for 23 minutes because
    giving it back was a log line nobody read. A repair that only flips the
    column is the silent version of the same fix.
    """
    _, ticket = probe_tickets
    slot = _slots(db_session)[0]
    _ghost(db_session, ticket, slot)

    run_invariant(db_session, EVERY_BUSY_LANE_HAS_AN_OCCUPANT)

    payloads = [
        json.loads(event.payload_json)
        for event in event_bus.ticket_history(db_session, ticket.id, limit=50)
        if event.type == EventType.ORCHESTRATOR_DECISION
    ]
    assert [p["decision"] for p in payloads] == [OrchestratorDecision.RECLAIMED_GHOST_LANE.value]
    assert payloads[0]["slot_number"] == slot.slot_number


def test_every_ghost_in_a_full_pool_is_found_in_one_pass(db_session: Session, probe_tickets):
    """Volume, and order-independence with it. The incident had one ghost; a
    restart loop can leave every lane that way, and a detector returning the
    first row it finds (`.first()`, `limit(1)`) would give one lane back per
    reconcile tick while the board stays full.
    """
    _, ticket = probe_tickets
    slots = _slots(db_session)
    for slot in slots:
        _ghost(db_session, ticket, slot)

    check = run_invariant(db_session, EVERY_BUSY_LANE_HAS_AN_OCCUPANT)

    assert (check.found, check.repaired) == (len(slots), len(slots))
    for slot in slots:
        db_session.refresh(slot)
        assert slot.is_available is True
    assert list(EVERY_BUSY_LANE_HAS_AN_OCCUPANT.detect(db_session)) == []


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
