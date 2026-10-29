"""A closed ticket's workflow is closed, whichever writer closed it.

lg-workflow-integrity-88 read `done` with `test-break/blocked`: it was fixed by
hand while its pipeline sat on an interrupted run, and closing the ticket left
the stages where they were. 361 of 466 closed tickets on the live database had
an open stage. `db.closed_workflow_guard` closes them at flush; these prove it
does so for each kind of writer, and that reopening gives the work back.

Tickets are built through `TicketService`, so each carries the workflow
instance a real ticket has. A bare `Ticket()` has none, and every assertion
about its stages would pass without exercising anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from loregarden.db.closed_workflow_guard import (
    CLOSED_WITH_TICKET,
    close_stage_rows,
    open_stage_keys,
    reopen_stage_rows,
)
from loregarden.db.versions.closed_tickets_close_workflows import m_closed_tickets_close_workflows
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkflowInstance,
)
from loregarden.services.invariant_registry import CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS
from loregarden.services.invariants import run_invariant
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_state_service import choose, derive
from loregarden.services.workflow_service import WorkflowService, resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlalchemy import text
from sqlmodel import Session, select
from tests.factories import make_task_in_tree


def _instance(session: Session, ticket: Ticket) -> WorkflowInstance:
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    session.refresh(instance)
    return instance


def _statuses(session: Session, ticket: Ticket) -> dict[str, str]:
    return {row["key"]: row["status"] for row in json.loads(_instance(session, ticket).stages_json)}


def _block_a_stage(session: Session, ticket: Ticket, key: str) -> None:
    """Mid-pipeline and stuck: the shape lg-workflow-integrity-88 was in."""
    _, stages = resolve_ticket_stages(session, ticket)
    instance = _instance(session, ticket)
    first = min(stages, key=lambda s: s.order).key
    set_stage_status(ticket, instance, stages, first, StageStatus.DONE)
    set_stage_status(ticket, instance, stages, key, StageStatus.BLOCKED)
    ticket.workflow_stage_key = key
    ticket.workflow_stage_status = StageStatus.BLOCKED
    ticket.blocking_issues = "Agent run interrupted before completion"
    session.add_all([ticket, instance])
    session.commit()


def _second_stage(session: Session, ticket: Ticket) -> str:
    _, stages = resolve_ticket_stages(session, ticket)
    return sorted(stages, key=lambda s: s.order)[1].key


@pytest.fixture(name="stuck")
def stuck_fixture(db_session: Session) -> Ticket:
    ticket = make_task_in_tree(db_session, "closed workflow guard")
    _block_a_stage(db_session, ticket, _second_stage(db_session, ticket))
    assert open_stage_keys(_instance(db_session, ticket).stages_json), "fixture has nothing open"
    return ticket


# ---- each kind of writer ---------------------------------------------------


def _operator_closes(session: Session, ticket: Ticket) -> None:
    """The board, or `loregarden_update_ticket`: a manual `done`."""
    OrchestrationService(session).update_ticket_manual(
        ticket, UpdateTicketRequest(state=TicketState.DONE)
    )


def _superseded(session: Session, ticket: Ticket) -> None:
    """`supersede_ticket`: `wont_do`, with the reason in `blocking_issues`."""
    ticket.blocking_issues = "Superseded by lg-probe-2"
    choose(session, ticket, TicketState.WONT_DO, actor="agent")
    session.add(ticket)
    session.commit()


def _derived(session: Session, ticket: Ticket) -> None:
    """A recomputation: the rollup's door, which validates nothing."""
    derive(ticket, TicketState.DONE, actor="rollup")
    session.add(ticket)
    session.commit()


def _late_run_blocks_after_close(session: Session, ticket: Ticket) -> None:
    """A run that outlived its ticket's close reports its stage blocked."""
    _operator_closes(session, ticket)
    _, stages = resolve_ticket_stages(session, ticket)
    instance = _instance(session, ticket)
    set_stage_status(ticket, instance, stages, _second_stage(session, ticket), StageStatus.BLOCKED)
    session.add_all([ticket, instance])
    session.commit()


def _workflow_reset_after_close(session: Session, ticket: Ticket) -> None:
    """Re-attaching a template writes a fresh, all-pending stage map."""
    _operator_closes(session, ticket)
    WorkflowService(session).set_ticket_workflow_template(ticket, "loregarden-tdd")


def _instance_created_after_close(session: Session, ticket: Ticket) -> None:
    """`ensure_workflow_instance` attaching a workflow to a closed ticket."""
    _operator_closes(session, ticket)
    session.delete(_instance(session, ticket))
    session.commit()
    OrchestrationService(session).ensure_workflow_instance(ticket, commit=True)


WRITERS: dict[str, Callable[[Session, Ticket], None]] = {
    "operator closes": _operator_closes,
    "superseded": _superseded,
    "derived": _derived,
    "a late run blocks a stage": _late_run_blocks_after_close,
    "the workflow is reset": _workflow_reset_after_close,
    "an instance is created": _instance_created_after_close,
}


@pytest.mark.parametrize("writer", WRITERS.values(), ids=WRITERS.keys())
def test_every_writer_leaves_a_closed_ticket_with_a_closed_workflow(
    db_session: Session, stuck: Ticket, writer: Callable[[Session, Ticket], None]
):
    writer(db_session, stuck)
    db_session.refresh(stuck)

    assert stuck.state in (TicketState.DONE, TicketState.WONT_DO)
    assert open_stage_keys(_instance(db_session, stuck).stages_json) == []
    assert stuck.workflow_stage_status == _statuses(db_session, stuck)[stuck.workflow_stage_key]
    assert stuck.workflow_stage_status in (StageStatus.DONE, StageStatus.WONT_DO)


def test_the_closing_note_says_what_the_stage_was(db_session: Session, stuck: Ticket):
    """A `wont_do` with no note reads as an unexplained gap in the workflow pane."""
    blocked = stuck.workflow_stage_key
    _operator_closes(db_session, stuck)

    rows = {r["key"]: r for r in json.loads(_instance(db_session, stuck).stages_json)}
    assert rows[blocked]["note"] == f"{CLOSED_WITH_TICKET} (done) while blocked"


def test_supersession_keeps_its_reason(db_session: Session, stuck: Ticket):
    """On a `wont_do` ticket `blocking_issues` is the reason, not a stale block."""
    _superseded(db_session, stuck)
    db_session.refresh(stuck)
    assert stuck.blocking_issues == "Superseded by lg-probe-2"


# ---- reopening --------------------------------------------------------------


def test_reopening_gives_back_exactly_the_stages_closing_took(db_session: Session, stuck: Ticket):
    before = _statuses(db_session, stuck)
    _operator_closes(db_session, stuck)

    OrchestrationService(db_session).update_ticket_manual(
        stuck, UpdateTicketRequest(state=TicketState.BACKLOG)
    )
    after = _statuses(db_session, stuck)

    # Finished stages stay finished; every stage that was owed is owed again,
    # a blocked one as a fresh start rather than a block nobody is looking at.
    expected = {
        key: StageStatus.PENDING.value if status == StageStatus.BLOCKED.value else status
        for key, status in before.items()
    }
    assert after == expected


def test_reopening_leaves_a_pruned_stage_pruned(db_session: Session, stuck: Ticket):
    """Only the marker reopens a stage. An agent's prune is a decision."""
    _, stages = resolve_ticket_stages(db_session, stuck)
    pruned = sorted(stages, key=lambda s: s.order)[2].key
    instance = _instance(db_session, stuck)
    set_stage_status(stuck, instance, stages, pruned, StageStatus.WONT_DO, note="not needed")
    db_session.add_all([stuck, instance])
    db_session.commit()

    _operator_closes(db_session, stuck)
    OrchestrationService(db_session).update_ticket_manual(
        stuck, UpdateTicketRequest(state=TicketState.BACKLOG)
    )

    rows = {r["key"]: r for r in json.loads(_instance(db_session, stuck).stages_json)}
    assert rows[pruned] == {"key": pruned, "status": "wont_do", "note": "not needed"}


def test_an_open_tickets_own_cursor_write_is_left_alone(db_session: Session, stuck: Ticket):
    """Only a closed ticket's cursor is the guard's. A writer blocking an open
    ticket sets the cursor itself, and a state change must not undo it."""
    key = _second_stage(db_session, stuck)  # a query: it autoflushes, so ask first
    stuck.state = TicketState.IN_PROGRESS
    db_session.add(stuck)
    db_session.commit()
    stuck.state = TicketState.BLOCKED
    stuck.workflow_stage_key = key
    stuck.workflow_stage_status = StageStatus.AWAITING
    db_session.add(stuck)
    db_session.commit()
    db_session.refresh(stuck)

    assert stuck.workflow_stage_status == StageStatus.AWAITING


def test_a_note_an_open_stage_carried_survives_the_round_trip():
    rows = json.dumps([{"key": "plan", "status": "pending", "note": "waiting on design"}])
    closed = close_stage_rows(rows, TicketState.DONE)
    reopened = reopen_stage_rows(closed.stages_json)

    assert json.loads(reopened.stages_json) == json.loads(rows)
    assert (closed.keys, reopened.keys) == (("plan",), ("plan",))


# ---- the backlog ------------------------------------------------------------


def test_the_migration_closes_the_backlog_and_reopening_still_works(
    db_session: Session, stuck: Ticket
):
    """The live backlog is repaired by migration, with the guard's own note, so a
    ticket it closed reopens like any other."""
    db_session.execute(text("UPDATE tickets SET state='done' WHERE id=:t"), {"t": stuck.id})
    db_session.commit()
    m_closed_tickets_close_workflows(db_session.connection())
    db_session.commit()
    db_session.expire_all()

    assert open_stage_keys(_instance(db_session, stuck).stages_json) == []
    assert stuck.workflow_stage_status == StageStatus.WONT_DO
    assert run_invariant(db_session, CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS).found == 0

    OrchestrationService(db_session).update_ticket_manual(
        stuck, UpdateTicketRequest(state=TicketState.BACKLOG)
    )
    assert StageStatus.PENDING.value in _statuses(db_session, stuck).values()


def test_the_invariant_repairs_a_row_written_past_the_guard(db_session: Session, stuck: Ticket):
    """Raw SQL is the one writer the flush cannot see, so it stands in for the
    backlog: rows closed before the guard existed."""
    _operator_closes(db_session, stuck)
    db_session.execute(
        text("UPDATE workflow_instances SET stages_json=:j WHERE ticket_id=:t"),
        {"j": json.dumps([{"key": stuck.workflow_stage_key, "status": "blocked"}]), "t": stuck.id},
    )
    db_session.execute(
        text("UPDATE tickets SET workflow_stage_status='blocked' WHERE id=:t"), {"t": stuck.id}
    )
    db_session.commit()
    db_session.expire_all()

    first = run_invariant(db_session, CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS)
    second = run_invariant(db_session, CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS)

    assert (first.found, first.repaired) == (1, 1)
    assert second.found == 0
    db_session.refresh(stuck)
    assert stuck.workflow_stage_status == StageStatus.WONT_DO
