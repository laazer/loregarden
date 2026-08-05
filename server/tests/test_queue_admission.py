"""The slot pool bounds the machine, not just the queue board.

Work started from the Dashboard, the chat primitives and the MCP tools used to
reach the orchestrator directly, so the board could show three idle lanes while
every agent the box could run was already busy. These pin the gate that closed
that: capacity free means start now, capacity full means wait your turn — and
the caller is told which.
"""

from unittest.mock import patch

import pytest
from loregarden.models.domain import AgentSlot, QueuedRun, Ticket, Workspace
from loregarden.services.queue_admission import QueueAdmissionService
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace_id: str, code: str) -> Ticket:
    ticket = Ticket(external_id=code, workspace_id=workspace_id, title=code)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _fill_pool(session: Session, admission: QueueAdmissionService, workspace, count: int) -> None:
    """Occupy every slot, as running work would."""
    for index in range(count):
        ticket = _ticket(session, workspace.id, f"FILL-{index}")
        reservation = admission.reserve_orchestration(ticket)
        assert reservation.admitted
        reservation.bind(run_id=f"run-fill-{index}")


def test_capacity_free_admits_and_claims_a_slot(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=3)
    ticket = _ticket(session, workspace.id, "LG-1")

    reservation = admission.reserve_orchestration(ticket)

    assert reservation.admitted is True
    assert reservation.slot_number == 1
    # Claimed before the caller starts anything, so a second request cannot
    # read the same slot as free.
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is False


def test_a_full_pool_queues_rather_than_starting(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=2)
    _fill_pool(session, admission, workspace, 2)

    ticket = _ticket(session, workspace.id, "LG-LATE")
    reservation = admission.reserve_orchestration(ticket)

    assert reservation.admitted is False
    assert reservation.position == 1
    # The caller is told, rather than left to infer it from a silent no-op.
    assert "busy" in reservation.message
    assert "Queued in slot" in reservation.message


def test_queueing_lands_in_the_shortest_lane(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=2)
    _fill_pool(session, admission, workspace, 2)

    # Lane 1 gets two behind it; the next arrival should prefer lane 2.
    for code in ("A", "B"):
        admission.lanes.add_to_lane(
            ticket_id=_ticket(session, workspace.id, code).id, slot_number=1
        )

    reservation = admission.reserve_orchestration(_ticket(session, workspace.id, "LG-NEXT"))

    assert reservation.slot_number == 2


def test_a_queued_stage_stays_a_stage(session, workspace):
    """ "Run this one stage" must not become "run everything left"."""
    admission = QueueAdmissionService(session, max_concurrent=1)
    _fill_pool(session, admission, workspace, 1)

    ticket = _ticket(session, workspace.id, "LG-STAGE")
    reservation = admission.reserve_stage(ticket, stage_key="verify")

    assert reservation.admitted is False
    entry = session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    assert entry.entry_kind == "stage"
    assert entry.stage_key == "verify"


def test_a_queued_orchestration_is_marked_as_one(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=1)
    _fill_pool(session, admission, workspace, 1)

    ticket = _ticket(session, workspace.id, "LG-ORCH")
    admission.reserve_orchestration(ticket, auto_approve=True, stop_at_stage_key="verify")

    entry = session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    assert entry.entry_kind == "orchestration"
    assert entry.auto_approve is True
    assert entry.stop_at_stage_key == "verify"


def test_binding_names_what_the_caller_started(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=3)
    reservation = admission.reserve_orchestration(_ticket(session, workspace.id, "LG-1"))

    reservation.bind(run_id="run-abc")

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.current_run_id == "run-abc"
    assert slot.is_available is False


def test_releasing_gives_the_slot_back(session, workspace):
    """A caller whose start failed must not leave the lane held by nothing."""
    admission = QueueAdmissionService(session, max_concurrent=3)
    reservation = admission.reserve_orchestration(_ticket(session, workspace.id, "LG-1"))

    reservation.release()

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is True
    assert slot.current_run_id is None


def test_releasing_a_queued_reservation_is_a_no_op(session, workspace):
    """Nothing was claimed, so nothing is given back — and the entry stays."""
    admission = QueueAdmissionService(session, max_concurrent=1)
    _fill_pool(session, admission, workspace, 1)
    ticket = _ticket(session, workspace.id, "LG-QUEUED")
    reservation = admission.reserve_orchestration(ticket)

    reservation.release()

    assert session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    assert slot.is_available is False


def test_two_requests_cannot_take_the_same_slot(session, workspace):
    """The claim happens at reserve time, not at bind time."""
    admission = QueueAdmissionService(session, max_concurrent=1)

    first = admission.reserve_orchestration(_ticket(session, workspace.id, "LG-1"))
    second = admission.reserve_orchestration(_ticket(session, workspace.id, "LG-2"))

    assert first.admitted is True
    assert second.admitted is False


@pytest.mark.parametrize("endpoint_kind", ["orchestration", "stage"])
def test_a_queued_entry_starts_when_the_lane_drains(session, workspace, endpoint_kind):
    """Admission is a wait, not a rejection: the work still runs."""
    admission = QueueAdmissionService(session, max_concurrent=1)
    _fill_pool(session, admission, workspace, 1)

    ticket = _ticket(session, workspace.id, "LG-WAITING")
    if endpoint_kind == "orchestration":
        admission.reserve_orchestration(ticket)
    else:
        admission.reserve_stage(ticket, stage_key="verify")

    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    slot.is_available = True
    slot.current_run_id = None
    session.add(slot)
    session.commit()

    with (
        patch.object(admission.lanes, "_dispatch_orchestration") as dispatch_orch,
        patch.object(admission.lanes, "_dispatch_stage") as dispatch_stage,
    ):
        dispatch_orch.return_value = type("O", (), {"id": "orch-1", "run_code": "orch_1"})()
        dispatch_stage.return_value = type("R", (), {"id": "run-1"})()
        admission.lanes.start_lane_head(1)

    if endpoint_kind == "orchestration":
        dispatch_orch.assert_called_once()
    else:
        dispatch_stage.assert_called_once()


def _ticket_id_by_external_id(client, external_id: str) -> str:
    tickets = client.get("/api/tickets").json()
    match = next(t for t in tickets if t["external_id"] == external_id)
    return match["id"]


def test_orchestrating_binds_the_slot_it_reserved(client, db_session):
    """A reservation the caller never binds is a slot nothing can ever release.

    `schedule_orchestration` runs on a worker thread and the orchestration row
    is created *there*, so binding by reading the ticket's active run back
    raced that thread and lost. The slot then sat claimed with both ids null:
    `on_orchestration_complete` looks a slot up *by* the orchestration it holds,
    finds nothing, and the lane is gone for the life of the process. Three
    starts exhausted the pool.

    Patched to do nothing at all, which is the worst case for a read-back and
    the normal case for a thread that has not been scheduled yet.
    """
    with patch("loregarden.api.tickets.schedule_orchestration"):
        res = client.post(
            f"/api/tickets/{_ticket_id_by_external_id(client, '04-workflow-template-overrides')}"
            "/orchestrate",
            json={},
        )
    assert res.status_code == 200

    slots = db_session.exec(select(AgentSlot).where(AgentSlot.is_available == False)).all()  # noqa: E712
    assert len(slots) == 1
    assert slots[0].current_orchestration_run_id, (
        "the reserved slot names nothing, so no release will ever find it"
    )


def test_a_refused_start_gives_the_slot_back(client, db_session):
    """The claim and the reservation are given up together."""
    with patch(
        "loregarden.api.tickets.schedule_orchestration",
        side_effect=ValueError("no workflow for this ticket"),
    ):
        res = client.post(
            f"/api/tickets/{_ticket_id_by_external_id(client, '04-workflow-template-overrides')}"
            "/orchestrate",
            json={},
        )
    assert res.status_code == 400

    held = db_session.exec(select(AgentSlot).where(AgentSlot.is_available == False)).all()  # noqa: E712
    assert not held


def test_a_named_lane_is_honoured(session, workspace):
    """The operator picked a lane on a board that showed what each was doing."""
    admission = QueueAdmissionService(session, max_concurrent=3)
    ticket = _ticket(session, workspace.id, "T-pick")

    reservation = admission.reserve_orchestration(ticket, preferred_slot=3)

    assert reservation.admitted
    assert reservation.slot_number == 3


def test_a_named_lane_that_filled_still_runs_the_ticket(session, workspace):
    """A preference, not a demand: the lane can fill between opening the dialog
    and confirming, and the ask was to run the ticket."""
    admission = QueueAdmissionService(session, max_concurrent=3)
    taken = _ticket(session, workspace.id, "T-taken")
    admission.reserve_orchestration(taken, preferred_slot=2).bind(run_id="run-taken")

    reservation = admission.reserve_orchestration(
        _ticket(session, workspace.id, "T-second"), preferred_slot=2
    )

    assert reservation.admitted
    assert reservation.slot_number != 2


def test_a_full_pool_parks_in_the_lane_that_was_asked_for(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=3)
    _fill_pool(session, admission, workspace, 3)

    reservation = admission.reserve_orchestration(
        _ticket(session, workspace.id, "T-waiting"), preferred_slot=3
    )

    assert not reservation.admitted
    assert reservation.slot_number == 3


def test_a_parked_request_still_runs_what_was_asked_for(session, workspace):
    """The entry is the whole record of the ask by the time a lane reaches it.

    A driver override and a stage cap used to be dropped the moment the pool was
    full, so "at most one stage on cursor" became "the whole pipeline on the
    profile's driver" — and which you got depended on how busy the box was.
    """
    admission = QueueAdmissionService(session, max_concurrent=3)
    _fill_pool(session, admission, workspace, 3)
    ticket = _ticket(session, workspace.id, "T-parked")

    reservation = admission.reserve_orchestration(ticket, driver="builtin_autopilot", max_stages=1)
    assert not reservation.admitted

    entry = session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    assert entry.driver == "builtin_autopilot"
    assert entry.max_stages == 1


def test_the_lane_starts_a_parked_request_with_its_own_options(session, workspace):
    admission = QueueAdmissionService(session, max_concurrent=1)
    lanes = admission.lanes
    lanes.slots.initialize_slots()
    _fill_pool(session, admission, workspace, 1)
    ticket = _ticket(session, workspace.id, "T-parked")
    admission.reserve_orchestration(ticket, driver="builtin_autopilot", max_stages=2)

    # Free the lane, which starts what was waiting in it.
    slot = session.exec(select(AgentSlot).where(AgentSlot.slot_number == 1)).one()
    slot.is_available = True
    slot.current_run_id = None
    session.add(slot)
    session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration") as dispatch:
        lanes.start_lane_head(1)

    kwargs = dispatch.call_args.kwargs
    assert kwargs["max_stages"] == 2
    assert kwargs["driver"] is not None
