"""Running is not the same as in progress.

Every case here pins that separation: a ticket sitting in `in_progress` with no
run behind it must classify as idle, and only a live execution row may say
otherwise.
"""

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    TicketActivity,
    TicketState,
    Workspace,
)
from loregarden.services.ticket_activity import (
    classify_ticket_activity,
    summarize_ticket_status,
)
from sqlmodel import Session, select


def _workspace(session: Session) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def _ticket(session: Session, ws: Workspace, code: str, state: TicketState) -> Ticket:
    ticket = Ticket(
        external_id=code,
        workspace_id=ws.id,
        title=f"Ticket {code}",
        state=state,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _agent_run(session: Session, ticket: Ticket, status: RunStatus) -> AgentRun:
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=status,
    )
    session.add(run)
    session.commit()
    return run


def test_in_progress_with_no_run_is_idle(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "IDLE-1", TicketState.IN_PROGRESS)

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.IDLE


def test_a_live_agent_run_makes_it_running(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "RUN-1", TicketState.IN_PROGRESS)
    _agent_run(db_session, ticket, RunStatus.RUNNING)

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.RUNNING


def test_a_finished_run_does_not_keep_it_running(db_session):
    """The common case that made the board lie: the run ended, the state did not."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "DEAD-1", TicketState.IN_PROGRESS)
    _agent_run(db_session, ticket, RunStatus.SUCCEEDED)

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.IDLE


def test_a_run_parked_on_a_permission_is_awaiting(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "WAIT-1", TicketState.IN_PROGRESS)
    _agent_run(db_session, ticket, RunStatus.AWAITING_PERMISSION)

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.AWAITING


def test_a_lane_entry_waiting_its_turn_is_queued(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "Q-1", TicketState.IN_PROGRESS)
    db_session.add(QueuedRun(workspace_id=ws.id, ticket_id=ticket.id, status=QueuePosition.QUEUED))
    db_session.commit()

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.QUEUED


def test_a_promoted_lane_entry_counts_as_running(db_session):
    """It holds a slot; the dispatch is under way even before the run row lands."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "Q-2", TicketState.IN_PROGRESS)
    db_session.add(
        QueuedRun(workspace_id=ws.id, ticket_id=ticket.id, status=QueuePosition.PROMOTED)
    )
    db_session.commit()

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.RUNNING


def test_a_started_lane_entry_is_history_not_running(db_session):
    """``STARTED`` means the lane released — the trap that made blocked tickets lie."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "Q-DONE", TicketState.BLOCKED)
    db_session.add(
        QueuedRun(workspace_id=ws.id, ticket_id=ticket.id, status=QueuePosition.STARTED)
    )
    db_session.commit()

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.IDLE


def test_running_wins_over_a_queued_follow_up(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "BOTH-1", TicketState.IN_PROGRESS)
    _agent_run(db_session, ticket, RunStatus.RUNNING)
    db_session.add(QueuedRun(workspace_id=ws.id, ticket_id=ticket.id, status=QueuePosition.QUEUED))
    db_session.commit()

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.RUNNING


def test_a_running_orchestration_counts_even_without_an_agent_run(db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "ORCH-1", TicketState.IN_PROGRESS)
    db_session.add(
        OrchestrationRun(
            run_code="orch-1",
            ticket_id=ticket.id,
            workspace_id=ws.id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    db_session.commit()

    activity = classify_ticket_activity(db_session, [ticket.id])

    assert activity[ticket.id] == TicketActivity.RUNNING


def test_unknown_ids_are_absent_rather_than_guessed(db_session):
    assert classify_ticket_activity(db_session, []) == {}
    assert classify_ticket_activity(db_session, ["nope"]) == {"nope": TicketActivity.IDLE}


def test_summary_separates_the_idle_pile_from_the_running_one(db_session):
    # The seeded workspace already carries tickets, so assert on the delta.
    ws = _workspace(db_session)
    before = summarize_ticket_status(db_session, workspace_id=ws.id)

    running = _ticket(db_session, ws, "SUM-RUN", TicketState.IN_PROGRESS)
    _agent_run(db_session, running, RunStatus.RUNNING)
    for i in range(3):
        _ticket(db_session, ws, f"SUM-IDLE-{i}", TicketState.IN_PROGRESS)
    _ticket(db_session, ws, "SUM-BLOCKED", TicketState.BLOCKED)
    _ticket(db_session, ws, "SUM-DONE", TicketState.DONE)

    after = summarize_ticket_status(db_session, workspace_id=ws.id)

    assert after.running - before.running == 1
    assert after.idle - before.idle == 3
    assert after.in_progress - before.in_progress == 4
    assert after.blocked - before.blocked == 1
    assert after.done - before.done == 1
    # The two axes cover the same in-progress tickets, so they must reconcile.
    assert after.in_progress == after.running + after.idle


def test_a_blocked_ticket_is_not_counted_as_idle(db_session):
    """`idle` names the actionable pile — in progress, waiting on nobody."""
    ws = _workspace(db_session)
    before = summarize_ticket_status(db_session, workspace_id=ws.id)

    _ticket(db_session, ws, "BLK-1", TicketState.BLOCKED)

    after = summarize_ticket_status(db_session, workspace_id=ws.id)

    assert after.blocked - before.blocked == 1
    assert after.idle == before.idle


def test_the_status_summary_endpoint_answers_for_one_workspace(client, db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "API-1", TicketState.IN_PROGRESS)
    _agent_run(db_session, ticket, RunStatus.RUNNING)
    _ticket(db_session, ws, "API-2", TicketState.IN_PROGRESS)

    body = client.get("/api/tickets/status-summary", params={"workspace": ws.slug}).json()

    assert body["running"] == 1
    assert body["idle"] >= 1
    assert body["in_progress"] >= 2


def test_an_unknown_workspace_summarizes_to_zero(client):
    body = client.get("/api/tickets/status-summary", params={"workspace": "nope"}).json()

    assert body["in_progress"] == 0
    assert body["running"] == 0


def test_list_tickets_carries_the_activity_of_each_row(client, db_session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws, "LIST-1", TicketState.IN_PROGRESS)
    _agent_run(db_session, ticket, RunStatus.RUNNING)
    idle = _ticket(db_session, ws, "LIST-2", TicketState.IN_PROGRESS)

    rows = client.get("/api/tickets", params={"workspace": ws.slug}).json()
    by_id = {row["id"]: row["activity"] for row in rows}

    assert by_id[ticket.id] == "running"
    assert by_id[idle.id] == "idle"
