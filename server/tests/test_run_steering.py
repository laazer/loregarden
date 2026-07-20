"""Steering a run that is already going."""

import json

import pytest
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner, _LoopState
from loregarden.models.domain import AgentRun, RunMessage, RunStatus, Ticket
from loregarden.services.run_steering import (
    MAX_MESSAGE_CHARS,
    pending_messages,
    queue_message,
    steer_refusal,
)
from sqlmodel import Session, select


def _run(session: Session, ticket: Ticket, *, agent_id="planner", status=RunStatus.RUNNING):
    run = AgentRun(
        run_code="run_steer",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key="plan",
        status=status,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_running_claude_agent_accepts_a_message(db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)

    assert steer_refusal(run) == ""
    message = queue_message(db_session, run, "  use the existing helper  ")
    assert message.content == "use the existing helper"
    assert message.delivered_at is None


def test_a_finished_run_cannot_be_steered(db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket, status=RunStatus.SUCCEEDED)

    assert "nothing to steer" in steer_refusal(run)
    with pytest.raises(ValueError, match="nothing to steer"):
        queue_message(db_session, run, "too late")


def test_a_cursor_agent_is_refused_with_the_reason(db_session: Session):
    """cursor-agent exposes stream-json output but no --input-format, so there
    is no channel to write into a run it is executing. Saying so beats
    accepting a message that would never arrive."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket, agent_id="backend_implementer")

    refusal = steer_refusal(run)
    assert "cursor" in refusal
    assert "cannot receive input" in refusal
    with pytest.raises(ValueError):
        queue_message(db_session, run, "stop touching that file")


def test_an_empty_message_is_rejected(db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    with pytest.raises(ValueError, match="empty"):
        queue_message(db_session, run, "   ")


def test_a_long_message_is_capped(db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    message = queue_message(db_session, run, "x" * (MAX_MESSAGE_CHARS * 2))
    assert len(message.content) == MAX_MESSAGE_CHARS


class _FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.written.append(payload)

    def flush(self) -> None:
        pass


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()


def test_delivery_writes_the_message_into_the_agents_stdin(db_session: Session):
    """The whole feature is this write. Everything else is bookkeeping."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    queue_message(db_session, run, "prefer the existing seam")

    proc = _FakeProc()
    state = _LoopState(stdout_lines=[], session_id="sess-1", last_persist=0.0)
    PermissionBridgeRunner(db_session)._deliver_steer_messages(
        run_id=run.id, proc=proc, state=state, streamer=None
    )

    assert len(proc.stdin.written) == 1
    payload = json.loads(proc.stdin.written[0].decode())
    assert payload["type"] == "user"
    assert payload["message"]["content"] == "prefer the existing seam"
    # Bound to the live session so the CLI continues the same conversation
    # rather than starting a new one.
    assert payload["session_id"] == "sess-1"


def test_a_delivered_message_is_not_sent_twice(db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    queue_message(db_session, run, "once only")

    bridge = PermissionBridgeRunner(db_session)
    proc = _FakeProc()
    state = _LoopState(stdout_lines=[], session_id="s", last_persist=0.0)
    bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)
    # Reset the poll clock so the second call is not simply throttled out.
    state.last_steer_poll = 0.0
    bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)

    assert len(proc.stdin.written) == 1
    assert pending_messages(db_session, run.id) == []


def test_delivery_is_throttled(db_session: Session):
    """The run loop spins fast; polling every pass would swamp the DB for a
    channel used a handful of times an hour."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    bridge = PermissionBridgeRunner(db_session)
    proc = _FakeProc()
    state = _LoopState(stdout_lines=[], session_id="s", last_persist=0.0)

    bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)
    first_poll = state.last_steer_poll
    queue_message(db_session, run, "sent right after a poll")
    bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)

    assert state.last_steer_poll == first_poll
    assert proc.stdin.written == []


def test_a_broken_stdin_does_not_kill_the_run(db_session: Session):
    """Steering is a side channel. A failed write must leave the message
    undelivered — which the UI reports — not take down a working run."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    queue_message(db_session, run, "this write will fail")

    class _ExplodingProc:
        class stdin:  # noqa: N801 - stand-in for a subprocess pipe
            @staticmethod
            def write(_payload):
                raise BrokenPipeError("agent went away")

            @staticmethod
            def flush():
                pass

    state = _LoopState(stdout_lines=[], session_id="s", last_persist=0.0)
    PermissionBridgeRunner(db_session)._deliver_steer_messages(
        run_id=run.id, proc=_ExplodingProc(), state=state, streamer=None
    )

    still_pending = db_session.exec(
        select(RunMessage).where(RunMessage.run_id == run.id, RunMessage.delivered_at.is_(None))
    ).all()
    assert len(still_pending) == 1


def test_the_api_reports_why_a_run_cannot_be_steered(client, db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket, agent_id="backend_implementer")

    listing = client.get(f"/api/runs/{run.id}/messages")
    assert listing.status_code == 200
    assert "cannot receive input" in listing.json()["refusal"]

    rejected = client.post(f"/api/runs/{run.id}/messages", json={"content": "hello"})
    assert rejected.status_code == 409
    assert "cursor" in rejected.json()["detail"]


def test_the_api_round_trips_a_message(client, db_session: Session):
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)

    created = client.post(f"/api/runs/{run.id}/messages", json={"content": "check the migration"})
    assert created.status_code == 200, created.text
    assert created.json()["delivered_at"] is None

    listing = client.get(f"/api/runs/{run.id}/messages").json()
    assert listing["refusal"] == ""
    assert [m["content"] for m in listing["messages"]] == ["check the migration"]


def test_messages_for_an_unknown_run_are_404(client):
    assert client.get("/api/runs/nope/messages").status_code == 404
    assert client.post("/api/runs/nope/messages", json={"content": "x"}).status_code == 404
