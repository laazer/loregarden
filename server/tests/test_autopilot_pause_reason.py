"""Why an autopilot stopped itself is told as of now, not as of when it stopped.

The breaker writes `paused_reason` once, naming the tickets blocked then. On
10-12 it still named 782 and on 10-27 1607, both long done, so the operator was
sent to look at work that needed nothing.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    InitiativePlan,
    Ticket,
    TicketState,
)
from loregarden.services.initiative_autopilot import (
    BREAKER_LIMIT,
    pause_reason_now,
    run_autopilot,
    set_autopilot,
)
from loregarden.services.initiative_plan_service import plan_view
from sqlmodel import Session
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _initiative,
    _item,
    _milestone,
    queue_fixture,
    workspaces_fixture,
)


def _tripped(session: Session, workspaces) -> tuple[Ticket, list[Ticket]]:
    here, _ = workspaces
    initiative = _initiative(session)
    m = _milestone(session, here, "M", initiative)
    set_autopilot(session, initiative.id, AutopilotUpdate(enabled=True), actor="t")
    broken = []
    for i in range(BREAKER_LIMIT):
        t = _item(session, here, m, f"broken-{i}", state=TicketState.BLOCKED)
        session.add(
            AutopilotEvent(
                initiative_id=initiative.id, action=AutopilotAction.DISPATCHED, ticket_id=t.id
            )
        )
        broken.append(t)
    session.commit()
    run_autopilot(session, initiative.id, now=NOW)
    return initiative, broken


def _unblock(session: Session, ticket: Ticket) -> None:
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()


def test_while_the_cause_holds_the_reason_is_as_written(db_session, workspaces, queue):
    initiative, _ = _tripped(db_session, workspaces)
    plan = db_session.get(InitiativePlan, initiative.id)

    assert pause_reason_now(db_session, plan) == plan.paused_reason


def test_once_its_tickets_are_unblocked_the_reason_says_so(db_session, workspaces, queue):
    initiative, broken = _tripped(db_session, workspaces)
    for t in broken:
        _unblock(db_session, t)

    reason = plan_view(db_session, initiative.id, now=NOW).autopilot.paused_reason

    assert "none of the tickets it started is blocked any more" in reason, reason


def test_when_some_stay_blocked_it_names_only_those(db_session, workspaces, queue):
    initiative, broken = _tripped(db_session, workspaces)
    _unblock(db_session, broken[0])

    reason = plan_view(db_session, initiative.id, now=NOW).autopilot.paused_reason

    tail = reason.split("Since then", 1)[1]
    assert broken[1].external_id in tail and broken[2].external_id in tail
    assert broken[0].external_id not in tail


def test_a_plan_that_never_stopped_itself_has_no_reason(db_session, workspaces, queue):
    here, _ = workspaces
    initiative = _initiative(db_session)
    _milestone(db_session, here, "M", initiative)

    assert pause_reason_now(db_session, db_session.get(InitiativePlan, initiative.id)) == ""
