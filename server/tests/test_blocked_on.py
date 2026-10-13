"""Blocked on a person, or on other tickets: told apart, stored and derived.

Ten of the 27 blocked tickets on the live board were parents rolled up to
`blocked` by a blocked child. They carried no kind, so they read exactly like a
ticket a person had to look at, and a backlog ticket held by a prerequisite was
never marked as held at all.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    BlockedOn,
    BlockKind,
    Ticket,
    TicketDependency,
    TicketState,
    WaitingReason,
    WorkItemType,
    Workspace,
)
from loregarden.services.blocked_on import blocked_on_clause, blocked_on_map, waiting_on_map
from loregarden.services.stage_report import parse_stage_report
from loregarden.services.ticket_rollup import reconcile_all_parents, reconcile_ancestors
from sqlmodel import Session, col, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session: Session) -> Workspace:
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(
    session: Session,
    workspace: Workspace,
    code: str,
    *,
    state: TicketState = TicketState.BACKLOG,
    kind: BlockKind | None = None,
    parent: Ticket | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id,
        title=code,
        state=state,
        block_kind=kind,
        work_item_type=work_item_type,
        parent_ticket_id=parent.id if parent else None,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _depends(session: Session, ticket: Ticket, on: Ticket) -> None:
    session.add(TicketDependency(ticket_id=ticket.id, depends_on_ticket_id=on.id))
    session.commit()


def _block(session: Session, ticket: Ticket, kind: BlockKind) -> None:
    ticket.state = TicketState.BLOCKED
    ticket.block_kind = kind
    session.add(ticket)
    session.commit()
    reconcile_ancestors(session, ticket)


# --- the rollup stamps the parent -------------------------------------------------


def test_a_parent_blocked_by_its_child_says_so(session, workspace):
    feature = _ticket(session, workspace, "f", work_item_type=WorkItemType.FEATURE)
    task = _ticket(session, workspace, "t", parent=feature, state=TicketState.IN_PROGRESS)

    _block(session, task, BlockKind.DECISION)

    session.refresh(feature)
    assert (feature.state, feature.block_kind) == (TicketState.BLOCKED, BlockKind.TICKETS)


def test_the_kind_clears_when_the_child_unblocks(session, workspace):
    feature = _ticket(session, workspace, "f", work_item_type=WorkItemType.FEATURE)
    task = _ticket(session, workspace, "t", parent=feature, state=TicketState.IN_PROGRESS)
    _block(session, task, BlockKind.WORK)

    task.state, task.block_kind = TicketState.IN_PROGRESS, None
    session.add(task)
    session.commit()
    reconcile_ancestors(session, task)

    session.refresh(feature)
    assert (feature.state, feature.block_kind) == (TicketState.IN_PROGRESS, None)


def test_a_parents_own_block_kind_is_not_overwritten(session, workspace):
    feature = _ticket(
        session,
        workspace,
        "f",
        state=TicketState.BLOCKED,
        kind=BlockKind.HUMAN_ACTION,
        work_item_type=WorkItemType.FEATURE,
    )
    task = _ticket(session, workspace, "t", parent=feature, state=TicketState.IN_PROGRESS)

    _block(session, task, BlockKind.WORK)

    session.refresh(feature)
    assert feature.block_kind is BlockKind.HUMAN_ACTION


def test_the_startup_sweep_stamps_parents_already_blocked(session, workspace):
    """The live parents were blocked before the kind existed; the state does not
    move, so only the stamp tells the sweep there is something to correct."""
    feature = _ticket(
        session, workspace, "f", state=TicketState.BLOCKED, work_item_type=WorkItemType.FEATURE
    )
    _ticket(session, workspace, "t", parent=feature, state=TicketState.BLOCKED)

    changed = reconcile_all_parents(session)

    session.refresh(feature)
    assert [t.id for t in changed] == [feature.id]
    assert feature.block_kind is BlockKind.TICKETS


def test_an_agent_cannot_declare_the_rollups_kind():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        + json.dumps({"status": "blocked", "confidence": 0.9, "blocked_kind": "tickets"})
        + "\n<<<END_STAGE_REPORT>>>\n"
    )
    assert report is not None
    assert (report.blocked_kind, report.kind_as_written) == (None, "tickets")


# --- blocked_on: who it waits for ---------------------------------------------------


@pytest.fixture(name="board")
def board_fixture(session, workspace) -> dict[str, Ticket]:
    """One ticket per answer, plus the near misses that must answer nothing."""
    done = _ticket(session, workspace, "done", state=TicketState.DONE)
    open_prereq = _ticket(session, workspace, "open-prereq", state=TicketState.IN_PROGRESS)
    board = {
        "decision": _ticket(
            session, workspace, "decision", state=TicketState.BLOCKED, kind=BlockKind.DECISION
        ),
        "human": _ticket(
            session, workspace, "human", state=TicketState.BLOCKED, kind=BlockKind.HUMAN_ACTION
        ),
        "unclassified": _ticket(session, workspace, "unclassified", state=TicketState.BLOCKED),
        "harness": _ticket(
            session, workspace, "harness", state=TicketState.BLOCKED, kind=BlockKind.HARNESS
        ),
        "work": _ticket(session, workspace, "work", state=TicketState.BLOCKED, kind=BlockKind.WORK),
        "rolled_up": _ticket(
            session, workspace, "rolled-up", state=TicketState.BLOCKED, kind=BlockKind.TICKETS
        ),
        "held": _ticket(session, workspace, "held"),
        "met": _ticket(session, workspace, "met"),
        "started": _ticket(session, workspace, "started", state=TicketState.IN_PROGRESS),
        "parked": _ticket(session, workspace, "parked", state=TicketState.PARKED),
        "done": done,
        "open_prereq": open_prereq,
    }
    _depends(session, board["held"], open_prereq)
    _depends(session, board["met"], done)
    # Started by name past its prerequisite: running, not held.
    _depends(session, board["started"], open_prereq)
    return board


EXPECTED = {
    "decision": BlockedOn.PERSON,
    "human": BlockedOn.PERSON,
    "unclassified": BlockedOn.PERSON,
    "harness": BlockedOn.AGENT,
    "work": BlockedOn.AGENT,
    "rolled_up": BlockedOn.TICKETS,
    "held": BlockedOn.TICKETS,
}


def test_each_ticket_answers_who_it_waits_for(session, board):
    answers = blocked_on_map(session, list(board.values()))
    by_name = {name: answers.get(t.id) for name, t in board.items()}
    assert by_name == {name: EXPECTED.get(name) for name in board}


@pytest.mark.parametrize("wanted", list(BlockedOn))
def test_the_filter_agrees_with_the_derivation(session, board, wanted):
    """The tree filters in SQL and the rows answer in Python; they must not drift."""
    ids = [t.id for t in board.values()]
    matched = set(
        session.exec(
            select(Ticket.id).where(col(Ticket.id).in_(ids), blocked_on_clause([wanted]))
        ).all()
    )
    derived = blocked_on_map(session, list(board.values()))
    assert matched == {tid for tid, answer in derived.items() if answer is wanted}
    assert matched  # every answer has a ticket on this board


def test_no_filter_value_matches_nothing(session, board):
    assert session.exec(select(Ticket.id).where(blocked_on_clause([]))).all() == []


# --- waiting_on: which tickets ------------------------------------------------------


def test_a_rolled_up_parent_names_the_ticket_at_the_bottom_of_the_chain(session, workspace):
    """Naming the intermediate feature would only repeat the block; the task is
    the one a person can act on, and it says it needs a person."""
    milestone = _ticket(session, workspace, "m", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(
        session, workspace, "f", parent=milestone, work_item_type=WorkItemType.FEATURE
    )
    task = _ticket(session, workspace, "t", parent=feature, state=TicketState.IN_PROGRESS)
    _ticket(session, workspace, "fine", parent=feature, state=TicketState.IN_PROGRESS)
    _block(session, task, BlockKind.DECISION)

    session.refresh(milestone)
    refs = waiting_on_map(session, [milestone])[milestone.id]

    assert [(r.external_id, r.reason, r.blocked_on) for r in refs] == [
        ("t", WaitingReason.BLOCKED_CHILD, BlockedOn.PERSON)
    ]


def test_a_held_ticket_names_its_unfinished_prerequisites(session, board):
    refs = waiting_on_map(session, [board["held"], board["met"]])
    assert [(r.external_id, r.reason) for r in refs[board["held"].id]] == [
        ("open-prereq", WaitingReason.PREREQUISITE)
    ]
    assert board["met"].id not in refs


# --- the API carries both -----------------------------------------------------------


def test_the_ticket_and_the_tree_carry_the_answer(client: TestClient, db_session: Session):
    ws = db_session.exec(select(Workspace)).first()
    assert ws is not None
    feature = _ticket(db_session, ws, "api-f", work_item_type=WorkItemType.FEATURE)
    task = _ticket(db_session, ws, "api-t", parent=feature, state=TicketState.IN_PROGRESS)
    _block(db_session, task, BlockKind.HUMAN_ACTION)

    detail = client.get(f"/api/tickets/{feature.id}").json()
    assert (detail["block_kind"], detail["blocked_on"]) == ("tickets", "tickets")
    assert [r["external_id"] for r in detail["waiting_on"]] == ["api-t"]
    assert detail["waiting_on"][0]["blocked_on"] == "person"

    tree = client.get(f"/api/tickets/tree?workspace={ws.slug}&blocked_on=person").json()
    flat: list[dict] = []
    stack = list(tree)
    while stack:
        node = stack.pop()
        flat.append(node)
        stack += node.get("children") or []
    matched = {n["external_id"]: n["blocked_on"] for n in flat}
    # The feature is scaffolding for its matching child, and still says what it is.
    assert matched["api-t"] == "person"
    assert matched["api-f"] == "tickets"
