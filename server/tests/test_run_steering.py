"""Steering a run that is already going."""

import json
from datetime import datetime, timezone
from unittest import mock

import pytest
from loregarden.agents.executors import permission_bridge
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner, _LoopState
from loregarden.models.domain import (
    AgentRun,
    AgentTransport,
    RunMessage,
    RunStatus,
    Ticket,
)
from loregarden.services.run_steering import (
    MAX_MESSAGE_CHARS,
    POLL_INTERVAL_SECONDS,
    pending_messages,
    queue_message,
    steer_refusal,
)
from sqlmodel import Session, select


class _FakeClock:
    """A clock the test moves, so timing assertions state intent not luck."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


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
    channel used a handful of times an hour.

    The clock is driven rather than raced. This test used to make two calls
    back-to-back and assert the second was throttled, which encoded "a database
    write takes under a second" — true on an idle machine and not under
    `-n auto`, where it failed twice (lg-workflow-integrity-654). Driving the
    clock also lets it assert the *other* half, which the timing version could
    not: that the message does get through once the window has passed.
    """
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    bridge = PermissionBridgeRunner(db_session)
    proc = _FakeProc()
    state = _LoopState(stdout_lines=[], session_id="s", last_persist=0.0)
    clock = _FakeClock(1_000.0)

    with mock.patch.object(permission_bridge, "_now", clock):
        bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)
        first_poll = state.last_steer_poll
        assert first_poll == 1_000.0

        queue_message(db_session, run, "sent right after a poll")
        bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)

        # Inside the window: no poll, nothing delivered, however long the write
        # above actually took.
        assert state.last_steer_poll == first_poll
        assert proc.stdin.written == []

        clock.advance(POLL_INTERVAL_SECONDS + 0.01)
        bridge._deliver_steer_messages(run_id=run.id, proc=proc, state=state, streamer=None)

    # Past the window: the throttle is a delay, not a drop.
    assert state.last_steer_poll > first_poll
    assert proc.stdin.written, "the queued message never arrived after the window"


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


# --- lg-durable-remote-336: a detached run has no stdin (AC34) ---------------
#
# This closes a pre-existing void rather than adding a limit: `print_mode.py`
# never drains `RunMessage` — only `permission_bridge.py` does — so a claude
# print-mode run has always shown an input that goes nowhere. Detach makes
# print mode the path this ticket ships, so the composer would be inviting text
# into a void on precisely the runs being created.
#
# Scope is the refusal string only. No delivery mechanism is added, and
# `STEERABLE_ADAPTERS` is unchanged.


@pytest.mark.parametrize(
    ("transport", "word"), [(AgentTransport.TMUX, "tmux"), (AgentTransport.FILE, "file")]
)
def test_a_detached_run_says_it_has_no_stdin_to_write_into(db_session: Session, transport, word):
    """AC34, verbatim. The run reports itself steerable on every other check."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    assert steer_refusal(run) == "", "precondition: this run is otherwise steerable"
    run.agent_transport = transport
    db_session.add(run)
    db_session.commit()

    assert steer_refusal(run) == (
        f"This run is detached on {word}, which has no stdin to write into, "
        "so it cannot be steered."
    )


def test_the_detached_refusal_sits_after_the_stop_and_chat_checks(db_session: Session):
    """AC34's placement, from the other side.

    A stop already in flight outranks anything queued behind it, and a
    workspace-scoped chat run is steered by replying in the chat. Both answers
    must survive a transport being set, or an operator who pressed stop is told
    about stdin instead.
    """
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    run.agent_transport = AgentTransport.TMUX
    run.cancel_requested_at = datetime.now(timezone.utc)
    db_session.add(run)
    db_session.commit()

    assert steer_refusal(run) == "Run is stopping, so it cannot be steered."


def test_the_detached_refusal_sits_before_the_adapter_check(db_session: Session):
    """AC34's placement, the half that actually fires.

    A claude run reports itself steerable today, so a branch placed after the
    adapter check would never be reached on the runs this ticket creates.
    """
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    run.agent_transport = AgentTransport.FILE
    db_session.add(run)
    db_session.commit()

    refusal = steer_refusal(run)

    assert "stdin" in refusal
    assert "cannot receive input once started" not in refusal


def test_a_run_with_no_transport_keeps_the_answer_it_has_today(db_session: Session):
    """1,449 rows have no transport. The refusal must not grow for them."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)

    assert steer_refusal(run) == ""


def test_the_steerable_adapters_set_is_unchanged(db_session: Session):
    """AC34's scope line: the refusal string only, no delivery mechanism."""
    from loregarden.services.run_steering import STEERABLE_ADAPTERS

    assert STEERABLE_ADAPTERS == frozenset({"claude"})


def test_queueing_a_message_for_a_detached_run_is_refused_with_that_reason(
    db_session: Session,
):
    """`queue_message` raises the refusal, so the POST cannot bypass the UI."""
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    run.agent_transport = AgentTransport.TMUX
    db_session.add(run)
    db_session.commit()

    with pytest.raises(ValueError, match="no stdin to write into"):
        queue_message(db_session, run, "please try the other helper")


def test_a_detached_run_that_has_already_finished_still_says_so(db_session: Session):
    """AC34's placement from above: the status check outranks the transport.

    `steer_refusal`'s first real branch is "there is nothing to steer", and the
    1,449 settled rows this ticket will eventually add a transport to are all
    past it. A branch written at the top of the function — the obvious place to
    put a new one — passes every other AC34 case in this module and tells an
    operator looking at a finished run about stdin instead of about the run
    being over.
    """
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket, status=RunStatus.SUCCEEDED)
    run.agent_transport = AgentTransport.TMUX
    db_session.add(run)
    db_session.commit()

    assert steer_refusal(run) == "Run is succeeded, so there is nothing to steer."


def test_a_detached_chat_run_is_still_pointed_back_at_the_chat(db_session: Session):
    """AC34 pins the branch AFTER the `ticket_id` check, and this is that half.

    A workspace-scoped chat run has no ticket, and the answer an operator needs
    is where to type instead — not a fact about the transport, which does not
    tell them what to do next. The sibling case above covers the
    `cancel_requested_at` check; between them the two checks AC34 names as
    senior are both asserted, rather than one asserted and one described in a
    docstring.
    """
    ticket = db_session.exec(select(Ticket)).first()
    run = _run(db_session, ticket)
    run.ticket_id = None
    run.agent_transport = AgentTransport.FILE
    db_session.add(run)
    db_session.commit()

    assert steer_refusal(run) == (
        "Workspace-scoped chat runs are steered by replying in the chat, not here."
    )
