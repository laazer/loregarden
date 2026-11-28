"""GET /api/runs/{run_id}/log — the source for the run-log modal."""

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import AgentRun, AgentTransport, Artifact, RunStatus, Ticket
from sqlmodel import Session, select


def _seed_run(session: Session, *, status: RunStatus = RunStatus.SUCCEEDED) -> AgentRun:
    ticket = session.exec(select(Ticket)).first()
    assert ticket
    run = AgentRun(
        run_code="run_modal1",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        skill_name="run_tests",
        stage_key="testing",
        status=status,
        command="claude -p 'run the tests'",
        stdout='{"type":"result","result":"raw stream-json that must not be served"}',
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _seed_log(session: Session, run: AgentRun, lines: list[dict], live: str | None = None) -> None:
    session.add(
        Artifact(
            ticket_id=run.ticket_id,
            run_id=run.id,
            kind="log",
            title="Run log",
            content_json=json.dumps({"lines": lines, "live": live}),
        )
    )
    session.commit()


def test_run_log_returns_rendered_lines(client: TestClient, db_session: Session):
    run = _seed_run(db_session)
    _seed_log(
        db_session,
        run,
        [
            {"time": "20:57:14", "tag": "RUN", "text": "static_qa invoked"},
            {"time": "20:57:20", "tag": "OUT", "text": "3 passed"},
        ],
        live="still working",
    )

    res = client.get(f"/api/runs/{run.id}/log")
    assert res.status_code == 200
    body = res.json()

    assert [line["text"] for line in body["lines"]] == ["static_qa invoked", "3 passed"]
    assert body["live"] == "still working"
    assert body["run_code"] == "run_modal1"
    assert body["agent_id"] == "static_qa"
    assert body["stage_key"] == "testing"
    assert body["command"] == "claude -p 'run the tests'"


def test_run_log_does_not_serve_raw_stdout(client: TestClient, db_session: Session):
    """stdout is the unbounded raw transcript — the modal must never receive it."""
    run = _seed_run(db_session)
    _seed_log(db_session, run, [{"time": "20:57:14", "tag": "RUN", "text": "hello"}])

    body = client.get(f"/api/runs/{run.id}/log").json()

    assert "stdout" not in body
    assert "raw stream-json" not in json.dumps(body)


def test_run_log_without_artifact_returns_empty_lines(client: TestClient, db_session: Session):
    """Runs predating the log streamer still resolve, so the modal shows identity."""
    run = _seed_run(db_session)

    res = client.get(f"/api/runs/{run.id}/log")
    assert res.status_code == 200
    body = res.json()
    assert body["lines"] == []
    assert body["live"] is None
    assert body["run_code"] == "run_modal1"


def test_run_log_isolates_each_concurrent_running_run(client: TestClient, db_session: Session):
    """Two RUNNING runs on the same ticket must never see each other's log body."""
    ticket = db_session.exec(select(Ticket)).first()
    assert ticket

    run_a = AgentRun(
        run_code="run_a",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        stage_key="plan",
        status=RunStatus.RUNNING,
        command="claude -p 'plan A'",
    )
    run_b = AgentRun(
        run_code="run_b",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        stage_key="plan",
        status=RunStatus.RUNNING,
        command="claude -p 'plan B'",
    )
    db_session.add(run_a)
    db_session.add(run_b)
    db_session.commit()
    db_session.refresh(run_a)
    db_session.refresh(run_b)

    _seed_log(
        db_session,
        run_a,
        [{"time": "20:57:14", "tag": "OUT", "text": "lane A output"}],
        live="A working",
    )
    _seed_log(
        db_session,
        run_b,
        [{"time": "20:58:00", "tag": "OUT", "text": "lane B output"}],
        live="B working",
    )

    body_a = client.get(f"/api/runs/{run_a.id}/log").json()
    body_b = client.get(f"/api/runs/{run_b.id}/log").json()

    assert [line["text"] for line in body_a["lines"]] == ["lane A output"]
    assert body_a["live"] == "A working"
    assert [line["text"] for line in body_b["lines"]] == ["lane B output"]
    assert body_b["live"] == "B working"


def test_run_log_missing_run_returns_404(client: TestClient):
    assert client.get("/api/runs/does-not-exist/log").status_code == 404


# --- lg-durable-remote-336: the transport and the attach command (AC27, AC33) ---
#
# `attach_command` is composed SERVER-SIDE by one pure function: the client
# cannot know the session name, and the `lg-` prefix has exactly one home.


def test_run_log_reports_the_transport_a_live_run_is_detached_on(
    client: TestClient, db_session: Session
):
    """AC27. This is how AC18's "visible in the run record" is satisfied."""
    run = _seed_run(db_session, status=RunStatus.RUNNING)
    run.agent_transport = AgentTransport.TMUX
    db_session.add(run)
    db_session.commit()

    body = client.get(f"/api/runs/{run.id}/log").json()

    assert body["transport"] == "tmux"
    assert body["attach_command"] == (
        f"tmux -L {settings.agent_tmux_socket} attach -t lg-run_modal1-{run.id[:8]}"
    )


def test_run_log_offers_no_attach_command_for_a_file_transport_run(
    client: TestClient, db_session: Session
):
    """There is no session to attach to, and "" is how the UI knows to render
    no copy control rather than a command that would fail."""
    run = _seed_run(db_session, status=RunStatus.RUNNING)
    run.agent_transport = AgentTransport.FILE
    db_session.add(run)
    db_session.commit()

    body = client.get(f"/api/runs/{run.id}/log").json()

    assert body["transport"] == "file"
    assert body["attach_command"] == ""


def test_run_log_reports_an_empty_transport_for_a_run_that_never_recorded_one(
    client: TestClient, db_session: Session
):
    """AC28's server half. 1,449 of 2,285 runs have no process identity and
    will never have a transport; reporting "file" would assert something the
    row does not say. Same fail-closed direction as `run_reattach.liveness`."""
    run = _seed_run(db_session, status=RunStatus.SUCCEEDED)

    body = client.get(f"/api/runs/{run.id}/log").json()

    assert body["transport"] == ""
    assert body["attach_command"] == ""


def test_run_log_offers_no_attach_command_for_a_settled_run(
    client: TestClient, db_session: Session
):
    """The session is gone with the run; its attach command is a dead end."""
    run = _seed_run(db_session, status=RunStatus.SUCCEEDED)
    run.agent_transport = AgentTransport.TMUX
    db_session.add(run)
    db_session.commit()

    body = client.get(f"/api/runs/{run.id}/log").json()

    assert body["transport"] == "tmux"
    assert body["attach_command"] == ""


def test_run_messages_reports_when_a_stop_was_requested(client: TestClient, db_session: Session):
    """AC33. Without it the client cannot tell "stopping" from any other
    refusal — and `isPending` resets as soon as the POST returns, so a second
    press would signal a process group the server no longer owns."""
    run = _seed_run(db_session, status=RunStatus.RUNNING)

    before = client.get(f"/api/runs/{run.id}/messages").json()
    assert before["cancel_requested_at"] is None

    client.post(f"/api/runs/{run.id}/cancel")
    after = client.get(f"/api/runs/{run.id}/messages").json()

    assert after["cancel_requested_at"] is not None


def test_the_stop_timestamp_is_the_same_iso_shape_as_the_rest_of_the_payload(
    client: TestClient, db_session: Session
):
    """AC33's shape, not just its presence.

    The sibling case above asserts the key stops being null, which `str(dt)`,
    an epoch float and a bare date all satisfy — and the client types the field
    `string | null`, so none of them is a type error either. It is read by
    `Boolean(...)`, so a wrong shape latches the control correctly and then
    misleads anyone who renders it. Pin it to the stored instant in the
    encoding every other timestamp on this payload uses.
    """
    run = _seed_run(db_session, status=RunStatus.RUNNING)

    client.post(f"/api/runs/{run.id}/cancel")
    db_session.refresh(run)
    body = client.get(f"/api/runs/{run.id}/messages").json()

    stamped = body["cancel_requested_at"]
    assert datetime.fromisoformat(stamped) == run.cancel_requested_at.replace(
        tzinfo=run.cancel_requested_at.tzinfo or timezone.utc
    )
    assert stamped.endswith("Z") or "+" in stamped, (
        f"{stamped!r} carries no zone, so the client cannot place it on a clock"
    )


# ---- S1b: the payload says which adapter ran, and whether it writes call lines -------------------
#
# Neither `LedgerAttempt` nor the run-log payload carried the adapter, so the Hive map had no input
# for "this adapter writes no tool-call lines" and rendered every such run as idle
# (lg-hive-office-2784, S1b / S7).

_COMMANDS = {
    "claude": "/Users/x/.local/bin/claude -p --output-format stream-json",
    "cursor": "/Users/x/.local/bin/cursor-agent agent -p --trust",
    "codex": "/Users/x/.local/bin/codex exec --json",
    "": "",
}


def _seed_run_with_command(session: Session, command: str) -> AgentRun:
    ticket = session.exec(select(Ticket)).first()
    assert ticket
    run = AgentRun(
        run_code=f"run_ad{abs(hash(command)) % 10000}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        skill_name="",
        stage_key="implement",
        status=RunStatus.SUCCEEDED,
        command=command,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_run_log_reports_the_adapter_and_whether_call_lines_are_expected(
    client: TestClient, db_session: Session
):
    """S1-AC3. Both fields, on one response each, over the three resolvable adapters and the
    unresolvable empty command. `call_lines_expected` is false for everything but Claude."""
    for adapter, command in _COMMANDS.items():
        run = _seed_run_with_command(db_session, command)
        body = client.get(f"/api/runs/{run.id}/log").json()
        assert body["adapter"] == adapter, command
        assert body["call_lines_expected"] is (adapter == "claude"), command
        # The raw command stays on the payload beside the derived adapter; the client renders
        # neither from the other.
        assert body["command"] == command


def test_the_two_new_fields_are_always_present_and_never_null(
    client: TestClient, db_session: Session
):
    """A missing key and `false` are different answers, and the client branches on both. An
    unresolvable adapter is `""` — never `null`, which would make `adapter` optional on the
    client and let `activity unknown — adapter not recorded` fall through to a default."""
    run = _seed_run_with_command(db_session, "")
    body = client.get(f"/api/runs/{run.id}/log").json()
    assert "adapter" in body
    assert "call_lines_expected" in body
    assert body["adapter"] == ""
    assert body["call_lines_expected"] is False
    assert isinstance(body["call_lines_expected"], bool)


def test_a_run_with_no_log_artifact_still_reports_its_adapter(
    client: TestClient, db_session: Session
):
    """The adapter comes from `AgentRun.command`, not from the log, so a run that streamed nothing
    still says what ran it. This is the common case the fields exist for: a run with no lines and
    no adapter is `unknown`, while a Claude run with no lines is `no calls yet`."""
    run = _seed_run_with_command(db_session, _COMMANDS["claude"])
    body = client.get(f"/api/runs/{run.id}/log").json()
    assert body["lines"] == []
    assert body["adapter"] == "claude"
    assert body["call_lines_expected"] is True
