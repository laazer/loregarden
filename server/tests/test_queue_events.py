"""The websocket that replaced the queue dashboard's polling."""

import time
from unittest.mock import patch

import pytest
from loregarden.api.queue_events import REFRESH_INTERVAL_SECONDS
from loregarden.services.event_hub import event_hub
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
    with ws_client.websocket_connect("/ws/queue/ws-1") as socket:
        message = socket.receive_json()

    assert message["type"] == "queue_status"
    assert set(message["data"]) == {
        "active_runs",
        "queued_runs",
        "available_slots",
        "total_slots",
        "queue_length",
        "stats",
    }


def test_the_snapshot_matches_the_rest_endpoint(ws_client):
    """A client that falls back to polling must not see a different shape."""
    with ws_client.websocket_connect("/ws/queue/ws-1") as socket:
        pushed = socket.receive_json()["data"]

    polled = ws_client.get("/api/parallel/status/ws-1").json()

    assert pushed == polled


def test_an_event_pushes_a_fresh_snapshot_promptly(ws_client):
    """The point of the socket. Arriving inside the refresh interval is what
    distinguishes 'the event woke us' from 'the periodic tick fired anyway' —
    without this margin the test would pass on a socket that ignores events
    entirely."""
    with ws_client.websocket_connect("/ws/queue/ws-1") as socket:
        socket.receive_json()  # the connect snapshot

        started = time.monotonic()
        event_hub.publish("workspace:ws-1", {"type": "execution_update"})
        message = socket.receive_json()
        elapsed = time.monotonic() - started

    assert message["type"] == "queue_status"
    assert elapsed < REFRESH_INTERVAL_SECONDS / 2


def test_an_event_for_another_workspace_does_not_wake_this_socket(ws_client):
    """Every workspace's queue changes constantly; a dashboard must not be
    pushed to for a workspace nobody is looking at."""
    with ws_client.websocket_connect("/ws/queue/ws-1") as socket:
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
            with ws_client.websocket_connect("/ws/queue/ws-1?token=wrong") as socket:
                socket.receive_json()


def test_a_valid_token_is_accepted(ws_client):
    with patch("loregarden.core.auth.settings") as cfg:
        cfg.api_token = "s3cret"
        with ws_client.websocket_connect("/ws/queue/ws-1?token=s3cret") as socket:
            assert socket.receive_json()["type"] == "queue_status"


def test_disconnecting_releases_the_subscription(ws_client):
    """Otherwise every closed tab leaves a queue the hub keeps filling."""
    with ws_client.websocket_connect("/ws/queue/ws-1") as socket:
        socket.receive_json()
        assert event_hub.subscriber_count("workspace:ws-1") == 1

    deadline = time.monotonic() + 5
    while event_hub.subscriber_count("workspace:ws-1") and time.monotonic() < deadline:
        time.sleep(0.05)

    assert event_hub.subscriber_count("workspace:ws-1") == 0
