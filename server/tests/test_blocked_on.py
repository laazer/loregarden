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
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    AgentRetry,
    AgentRun,
    AutopilotAction,
    AutopilotEvent,
    BlockedOn,
    BlockKind,
    InitiativePlan,
    StageStatus,
    Ticket,
    TicketDependency,
    TicketState,
    WaitingReason,
    WorkItemType,
    Workspace,
)
from loregarden.services.agent_retry import agent_retry_map, spent_agent_blocks
from loregarden.services.blocked_on import (
    blocked_on_clause,
    blocked_on_map,
    root_blockers_map,
    waiting_on_map,
)
from loregarden.services.dependency_hold import override_prerequisites
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


def _at_blocked_stage(session: Session, ticket: Ticket, *, repaired: bool = False) -> Ticket:
    """A blocked stage a re-run could still clear; ``repaired`` spends its one repair turn."""
    ticket.workflow_stage_key = "implement"
    ticket.workflow_stage_status = StageStatus.BLOCKED
    session.add(ticket)
    if repaired:
        session.add(
            AgentRun(
                run_code="r-1",
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                agent_id=REPAIR_AGENT_ID,
                stage_key="implement",
            )
        )
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


def test_an_agent_declares_a_wait_on_tickets_by_naming_them():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        + json.dumps(
            {
                "status": "blocked",
                "confidence": 0.9,
                "blocked_kind": "tickets",
                "waiting_on": ["lg-milestone-that-1314", " "],
            }
        )
        + "\n<<<END_STAGE_REPORT>>>\n"
    )
    assert report is not None
    assert (report.blocked_kind, report.waiting_on) == (
        BlockKind.TICKETS,
        ["lg-milestone-that-1314"],
    )


def test_an_agent_cannot_declare_the_rollups_kind():
    """Without `waiting_on` the kind names no ticket to wait on, so it is not one."""
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
        "harness": _at_blocked_stage(
            session,
            _ticket(
                session, workspace, "harness", state=TicketState.BLOCKED, kind=BlockKind.HARNESS
            ),
        ),
        "work": _at_blocked_stage(
            session,
            _ticket(session, workspace, "work", state=TicketState.BLOCKED, kind=BlockKind.WORK),
        ),
        # Its repair turn ran and it blocked again: the kind says agent, nothing agent comes next.
        "work_repaired": _at_blocked_stage(
            session,
            _ticket(
                session, workspace, "work-repaired", state=TicketState.BLOCKED, kind=BlockKind.WORK
            ),
            repaired=True,
        ),
        "rolled_up": _ticket(
            session, workspace, "rolled-up", state=TicketState.BLOCKED, kind=BlockKind.TICKETS
        ),
        "held": _ticket(session, workspace, "held"),
        "met": _ticket(session, workspace, "met"),
        "started": _ticket(session, workspace, "started", state=TicketState.IN_PROGRESS),
        "held_mid_workflow": _ticket(
            session, workspace, "held-mid-workflow", state=TicketState.IN_PROGRESS
        ),
        "harness_and_held": _ticket(
            session,
            workspace,
            "harness-and-held",
            state=TicketState.BLOCKED,
            kind=BlockKind.HARNESS,
        ),
        "parked": _ticket(session, workspace, "parked", state=TicketState.PARKED),
        "done": done,
        "open_prereq": open_prereq,
    }
    _depends(session, board["held"], open_prereq)
    _depends(session, board["met"], done)
    # Started by name past its prerequisite, which is recorded: running, not held.
    _depends(session, board["started"], open_prereq)
    override_prerequisites(session, board["started"], stage_key="implement", actor="operator")
    # Every dispatch holds an in-progress ticket too (lg-milestone-that-1315).
    _depends(session, board["held_mid_workflow"], open_prereq)
    # A repair would be held at its dispatch, so it is not the agent's to clear.
    _depends(session, board["harness_and_held"], open_prereq)
    return board


EXPECTED = {
    "decision": BlockedOn.PERSON,
    "human": BlockedOn.PERSON,
    "unclassified": BlockedOn.PERSON,
    "harness": BlockedOn.AGENT,
    "work": BlockedOn.AGENT,
    "work_repaired": BlockedOn.PERSON,
    "rolled_up": BlockedOn.TICKETS,
    "held": BlockedOn.TICKETS,
    "held_mid_workflow": BlockedOn.TICKETS,
    "harness_and_held": BlockedOn.TICKETS,
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
            select(Ticket.id).where(
                col(Ticket.id).in_(ids), blocked_on_clause([wanted], spent_agent_blocks(session))
            )
        ).all()
    )
    derived = blocked_on_map(session, list(board.values()))
    assert matched == {tid for tid, answer in derived.items() if answer is wanted}
    assert matched  # every answer has a ticket on this board


def test_no_filter_value_matches_nothing(session, board):
    assert session.exec(select(Ticket.id).where(blocked_on_clause([], []))).all() == []


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


def test_a_held_ticket_leads_with_the_root_of_its_chain(session, workspace):
    """A waits on B, B waits on C, and C is the one a person can move."""
    root = _ticket(session, workspace, "c", state=TicketState.BLOCKED, kind=BlockKind.DECISION)
    middle = _ticket(session, workspace, "b", state=TicketState.IN_PROGRESS)
    sibling_root = _ticket(session, workspace, "d", state=TicketState.IN_PROGRESS)
    held = _ticket(session, workspace, "a")
    _depends(session, held, middle)
    _depends(session, middle, root)
    _depends(session, held, sibling_root)

    roots = root_blockers_map(session, [held, middle, root])

    assert {(r.external_id, r.blocked_on, r.workspace_slug) for r in roots[held.id]} == {
        ("c", BlockedOn.PERSON, "proj"),
        ("d", None, "proj"),
    }
    assert [r.external_id for r in roots[middle.id]] == ["c"]
    assert root.id not in roots


def test_a_held_ticket_names_its_unfinished_prerequisites(session, board):
    refs = waiting_on_map(session, [board["held"], board["met"]])
    assert [(r.external_id, r.reason) for r in refs[board["held"].id]] == [
        ("open-prereq", WaitingReason.PREREQUISITE)
    ]
    assert board["met"].id not in refs


# --- agent_retry: who starts the re-run ----------------------------------------------


def _autopilot_over(session: Session, root: Ticket) -> InitiativePlan:
    plan = InitiativePlan(initiative_id=root.id, autopilot=True)
    session.add(plan)
    session.commit()
    return plan


def test_a_retryable_block_waits_for_one_run_when_nothing_will_start_it(session, board):
    retry = agent_retry_map(session, list(board.values()))
    assert retry == {
        board["harness"].id: AgentRetry.ON_REQUEST,
        board["work"].id: AgentRetry.ON_REQUEST,
    }


def test_an_autopilot_covering_it_retries_it_until_it_has(session, workspace):
    milestone = _ticket(session, workspace, "m", work_item_type=WorkItemType.MILESTONE)
    task = _ticket(session, workspace, "t", parent=milestone)
    _block(session, task, BlockKind.HARNESS)
    _at_blocked_stage(session, task)
    _autopilot_over(session, milestone)

    assert agent_retry_map(session, [task]) == {task.id: AgentRetry.AUTOMATIC}

    session.add(
        AutopilotEvent(
            initiative_id=milestone.id, action=AutopilotAction.RETRIED, ticket_id=task.id
        )
    )
    session.commit()
    # It retries once per time it is turned on; the next re-run is a person's click.
    assert agent_retry_map(session, [task]) == {task.id: AgentRetry.ON_REQUEST}


def test_a_spent_block_has_no_retry(session, board):
    assert board["work_repaired"].id not in agent_retry_map(session, [board["work_repaired"]])
    # With no blocked stage there is nothing to re-run either.
    assert set(spent_agent_blocks(session)) == {
        board["work_repaired"].id,
        board["harness_and_held"].id,
    }


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


def test_the_summary_says_who_starts_the_re_run(client: TestClient, db_session: Session):
    ws = db_session.exec(select(Workspace)).first()
    assert ws is not None
    held_prereq = _ticket(db_session, ws, "api-prereq", state=TicketState.IN_PROGRESS)
    _at_blocked_stage(
        db_session, _ticket(db_session, ws, "api-a", state=TicketState.BLOCKED, kind=BlockKind.WORK)
    )
    held = _at_blocked_stage(
        db_session, _ticket(db_session, ws, "api-h", state=TicketState.BLOCKED, kind=BlockKind.WORK)
    )
    _depends(db_session, held, held_prereq)

    rows = {
        r["external_id"]: (r["blocked_on"], r["agent_retry"])
        for r in client.get(f"/api/tickets?workspace={ws.slug}&state=blocked").json()
    }
    assert rows["api-a"] == ("agent", "on_request")
    assert rows["api-h"] == ("tickets", None)
