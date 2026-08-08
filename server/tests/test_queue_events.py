"""The websocket that replaced the queue dashboard's polling."""

import time
from unittest.mock import patch

import pytest
from loregarden.api.queue_events import REFRESH_INTERVAL_SECONDS
from loregarden.services.event_hub import event_hub
from loregarden.websocket_events import QUEUE_TOPIC
from starlette.websockets import WebSocketDisconnect


@pytest.fixture(name="ws_client")
def ws_client_fixture(client, isolated_db):
    """The queue socket opens its own sessions, so it needs the test engine.

    A session held for the life of the socket would cache its first read and
    every later snapshot would repeat it — so the handler deliberately does not
    take the `get_session` dependency the rest of the API uses, and the engine
    is the seam instead.
    """
    with patch("loregarden.api.queue_events.engine", isolated_db):
        yield client


def test_connecting_delivers_a_snapshot_without_being_asked(ws_client):
    """First paint must not wait for a state change; an idle queue still has to
    render, and it renders from this."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        message = socket.receive_json()

    assert message["type"] == "queue_status"
    assert set(message["data"]) == {
        "active_runs",
        "queued_runs",
        # Each slot with what runs in it and what waits behind it.
        "lanes",
        "available_slots",
        "total_slots",
        "queue_length",
        "estimated_clear_seconds",
        # Projected wait before the last queued entry starts — forward-looking,
        # unlike stats.longest_wait_seconds.
        "estimated_wait_seconds",
        "stats",
    }


def test_an_idle_queue_reports_no_clear_estimate(ws_client):
    """With no completed runs there is nothing to estimate from, and the
    dashboard must be told that rather than handed a made-up number."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        snapshot = socket.receive_json()["data"]

    assert snapshot["estimated_clear_seconds"] is None


def test_the_snapshot_matches_the_rest_endpoint(ws_client):
    """A client that falls back to polling must not see a different shape."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        pushed = socket.receive_json()["data"]

    polled = ws_client.get("/api/parallel/status").json()

    assert pushed == polled


def test_an_event_pushes_a_fresh_snapshot_promptly(ws_client):
    """The point of the socket. Arriving inside the refresh interval is what
    distinguishes 'the event woke us' from 'the periodic tick fired anyway' —
    without this margin the test would pass on a socket that ignores events
    entirely."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        socket.receive_json()  # the connect snapshot

        started = time.monotonic()
        event_hub.publish(QUEUE_TOPIC, {"type": "execution_update"})
        message = socket.receive_json()
        elapsed = time.monotonic() - started

    assert message["type"] == "queue_status"
    assert elapsed < REFRESH_INTERVAL_SECONDS / 2


def test_a_notifiable_event_is_forwarded_alongside_the_snapshot(ws_client):
    """The snapshot says what the queue looks like now; it cannot say that a run
    just finished. Toasts need the event itself, so both go out."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        socket.receive_json()  # the connect snapshot

        event_hub.publish(
            QUEUE_TOPIC,
            {"type": "run_completed", "data": {"runId": "run-1", "status": "succeeded"}},
        )
        forwarded = socket.receive_json()
        snapshot = socket.receive_json()

    assert forwarded["type"] == "queue_event"
    assert forwarded["data"]["type"] == "run_completed"
    assert forwarded["data"]["data"]["runId"] == "run-1"
    assert snapshot["type"] == "queue_status"


def test_execution_update_produces_a_snapshot_but_no_toast(ws_client):
    """'The queue changed' is what the snapshot already conveys. Forwarding it
    too would fire a toast on every reorder."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        socket.receive_json()  # the connect snapshot

        event_hub.publish(QUEUE_TOPIC, {"type": "execution_update"})
        message = socket.receive_json()

    assert message["type"] == "queue_status"


def test_a_burst_coalesces_to_one_snapshot_and_one_toast_per_run(ws_client):
    """A completion promotes the next run, which updates the queue. That is one
    thing happening, so it must not stack duplicate toasts or snapshots."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        socket.receive_json()  # the connect snapshot

        for _ in range(3):
            event_hub.publish(
                QUEUE_TOPIC,
                {"type": "queue_promoted", "data": {"runId": "run-1", "slotNumber": 1}},
            )
        event_hub.publish(QUEUE_TOPIC, {"type": "execution_update"})

        started = time.monotonic()
        messages = []
        # Everything the burst produces arrives well inside the periodic tick;
        # whatever has not arrived by then is not part of the burst.
        while time.monotonic() - started < REFRESH_INTERVAL_SECONDS / 2:
            messages.append(socket.receive_json())
            if len(messages) >= 2:
                break

    assert [m["type"] for m in messages] == ["queue_event", "queue_status"]
    assert messages[0]["data"]["data"]["runId"] == "run-1"


def test_an_event_for_another_workspace_does_not_wake_this_socket(ws_client):
    """Every workspace's queue changes constantly; a dashboard must not be
    pushed to for a workspace nobody is looking at."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        socket.receive_json()  # the connect snapshot

        started = time.monotonic()
        event_hub.publish("workspace:ws-2", {"type": "execution_update"})
        socket.receive_json()
        elapsed = time.monotonic() - started

    # The next snapshot came from the periodic tick, not from the ws-2 event.
    assert elapsed >= REFRESH_INTERVAL_SECONDS / 2


def test_the_socket_enforces_the_api_token(ws_client):
    """TokenAuthMiddleware extends BaseHTTPMiddleware and never sees a
    websocket scope, so the handler must check for itself."""
    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = "s3cret"
        with pytest.raises(WebSocketDisconnect):
            with ws_client.websocket_connect("/ws/queue?token=wrong") as socket:
                socket.receive_json()


def test_a_valid_token_is_accepted(ws_client):
    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = "s3cret"
        with ws_client.websocket_connect("/ws/queue?token=s3cret") as socket:
            assert socket.receive_json()["type"] == "queue_status"


def test_disconnecting_releases_the_subscription(ws_client):
    """Otherwise every closed tab leaves a queue the hub keeps filling."""
    with ws_client.websocket_connect("/ws/queue") as socket:
        socket.receive_json()
        assert event_hub.subscriber_count(QUEUE_TOPIC) == 1

    deadline = time.monotonic() + 5
    while event_hub.subscriber_count(QUEUE_TOPIC) and time.monotonic() < deadline:
        time.sleep(0.05)

    assert event_hub.subscriber_count(QUEUE_TOPIC) == 0
