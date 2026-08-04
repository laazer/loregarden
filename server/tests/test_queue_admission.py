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
