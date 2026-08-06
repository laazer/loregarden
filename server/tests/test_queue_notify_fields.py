"""Queue websocket events carry ticket / stage labels for the inbox."""

from unittest.mock import MagicMock, patch

from loregarden.services.parallel_queue import run_notify_fields
from loregarden.websocket_events import emit_queue_promoted, emit_run_completed


def test_emit_run_completed_includes_ticket_and_step():
    with patch("loregarden.websocket_events.event_hub") as hub:
        emit_run_completed(
            run_id="run-1",
            status="succeeded",
            ticket_id="ticket-1",
            ticket_title="Bootstrap vertical slice",
            stage_key="implement",
            agent_id="backend_implementer",
        )

    hub.publish.assert_called_once()
    topic, payload = hub.publish.call_args[0]
    assert topic == "queue"
    assert payload["type"] == "run_completed"
    assert payload["data"] == {
        "runId": "run-1",
        "status": "succeeded",
        "ticketId": "ticket-1",
        "ticketTitle": "Bootstrap vertical slice",
        "stageKey": "implement",
        "agentId": "backend_implementer",
    }


def test_emit_queue_promoted_includes_ticket_and_step():
    with patch("loregarden.websocket_events.event_hub") as hub:
        emit_queue_promoted(
            run_id="run-2",
            slot_number=2,
            ticket_id="ticket-2",
            ticket_title="Fix queue",
            stage_key="plan",
            agent_id="planner",
        )

    payload = hub.publish.call_args[0][1]
    assert payload["type"] == "queue_promoted"
    assert payload["data"]["slotNumber"] == 2
    assert payload["data"]["ticketTitle"] == "Fix queue"
    assert payload["data"]["stageKey"] == "plan"


def test_run_notify_fields_reads_ticket_title():
    ticket = MagicMock(title="Bootstrap vertical slice")
    run = MagicMock(
        ticket_id="ticket-1",
        stage_key="implement",
        agent_id="backend_implementer",
    )
    session = MagicMock()
    session.get.return_value = ticket

    assert run_notify_fields(session, run) == {
        "ticket_id": "ticket-1",
        "ticket_title": "Bootstrap vertical slice",
        "stage_key": "implement",
        "agent_id": "backend_implementer",
    }
