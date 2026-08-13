"""`tickets.state` has one writer, and two doors into it.

The distinction that matters: a *chosen* move is validated, a *derived*
recomputation is not — because the table describes moves, and a state computed
from a stage map or a set of children is not a move anyone made. Enforcing the
table on derived writes would reject the parent rollup reopening a done parent
and the workflow settling a blocked ticket to done, both of which are correct.
"""

import re
from pathlib import Path

import pytest
from loregarden.models.domain import Ticket, TicketState, Workspace
from loregarden.services.ticket_state_service import (
    InvalidTicketTransition,
    can_choose,
    choose,
    derive,
)
from sqlmodel import Session

SERVICES = Path(__file__).resolve().parents[1] / "loregarden" / "services"


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


def _ticket(session, workspace, state=TicketState.BACKLOG, *, locked=False) -> Ticket:
    ticket = Ticket(
        external_id="T-1",
        workspace_id=workspace.id,
        title="T-1",
        state=state,
        state_locked=locked,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


# ---- chosen moves ------------------------------------------------------


def test_a_valid_chosen_move_is_written_with_its_bookkeeping(session, workspace):
    ticket = _ticket(session, workspace, TicketState.IN_PROGRESS)
    before = ticket.revision

    assert choose(session, ticket, TicketState.DONE, actor="human") is True

    assert ticket.state == TicketState.DONE
    assert ticket.revision == before + 1
    assert ticket.last_updated_by == "human"


def test_an_invalid_chosen_move_raises_rather_than_writing(session, workspace):
    """The API used to take any value from the request body and store it."""
    ticket = _ticket(session, workspace, TicketState.DONE)

    with pytest.raises(InvalidTicketTransition):
        choose(session, ticket, TicketState.IN_PROGRESS, actor="human")

    assert ticket.state == TicketState.DONE


def test_choosing_the_state_it_already_has_is_a_no_op(session, workspace):
    ticket = _ticket(session, workspace, TicketState.IN_PROGRESS)
    before = ticket.revision

    assert choose(session, ticket, TicketState.IN_PROGRESS, actor="human") is False
    assert ticket.revision == before


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # Both were rejected by the old table and both genuinely happen.
        (TicketState.BACKLOG, TicketState.BLOCKED),
        (TicketState.BLOCKED, TicketState.DONE),
        # Closing something nobody started; the API has always allowed it.
        (TicketState.BACKLOG, TicketState.DONE),
    ],
)
def test_moves_the_old_table_wrongly_rejected(current, target):
    assert can_choose(current, target)


def test_a_done_ticket_is_not_chosen_straight_back_to_in_progress():
    """Reopening is derived, or goes through backlog deliberately."""
    assert not can_choose(TicketState.DONE, TicketState.IN_PROGRESS)


# ---- derived writes ----------------------------------------------------


def test_a_derived_write_skips_the_table(session, workspace):
    """The rollup reopening a done parent — a move `choose` would reject."""
    ticket = _ticket(session, workspace, TicketState.DONE)

    assert derive(ticket, TicketState.IN_PROGRESS, actor="rollup") is True
    assert ticket.state == TicketState.IN_PROGRESS


def test_a_derived_write_respects_the_lock(session, workspace):
    ticket = _ticket(session, workspace, TicketState.IN_PROGRESS, locked=True)

    assert derive(ticket, TicketState.DONE, actor="rollup") is False
    assert ticket.state == TicketState.IN_PROGRESS


def test_a_derived_write_will_not_revive_an_abandoned_ticket(session, workspace):
    ticket = _ticket(session, workspace, TicketState.WONT_DO)

    assert derive(ticket, TicketState.IN_PROGRESS, actor="rollup") is False
    assert ticket.state == TicketState.WONT_DO


def test_a_derived_write_persists_without_anyone_calling_add(session, workspace):
    """Why `derive` needs no session: a tracked ticket is written on commit.

    `session.add` on an instance the session already tracks is a no-op, so the
    parameter it used to take did nothing — no derived caller emits an event
    either.
    """
    ticket = _ticket(session, workspace, TicketState.BACKLOG)

    assert derive(ticket, TicketState.IN_PROGRESS, actor="workflow") is True
    session.commit()
    session.expire_all()

    assert session.get(Ticket, ticket.id).state == TicketState.IN_PROGRESS


# ---- nobody bypasses it ------------------------------------------------


def test_no_service_assigns_ticket_state_directly():
    """The property that makes the rest of this module worth anything."""
    pattern = re.compile(r"^\s*(?!#)\S*ticket\.state\s*=\s")
    offenders = [
        f"{path.name}:{number}: {line.strip()}"
        for path in SERVICES.glob("*.py")
        if path.name != "ticket_state_service.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.match(line)
    ]
    assert offenders == [], (
        "assign tickets.state through ticket_state_service.choose/derive, "
        "so the revision bump and actor cannot be forgotten"
    )
