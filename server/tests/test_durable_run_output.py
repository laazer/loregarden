"""A run's output is readable by a process that did not spawn it.

317 detaches the agent subprocess from the server's lifetime. Nothing else in
that work is possible until output survives the process that produced it: a
parent that exits takes its pipes with it, and a new parent reattaching to a
live run has to be able to read what has already been written.

The store for this already existed — `RunLogStreamer` writes to a `log` artifact
and hydrates from it — and it did not quite work. Persistence was throttled *and*
tag-dependent, so whether a line was durable depended on what kind of line it
was:

    READER SAW:          ['a invoked · skill=—', 'some command']
    READABLE MID-FLIGHT: False

Two chunks of ordinary `OUT` written inside the throttle window were invisible to
a reader in another process, and would have died with the writer. The tag
allow-list is gone; time alone bounds the window now.
"""

from __future__ import annotations

import time

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkItemType
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.ticket_service import TicketService


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="durable run output",
        work_item_type=WorkItemType.MILESTONE,
    )


@pytest.fixture(name="run")
def run_fixture(db_session, ticket) -> AgentRun:
    run = AgentRun(
        run_code="rc_durable",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _streamer(run: AgentRun) -> RunLogStreamer:
    """A streamer for this run — a fresh one stands in for another process."""
    return RunLogStreamer(
        run_id=run.id,
        ticket_id=run.ticket_id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name="",
    )


def _texts(lines: list[dict[str, str]]) -> str:
    return "\n".join(line["text"] for line in lines)


def _past_the_window(writer: RunLogStreamer) -> None:
    """Put the writer past its throttle, deterministically.

    The alternative is sleeping 0.4s in every test, which is both slower and a
    race on a loaded machine. Reaching for `_last_persist` is reaching into the
    thing under test, and it is the honest way to say "the window has elapsed"
    without measuring wall-clock.
    """
    writer._last_persist = time.time() - (RunLogStreamer.PERSIST_INTERVAL_SECONDS + 0.1)


# ---- readable from elsewhere, mid-flight --------------------------------


def test_output_is_readable_by_another_process_before_the_run_finishes(db_session, run):
    """The requirement 317 rests on, and the exact case that used to fail.

    The writer never finalizes: a reattaching process arrives while the run is
    still going, which is the only time reattachment is worth anything.
    """
    writer = _streamer(run)
    writer.start("agent --do-the-thing")
    _past_the_window(writer)
    writer.append("OUT", "first chunk of agent output")
    _past_the_window(writer)
    writer.append("OUT", "second chunk")

    seen = _texts(_streamer(run).output_so_far())

    assert "first chunk of agent output" in seen
    assert "second chunk" in seen


def test_the_reader_needs_no_private_access(db_session, run):
    """`output_so_far` is the contract; `_hydrate` is an implementation detail.

    470 is the caller, and a caller reaching into a private method would make
    the store's internals part of its interface.
    """
    writer = _streamer(run)
    writer.start("cmd")
    _past_the_window(writer)
    writer.append("OUT", "visible")

    assert any("visible" in line["text"] for line in _streamer(run).output_so_far())


def test_a_reader_of_a_run_with_no_output_gets_nothing_rather_than_failing(db_session, run):
    """A run that has produced nothing yet is normal, not an error."""
    assert _streamer(run).output_so_far() == []


# ---- no tag is exempt ---------------------------------------------------


def test_durability_does_not_depend_on_the_tag(db_session, run):
    """The bug: whether a line survived depended on what kind of line it was.

    `OUT` was outside the old allow-list, so a burst of ordinary output inside
    the throttle window was lost. Both tags are written here in the same window
    and both must be readable.
    """
    writer = _streamer(run)
    writer.start("cmd")
    _past_the_window(writer)
    writer.append("OUT", "ordinary output")
    _past_the_window(writer)
    writer.append("TOOL", "a tool call")

    seen = _texts(_streamer(run).output_so_far())

    assert "a tool call" in seen, "a privileged tag stopped being durable"
    assert "ordinary output" in seen, "an ordinary tag is still not durable"


def test_the_window_is_still_throttled(db_session, run):
    """The throttle is a write-rate control and must survive the fix.

    Persisting every line would multiply writes on a chatty run. What changed is
    that the bound is time, not the tag — so this asserts a line written well
    inside the window is *not* yet on disk, which is the cost being accepted.
    """
    writer = _streamer(run)
    writer.start("cmd")
    writer._last_persist = time.time()
    writer.append("OUT", "inside the window")

    # Read the store directly: the writer's own memory would show it either way.
    assert "inside the window" not in _texts(_streamer(run).output_so_far())


def test_output_lands_once_the_window_passes(db_session, run):
    """And the bound is real: past the interval, the line is durable.

    Paired with the test above so the two together say "delayed, not dropped".
    """
    writer = _streamer(run)
    writer.start("cmd")
    _past_the_window(writer)
    writer.append("OUT", "past the window")

    assert "past the window" in _texts(_streamer(run).output_so_far())
