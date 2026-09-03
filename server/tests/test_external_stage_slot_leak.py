"""An externally driven run holds one slot, however many stages it starts.

Observed live on 2026-08-29 driving a ticket through the terminal autopilot
(driver `external_mcp`). Each `loregarden_start_stage` claimed a slot and gave
none back: `complete_stage` released nothing, and the slot was bound to the
*orchestration* run while the only release path matched on the *agent* run id, so
nothing could ever find it. A 3-slot pool self-blocked at the second stage, and
the surplus slot was held against nothing — blocking two unrelated sessions'
work until it was cleared by hand (lg-workflow-integrity-568).

The fix is not a second release path. It is that an orchestration already inside
the pool does not get a second slot: stages run sequentially within one run, so
one run is one occupant. That also removes the ambiguity behind AC4 — a release
resolving a slot by run id cannot pick the wrong one when a run never holds two.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import AgentSlot, QueuedRun, QueuePosition
from loregarden.services.queue_admission import QueueAdmissionService
from sqlmodel import Session, select
from tests.factories import make_orchestration_run, make_ticket, make_workspace

WORKSPACE = "ws-slot"
POOL = 3


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        make_workspace(session, workspace_id=WORKSPACE, slug=WORKSPACE)
        yield session


def _ticket(session: Session, ticket_id: str = "t-slot"):
    return make_ticket(session, ticket_id=ticket_id, workspace_id=WORKSPACE)


def _available(session: Session) -> int:
    return len([s for s in session.exec(select(AgentSlot)).all() if s.is_available])


def test_starting_more_stages_than_slots_holds_one_slot(session):
    """AC1, AC2 and AC5. Twelve stage starts against a three-slot pool.

    Before the fix this exhausted the pool at the second stage. The assertion is
    on the pool, not on a return value: a leak is invisible from the caller's
    side, which is why it survived long enough to block other sessions.
    """
    ticket = _ticket(session)
    orch = make_orchestration_run(
        session, workspace_id=WORKSPACE, ticket_id=ticket.id, run_code="orch-slot"
    )
    admission = QueueAdmissionService(session, max_concurrent=POOL)

    first = admission.reserve_orchestration(ticket)
    assert first.admitted
    first.bind(orchestration_run_id=orch.id)
    assert _available(session) == POOL - 1

    for index in range(12):
        stage = admission.reserve_stage(
            ticket, stage_key=f"stage-{index}", orchestration_run_id=orch.id
        )
        assert stage.admitted, f"stage {index} was refused — the pool leaked"
        assert stage.reused is True
        assert stage.slot_number == first.slot_number
        stage.bind(orchestration_run_id=orch.id)
        # Still exactly one slot held, at every step.
        assert _available(session) == POOL - 1, f"slot count moved at stage {index}"

    first.release()
    assert _available(session) == POOL, "the pool did not return to its starting state"


def test_a_reused_reservation_never_releases_the_orchestrations_slot(session):
    """`run_admitted` releases on exception. On a reused slot that would take the
    lane away from a run that is still going — the stage failed, not the run."""
    ticket = _ticket(session)
    orch = make_orchestration_run(
        session, workspace_id=WORKSPACE, ticket_id=ticket.id, run_code="orch-keep"
    )
    admission = QueueAdmissionService(session, max_concurrent=POOL)
    held = admission.reserve_orchestration(ticket)
    held.bind(orchestration_run_id=orch.id)

    stage = admission.reserve_stage(ticket, stage_key="boom", orchestration_run_id=orch.id)
    stage.release()

    assert _available(session) == POOL - 1, "a failed stage released the run's own slot"


def test_a_standalone_stage_still_claims_its_own_slot(session):
    """No orchestration, no reuse. A stage run started on its own is a separate
    occupant and must still be counted."""
    ticket = _ticket(session)
    admission = QueueAdmissionService(session, max_concurrent=POOL)

    stage = admission.reserve_stage(ticket, stage_key="solo")

    assert stage.admitted
    assert stage.reused is False
    assert _available(session) == POOL - 1


def test_one_run_never_holds_two_slots(session):
    """AC4, by making the state unreachable rather than teaching release to
    disambiguate it."""
    ticket = _ticket(session)
    orch = make_orchestration_run(
        session, workspace_id=WORKSPACE, ticket_id=ticket.id, run_code="orch-one"
    )
    admission = QueueAdmissionService(session, max_concurrent=POOL)
    admission.reserve_orchestration(ticket).bind(orchestration_run_id=orch.id)

    for index in range(4):
        admission.reserve_stage(ticket, stage_key=f"s{index}", orchestration_run_id=orch.id).bind(
            orchestration_run_id=orch.id
        )

    holding = [
        s
        for s in session.exec(select(AgentSlot)).all()
        if s.current_orchestration_run_id == orch.id
    ]
    assert len(holding) == 1, f"one run holds {len(holding)} slots"


def test_retrying_a_refused_stage_does_not_queue_it_twice(session):
    """AC3. Three retries once produced three entries for one (ticket, stage),
    each of which would later promote and re-run it — one dispatched a real agent
    against a stage that had been done for 26 minutes."""
    ticket = _ticket(session)
    admission = QueueAdmissionService(session, max_concurrent=1)

    # Fill the single slot with somebody else's work.
    other = _ticket(session, ticket_id="t-other")
    other_orch = make_orchestration_run(
        session, workspace_id=WORKSPACE, ticket_id=other.id, run_code="orch-other"
    )
    admission.reserve_orchestration(other).bind(orchestration_run_id=other_orch.id)

    refusals = [admission.reserve_stage(ticket, stage_key="test-break") for _ in range(3)]
    assert all(r.admitted is False for r in refusals), "expected the pool to be full"

    queued = session.exec(
        select(QueuedRun).where(
            QueuedRun.ticket_id == ticket.id,
            QueuedRun.status == QueuePosition.QUEUED,
        )
    ).all()
    assert len(queued) == 1, f"{len(queued)} entries queued for one (ticket, stage)"
    # And the retries report the place already held, not a new one.
    assert {r.position for r in refusals} == {queued[0].position}


def test_two_different_stages_may_both_wait(session):
    """The guard keys on (ticket, kind, stage). Two genuinely different stage
    requests are not duplicates of each other."""
    ticket = _ticket(session)
    admission = QueueAdmissionService(session, max_concurrent=1)
    other = _ticket(session, ticket_id="t-filler")
    filler_orch = make_orchestration_run(
        session, workspace_id=WORKSPACE, ticket_id=other.id, run_code="orch-filler"
    )
    admission.reserve_orchestration(other).bind(orchestration_run_id=filler_orch.id)

    admission.reserve_stage(ticket, stage_key="implement")
    admission.reserve_stage(ticket, stage_key="verify")

    queued = session.exec(
        select(QueuedRun).where(
            QueuedRun.ticket_id == ticket.id,
            QueuedRun.status == QueuePosition.QUEUED,
        )
    ).all()
    assert {e.stage_key for e in queued} == {"implement", "verify"}
