"""One ticket may hold one lane, whichever column records it (645).

A lane is claimed two ways. An orchestration writes `current_orchestration_run_id`
and leaves `current_run_id` NULL; a dispatched stage writes `current_run_id`
instead. Both are legitimate, and each admission path used to check only the
column it writes — so neither could see a claim made by the other.

Observed live on ticket 638: an external orchestration held one lane while the
builtin dispatcher put a stage run for the same ticket into another. A third of
the machine's capacity went to one ticket, and two drivers advanced one stage
map at the same time.

These tests build slot rows directly rather than driving admission. Reaching
`promote_from_queue_sync` dispatches for real — it checks out a branch in the
working tree — so a test that wanted to observe lane bookkeeping would instead
be running an agent.
"""

from datetime import datetime, timezone

import pytest
from loregarden.models.domain import (
    AgentSlot,
    OrchestrationDriver,
    OrchestrationRunStatus,
    QueuedRun,
)
from loregarden.services.parallel_queue import tickets_holding_lanes
from loregarden.services.queue_dispatch import LaneDispatch
from sqlmodel import Session, select
from tests.factories import (
    make_agent_run,
    make_orchestration_run,
    make_ticket,
    make_workspace,
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    return make_workspace(session, slug="proj")


def _slots(session: Session, count: int = 3) -> list[AgentSlot]:
    existing = list(session.exec(select(AgentSlot).order_by(AgentSlot.slot_number)).all())
    for number in range(len(existing) + 1, count + 1):
        slot = AgentSlot(slot_number=number, is_available=True)
        session.add(slot)
        existing.append(slot)
    session.commit()
    return existing[:count]


def _hold_with_stage_run(session, slot, workspace_id, ticket_id):
    run = make_agent_run(
        session, workspace_id=workspace_id, ticket_id=ticket_id, run_code=f"RUN-{slot.slot_number}"
    )
    slot.is_available = False
    slot.current_run_id = run.id
    slot.current_orchestration_run_id = None
    slot.assigned_at = datetime.now(timezone.utc)
    session.add(slot)
    session.commit()
    return run


def _hold_with_orchestration(session, slot, workspace_id, ticket_id, *, driver=None):
    run = make_orchestration_run(
        session, workspace_id=workspace_id, ticket_id=ticket_id, run_code=f"ORCH-{slot.slot_number}"
    )
    if driver is not None:
        run.driver = driver
        session.add(run)
    slot.is_available = False
    slot.current_orchestration_run_id = run.id
    slot.current_run_id = None
    slot.assigned_at = datetime.now(timezone.utc)
    session.add(slot)
    session.commit()
    return run


def test_a_lane_held_by_a_stage_run_is_visible(session, workspace):
    """The column the *other* admission path writes, which is the whole defect."""
    slots = _slots(session)
    make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    _hold_with_stage_run(session, slots[0], workspace.id, "t-1")

    assert tickets_holding_lanes(session) == {"t-1": [1]}


def test_a_lane_held_by_an_orchestration_is_visible(session, workspace):
    slots = _slots(session)
    make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    _hold_with_orchestration(session, slots[1], workspace.id, "t-1")

    assert tickets_holding_lanes(session) == {"t-1": [2]}


def test_one_ticket_across_both_columns_reports_both_lanes(session, workspace):
    """The live incident, reconstructed: 638 in two lanes by two different routes.

    This is the state the fix exists to prevent, so the reporting has to be able
    to describe it — a double claim nobody can see is one nobody can refuse.
    """
    slots = _slots(session)
    make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    _hold_with_orchestration(session, slots[1], workspace.id, "t-1")
    _hold_with_stage_run(session, slots[2], workspace.id, "t-1")

    assert tickets_holding_lanes(session) == {"t-1": [2, 3]}


def test_two_tickets_holding_one_lane_each_are_kept_apart(session, workspace):
    """A guard that answered "someone holds a lane" would pass the tests above."""
    slots = _slots(session)
    make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    make_ticket(session, workspace_id=workspace.id, ticket_id="t-2")
    _hold_with_orchestration(session, slots[0], workspace.id, "t-1")
    _hold_with_stage_run(session, slots[1], workspace.id, "t-2")

    assert tickets_holding_lanes(session) == {"t-1": [1], "t-2": [2]}


def test_exclude_slot_hides_the_callers_own_fresh_claim(session, workspace):
    """Admission claims the slot before it resolves the ticket behind the entry.

    Without this the caller finds its own claim and refuses itself, which would
    turn the fix into a deadlock that admits nothing.
    """
    slots = _slots(session)
    make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    _hold_with_stage_run(session, slots[0], workspace.id, "t-1")

    assert tickets_holding_lanes(session, exclude_slot=1) == {}
    assert tickets_holding_lanes(session, exclude_slot=2) == {"t-1": [1]}


def test_an_available_slot_holds_nothing(session, workspace):
    _slots(session)
    assert tickets_holding_lanes(session) == {}


def test_the_builtin_dispatcher_yields_to_a_live_external_orchestration(session, workspace):
    """645 AC2. `driver=external_mcp` says something outside is driving.

    The lane accounting made the race visible; this is the race itself. Two
    drivers on one stage map is the defect, and refusing here is what stops it —
    the entry carries the reason, because a refusal only in the server log is
    one the operator never learns about.
    """
    ticket = make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    owner = make_orchestration_run(
        session, workspace_id=workspace.id, ticket_id="t-1", run_code="ORCH-EXT"
    )
    owner.driver = OrchestrationDriver.EXTERNAL_MCP
    owner.status = OrchestrationRunStatus.RUNNING
    session.add(owner)
    entry = QueuedRun(workspace_id=workspace.id, ticket_id="t-1", slot_number=1, entry_kind="stage")
    session.add(entry)
    session.commit()

    assert LaneDispatch(session).dispatch_stage(ticket, entry) is None

    session.refresh(entry)
    assert owner.id in entry.failure_reason, entry.failure_reason
    assert entry.last_failed_at is not None


def test_a_finished_external_orchestration_does_not_block_the_dispatcher(session, workspace):
    """The discriminator: it is *live* ownership that yields, not ever having run.

    Without this the guard would strand every ticket an external harness had
    ever touched — which is worse than the defect, and would pass the test above.
    """
    ticket = make_ticket(session, workspace_id=workspace.id, ticket_id="t-1")
    done = make_orchestration_run(
        session, workspace_id=workspace.id, ticket_id="t-1", run_code="ORCH-EXT"
    )
    done.driver = OrchestrationDriver.EXTERNAL_MCP
    done.status = OrchestrationRunStatus.SUCCEEDED
    session.add(done)
    entry = QueuedRun(workspace_id=workspace.id, ticket_id="t-1", slot_number=1, entry_kind="stage")
    session.add(entry)
    session.commit()

    # It gets past the 645 guard and fails later, on its own terms — the point
    # is that `failure_reason` does not name the external orchestration.
    LaneDispatch(session).dispatch_stage(ticket, entry)

    session.refresh(entry)
    assert done.id not in (entry.failure_reason or ""), entry.failure_reason
