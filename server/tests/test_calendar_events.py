"""Calendar events projection for chat UI primitives."""

from fastapi.testclient import TestClient


def test_calendar_events_endpoint(client: TestClient):
    res = client.get("/api/workspaces/loregarden/calendar/events")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_calendar_events_unknown_workspace(client: TestClient):
    res = client.get("/api/workspaces/no-such-ws/calendar/events")
    assert res.status_code == 404
