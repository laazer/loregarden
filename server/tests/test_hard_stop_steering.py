"""Stopping a turn that is already running.

#48 shipped the notes-only half of steering: an operator could write a sentence
into a live run. The hard-stop half — end the turn now — was deferred, and the
pieces landed since without being joined up: `POST /api/runs/{id}/cancel` sets
`cancel_requested_at`, `_check_cancel` kills the CLI, and `cancelRun` sits in
the API client with no caller.

What these pin is the part that is easy to get wrong once it *is* wired up: a
stop must not lose to ordinary traffic, and it must stay something only an
operator can do.
"""

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.run_cancellation import request_cancel
from loregarden.services.run_steering import queue_message, steer_refusal
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _running_run(session: Session, workspace) -> AgentRun:
    ticket = Ticket(external_id="T-1", workspace_id=workspace.id, title="T-1")
    session.add(ticket)
    session.commit()
    run = AgentRun(
        run_code="run_1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        # `claude` is the only steerable adapter — cursor-agent has no
        # --input-format, so there is no stdin to write into.
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---- the stop is sticky ------------------------------------------------


def test_a_stopping_run_refuses_new_steering(session, workspace):
    """Traffic arriving after the stop must not be accepted.

    The bridge polls the cancel before it drains messages, so a steer accepted
    now is either dropped unread or — landing in the same iteration — is the
    last thing the agent is told before being killed. Neither is what the
    operator who pressed stop asked for, and both leave them believing they
    corrected a run they actually ended.
    """
    run = _running_run(session, workspace)
    assert steer_refusal(run) == "" or "adapter" in steer_refusal(run)

    request_cancel(session, run)
    session.refresh(run)

    assert steer_refusal(run) == "Run is stopping, so it cannot be steered."
    with pytest.raises(ValueError, match="stopping"):
        queue_message(session, run, "actually, do it the other way")


def test_the_refusal_names_the_stop_rather_than_the_adapter(session, workspace):
    """A stopping run reports *why* it will not take a message.

    A single reason string is what keeps the API and the UI saying the same
    thing, so the stop has to outrank the adapter check rather than hide behind
    it — otherwise an operator on a cursor run is told the wrong thing.
    """
    run = _running_run(session, workspace)
    request_cancel(session, run)
    session.refresh(run)

    assert "stopping" in steer_refusal(run)


def test_a_second_stop_is_refused_rather_than_re_armed(session, workspace):
    """Pressing stop twice is not an error worth surfacing as a new request."""
    from loregarden.services.run_cancellation import cancel_refusal

    run = _running_run(session, workspace)
    request_cancel(session, run)
    session.refresh(run)

    assert cancel_refusal(run) == "Cancel already requested."


# ---- operator-only, pinned rather than assumed -------------------------


def test_no_mcp_tool_can_stop_a_run():
    """Agents must not be able to stop each other.

    Today this holds by absence: there is no cancel tool in the MCP surface, so
    an orchestrated agent has no way to reach one. That is a real guarantee and
    a fragile one — it survives only until someone adds the obvious tool. This
    fails the moment that happens, so the operator-only decision has to be made
    again deliberately rather than lost in a convenience.
    """
    from loregarden.mcp import tools as mcp_tools

    names = {tool["name"] for tool in mcp_tools.TOOL_DEFINITIONS}
    offenders = sorted(name for name in names if "cancel" in name.lower() or "stop" in name.lower())

    assert offenders == [], (
        "an MCP tool can now stop a run; e3 requires stopping to be operator-only, "
        "so this needs an explicit authorization decision rather than a new tool"
    )


def test_the_rest_endpoint_is_the_operator_path(session, workspace):
    """The stop an operator presses is the REST one, and it works on a live run."""
    run = _running_run(session, workspace)

    request_cancel(session, run)

    session.expire_all()
    stored = session.exec(select(AgentRun).where(AgentRun.id == run.id)).one()
    assert stored.cancel_requested_at is not None
