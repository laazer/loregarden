"""What the board says about waiting, and whether it is true.

Every figure in the queue's stat row was wrong in the same way: a lane entry
has no `run_id` until it starts, and the queue read itself through the agent
run behind each entry. So the queue reported a length of zero, a clear time of
zero, and an empty projection while three lanes sat full — the board's own
lane cards were the only thing that knew otherwise.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    OrchestrationRun,
    QueuedRun,
    QueuePosition,
    RunStatus,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.parallel_queue import ParallelQueueService
from loregarden.services.queue_status import build_queue_status
from loregarden.services.run_duration_stats import (
    load_duration_stats,
    project_clear_time,
    project_lane_waits,
)
from sqlmodel import Session, select

STAGES = [
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 1},
    {"key": "done", "name": "Done", "agent_id": "", "order": 2, "terminal": True},
]


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    template = WorkflowTemplate(
        slug="tdd",
        name="TDD",
        version=1,
        stages_json=json.dumps(STAGES),
        transitions_json="[]",
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    ws = Workspace(slug="proj", name="proj", repo_path=".", workflow_template_id=template.id)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace: Workspace, code: str) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace.id, title=code)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=workspace.workflow_template_id,
            template_version=1,
            current_stage_key="implement",
            stages_json=json.dumps(
                [{"key": stage["key"], "status": StageStatus.PENDING.value} for stage in STAGES]
            ),
        )
    )
    session.commit()
    return ticket


def _history(session: Session, workspace: Workspace, seconds: float, count: int = 4) -> None:
    """Enough finished runs that a median is not one outlier."""
    started = datetime.now(timezone.utc) - timedelta(days=1)
    for index in range(count):
        session.add(
            AgentRun(
                run_code=f"hist-{seconds}-{index}",
                ticket_id=None,
                workspace_id=workspace.id,
                agent_id="backend_implementer",
                stage_key="implement",
                status=RunStatus.SUCCEEDED,
                started_at=started,
                finished_at=started + timedelta(seconds=seconds),
            )
        )
    session.commit()


def _occupy(session: Session, slot_number: int) -> None:
    """Make a lane busy, which is the only reason an entry waits in it.

    `reconcile_lanes` starts the head of any idle lane that has work queued —
    a refused dispatch used to leave exactly that state and nothing retried it.
    An entry parked in an *available* lane is therefore a state the system now
    heals, so a fixture that wants a waiting entry has to occupy its lane the
    way real waiting does.
    """
    ParallelQueueService(session).initialize_slots()
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == slot_number)).one()
    slot.is_available = False
    # Stamped, because a slot held by nothing is residue: `reconcile_slots`
    # reclaims it once it falls outside the reservation grace, frees the lane,
    # and then drains the very entry this fixture is trying to keep waiting.
    slot.assigned_at = datetime.now(timezone.utc)
    session.add(slot)
    session.commit()


def _lane_entry(session: Session, ticket: Ticket, slot_number: int, position: int) -> QueuedRun:
    _occupy(session, slot_number)
    entry = QueuedRun(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        slot_number=slot_number,
        position=position,
        status=QueuePosition.QUEUED,
        entry_kind="orchestration",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@pytest.mark.asyncio
async def test_a_lane_entry_is_visible_without_an_agent_run(session, workspace):
    """The bug in one assertion: an entry that has not started yet has no
    `run_id`, and reading the queue through that dropped it entirely."""
    ticket = _ticket(session, workspace, "T-1")
    _lane_entry(session, ticket, slot_number=1, position=1)

    queued = await ParallelQueueService(session).get_queued_runs()

    assert [entry["ticket_id"] for entry in queued] == [ticket.id]
    assert queued[0]["run_id"] == ""
    assert queued[0]["slot_number"] == 1
    assert queued[0]["entry_kind"] == "orchestration"


@pytest.mark.asyncio
async def test_the_queue_length_matches_what_the_lanes_show(session, workspace):
    for index in range(3):
        _lane_entry(session, _ticket(session, workspace, f"T-{index}"), 1, index + 1)

    status = await build_queue_status(session)

    assert status["queue_length"] == 3
    assert sum(len(lane["waiting"]) for lane in status["lanes"]) == 3
    assert status["stats"]["queued_count"] == 3


@pytest.mark.asyncio
async def test_a_queued_entry_is_priced_by_its_whole_pipeline(session, workspace):
    _history(session, workspace, seconds=300)
    ticket = _ticket(session, workspace, "T-2")
    _lane_entry(session, ticket, slot_number=1, position=1)

    status = await build_queue_status(session)
    entry = status["lanes"][0]["waiting"][0]

    assert entry["estimated_duration_seconds"] == pytest.approx(300.0)
    assert entry["ticket_tree_estimate"]["ticket_count"] == 1
    # Nothing is running, so it starts now.
    assert entry["estimated_wait_seconds"] == 0.0
    assert status["estimated_clear_seconds"] == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_waiting_behind_a_lane_mate_is_not_waiting_for_nothing(session, workspace):
    """Two entries in one lane run one after the other. Reported against the
    whole pool they both read as "starts now", which is the number that made a
    five-deep lane look instant."""
    _history(session, workspace, seconds=300)
    first = _ticket(session, workspace, "T-3")
    second = _ticket(session, workspace, "T-4")
    _lane_entry(session, first, slot_number=1, position=1)
    _lane_entry(session, second, slot_number=1, position=2)

    status = await build_queue_status(session)
    waiting = status["lanes"][0]["waiting"]

    assert waiting[0]["estimated_wait_seconds"] == pytest.approx(0.0)
    assert waiting[1]["estimated_wait_seconds"] == pytest.approx(300.0)
    assert status["estimated_clear_seconds"] == pytest.approx(600.0)
    assert status["estimated_wait_seconds"] == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_no_history_still_reports_the_queue_it_can_count(session, workspace):
    """Unknown timing is not unknown existence. The length is a fact; only the
    projection depends on history."""
    _lane_entry(session, _ticket(session, workspace, "T-5"), 1, 1)

    status = await build_queue_status(session)

    assert status["queue_length"] == 1
    assert status["estimated_clear_seconds"] is None
    assert status["estimated_wait_seconds"] is None
    assert status["lanes"][0]["waiting"][0]["estimated_duration_seconds"] is None


@pytest.mark.asyncio
async def test_a_running_lane_reports_what_is_left_of_its_ticket(session, workspace):
    """A lane card used to draw an indeterminate bar forever: its estimate was
    suppressed because one agent's median could not describe a whole ticket."""
    _history(session, workspace, seconds=300)
    ticket = _ticket(session, workspace, "T-6")
    orch = OrchestrationRun(run_code="o-1", ticket_id=ticket.id, workspace_id=workspace.id)
    session.add(orch)
    session.commit()
    session.refresh(orch)

    ParallelQueueService(session).initialize_slots()
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    slot.is_available = False
    slot.current_orchestration_run_id = orch.id
    slot.assigned_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    session.add(slot)
    session.commit()

    status = await build_queue_status(session)
    running = status["lanes"][0]["running"]

    assert running is not None
    assert running["estimated_remaining_seconds"] == pytest.approx(300.0)
    # The bar's denominator is elapsed plus remaining, so a ticket a minute in
    # draws a fifth full rather than not at all.
    assert running["estimated_duration_seconds"] == pytest.approx(360.0, abs=2)


def test_lane_waits_do_not_migrate_between_lanes():
    """Pinned entries queue behind their own lane; only unpinned ones take
    whichever slot opens first."""
    medians = {"*": 100.0}
    active = [{"slot_number": 1, "agent_id": "a", "elapsed_seconds": 0}]
    queued = [
        {"slot_number": 1, "agent_id": "a"},
        {"slot_number": 2, "agent_id": "a"},
    ]

    assert project_lane_waits(active, queued, medians, 3) == [100.0, 0.0]
    assert project_clear_time(active, queued, medians, 3) == 200.0


def test_a_precomputed_remaining_beats_the_median():
    """The queue prices lane entries by their whole subtree; the projection has
    to use that figure rather than re-deriving a per-agent one."""
    medians = {"*": 100.0}
    queued = [{"slot_number": 1, "agent_id": "a", "estimated_remaining_seconds": 900.0}]

    assert project_clear_time([], queued, medians, 3) == 900.0


def test_rework_shows_up_in_the_loaded_stats(session, workspace):
    """Two runs of one stage in one orchestration is a stage that ran twice,
    and the multiplier is the only place that fact reaches an estimate."""
    ticket = _ticket(session, workspace, "T-7")
    orch = OrchestrationRun(run_code="o-2", ticket_id=ticket.id, workspace_id=workspace.id)
    session.add(orch)
    session.commit()
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    for index in range(2):
        session.add(
            AgentRun(
                run_code=f"rw-{index}",
                ticket_id=None,
                workspace_id=workspace.id,
                orchestration_run_id=orch.id,
                agent_id="backend_implementer",
                stage_key="implement",
                status=RunStatus.SUCCEEDED,
                started_at=started,
                finished_at=started + timedelta(seconds=100),
            )
        )
    session.commit()

    assert load_duration_stats(session).attempts_per_stage == 2.0
