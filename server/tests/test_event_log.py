"""The event log is a queryable audit trail, decided in lg-workflow-integrity-567.

Eight services wrote to `domain_events` for two months and nothing read it. The
deciding measurement, taken before choosing: it is the ONLY table that records
transitions. `tickets.state` holds the current value and a revision counter, not
a log; a stage's status lives in `stages_json` and is overwritten in place. So
the four TRANSITION_EVENTS are not derivable from anywhere, and deleting the
writes would destroy the only record of how anything got where it is.

What made it unusable was never the writes — it was that `list_recent` took no
filters, so the only answerable question was "the last N events installation
wide".
"""

from datetime import datetime, timedelta, timezone

from loregarden.core.event_bus import TRANSITION_EVENTS, event_bus
from loregarden.models.domain import (
    EventType,
    Ticket,
    TicketState,
    Workspace,
)
from sqlmodel import Session, select
from tests.factories import make_ticket


def _ticket(db_session: Session, external_id: str) -> Ticket:
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    return make_ticket(
        db_session,
        workspace_id=ws.id,
        external_id=external_id,
        title=external_id,
        state=TicketState.IN_PROGRESS,
    )


def test_the_bus_no_longer_advertises_a_subscription_model():
    """AC3-adjacent. `subscribe` had zero callers in two months, so the handler
    list and the fan-out inside publish were dead code implying that publishing
    notifies something. Removed rather than left standing."""
    assert not hasattr(event_bus, "subscribe")
    assert not hasattr(event_bus, "_subscribers")


def test_events_filter_by_ticket(db_session: Session):
    """AC2. This is the question the log could not answer for two months."""
    first, second = _ticket(db_session, "ev-a"), _ticket(db_session, "ev-b")
    event_bus.publish(db_session, EventType.TICKET_STATE_CHANGED, ticket_id=first.id)
    event_bus.publish(db_session, EventType.TICKET_STATE_CHANGED, ticket_id=second.id)

    found = event_bus.list_recent(db_session, ticket_id=first.id)
    assert [e.ticket_id for e in found] == [first.id]


def test_events_filter_by_type(db_session: Session):
    ticket = _ticket(db_session, "ev-type")
    event_bus.publish(db_session, EventType.TICKET_STATE_CHANGED, ticket_id=ticket.id)
    event_bus.publish(db_session, EventType.STAGE_STARTED, ticket_id=ticket.id)

    found = event_bus.list_recent(db_session, types=(EventType.STAGE_STARTED,))
    assert {e.type for e in found} == {EventType.STAGE_STARTED}


def test_events_filter_by_time_window(db_session: Session):
    ticket = _ticket(db_session, "ev-window")
    event_bus.publish(db_session, EventType.STAGE_STARTED, ticket_id=ticket.id)

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert event_bus.list_recent(db_session, since=future) == []
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert event_bus.list_recent(db_session, since=past) != []


def test_filters_compose(db_session: Session):
    first, second = _ticket(db_session, "ev-c1"), _ticket(db_session, "ev-c2")
    event_bus.publish(db_session, EventType.STAGE_STARTED, ticket_id=first.id)
    event_bus.publish(db_session, EventType.TICKET_STATE_CHANGED, ticket_id=first.id)
    event_bus.publish(db_session, EventType.STAGE_STARTED, ticket_id=second.id)

    found = event_bus.list_recent(db_session, ticket_id=first.id, types=(EventType.STAGE_STARTED,))
    assert len(found) == 1


def test_unfiltered_still_returns_everything(db_session: Session):
    """The control: filters that were always applied would break the old caller."""
    ticket = _ticket(db_session, "ev-all")
    for _ in range(3):
        event_bus.publish(db_session, EventType.STAGE_STARTED, ticket_id=ticket.id)
    assert len(event_bus.list_recent(db_session)) >= 3


# --- the consumer -----------------------------------------------------------


def test_ticket_history_is_oldest_first(db_session: Session):
    """A history read newest-first is a history read backwards."""
    ticket = _ticket(db_session, "ev-order")
    for stage in ("plan", "implement", "verify"):
        event_bus.publish(
            db_session,
            EventType.STAGE_STARTED,
            ticket_id=ticket.id,
            payload={"stage_key": stage},
        )

    history = event_bus.ticket_history(db_session, ticket.id)
    assert [e.created_at for e in history] == sorted(e.created_at for e in history)


def test_ticket_history_excludes_events_that_describe_rows_you_can_already_see(
    db_session: Session,
):
    """`ArtifactCreated` and the run/approval events restate rows the reader
    already has. Mixing them in buries the four kinds that are the only record
    of anything — 2585 ArtifactCreated rows against 427 TicketStateChanged."""
    ticket = _ticket(db_session, "ev-filtered")
    event_bus.publish(db_session, EventType.TICKET_STATE_CHANGED, ticket_id=ticket.id)
    event_bus.publish(db_session, EventType.ARTIFACT_CREATED, ticket_id=ticket.id)
    event_bus.publish(db_session, EventType.AGENT_RUN_STARTED, ticket_id=ticket.id)

    history = event_bus.ticket_history(db_session, ticket.id)
    assert {e.type for e in history} == {EventType.TICKET_STATE_CHANGED}
    assert all(e.type in TRANSITION_EVENTS for e in history)


def test_the_history_endpoint_serves_one_ticket(db_session: Session, client):
    ticket = _ticket(db_session, "ev-api")
    event_bus.publish(
        db_session,
        EventType.STAGE_SKIPPED,
        ticket_id=ticket.id,
        payload={"stage_key": "ui-design", "reason": "backend only"},
    )

    response = client.get(f"/api/events/ticket/{ticket.id}/history")
    assert response.status_code == 200
    body = response.json()
    assert [item["type"] for item in body] == ["StageSkipped"]
    assert body[0]["payload"]["reason"] == "backend only"


def test_the_history_endpoint_404s_on_an_unknown_ticket(client):
    assert client.get("/api/events/ticket/not-a-ticket/history").status_code == 404


def test_stage_skipped_now_reaches_a_reader(db_session: Session):
    """PR #203 added StageSkipped and it went nowhere — one row in the live
    database and no query that could find it. It is a transition event, so the
    ticket history is where it lands."""
    assert EventType.STAGE_SKIPPED in TRANSITION_EVENTS
    ticket = _ticket(db_session, "ev-skipped")
    event_bus.publish(db_session, EventType.STAGE_SKIPPED, ticket_id=ticket.id)
    assert len(event_bus.ticket_history(db_session, ticket.id)) == 1
