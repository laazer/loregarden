"""`merge-queue wait` ends on the entry's verdict, or says why it has none.

Waiting on a landing meant a hand-written polling loop per wait, and a loop
reading the database cannot tell "still testing" from "the server that runs the
queue is gone". These drive `wait_for_entry` over a real entry read through
`entry_detail`, with the clock and sleep faked; each sleep plays the next step.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from loregarden.cli.main import build_parser
from loregarden.cli.merge_queue import EXIT_WAIT_STOPPED
from loregarden.models.domain import MergeQueueEntry, MergeQueueState, Workspace
from loregarden.services.merge_queue_requests import entry_detail
from loregarden.services.merge_queue_wait import (
    EntryDidNotMerge,
    WaitPolicy,
    WaitStopped,
    wait_for_entry,
)
from sqlmodel import Session
from tests.factories import make_workspace

POLICY = WaitPolicy(poll_seconds=30, timeout_seconds=3600, unreachable_seconds=600)


@pytest.fixture(name="entry")
def entry_fixture(db_session: Session) -> MergeQueueEntry:
    workspace: Workspace = make_workspace(db_session, slug="queue-wait")
    entry = MergeQueueEntry(
        workspace_id=workspace.id,
        pr_number=935,
        pr_url="https://github.com/o/r/pull/935",
        pr_head_branch="claude/x",
        source_branch="claude/x",
        head_sha="a" * 40,
        position=1,
        state=MergeQueueState.QUEUED,
        detail="waiting its turn",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return entry


class Harness:
    """A clock that each sleep advances, and a script of steps the sleeps play."""

    def __init__(self, session: Session, entry: MergeQueueEntry, steps: list[Callable[[], None]]):
        self.session = session
        self.entry = entry
        self.steps = list(steps)
        self.now = 0.0
        self.up = True
        self.reports: list[str] = []

    def move(self, state: MergeQueueState, detail: str) -> Callable[[], None]:
        def step() -> None:
            self.entry.state = state
            self.entry.detail = detail
            self.session.add(self.entry)
            self.session.commit()

        return step

    def server(self, up: bool) -> Callable[[], None]:
        def step() -> None:
            self.up = up

        return step

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.steps:
            self.steps.pop(0)()

    def wait(self):
        return wait_for_entry(
            lambda: entry_detail(self.session, self.entry.id, live=False),
            lambda: self.up,
            self.reports.append,
            POLICY,
            sleep=self.sleep,
            clock=lambda: self.now,
        )


def _noop() -> None:
    return None


def test_returns_the_merged_entry_and_reports_each_change_once(
    db_session: Session, entry: MergeQueueEntry
):
    harness = Harness(db_session, entry, [])
    harness.steps = [
        _noop,
        harness.move(MergeQueueState.TESTING, "checks running"),
        _noop,
        harness.move(MergeQueueState.MERGED, "merged as abc"),
    ]

    done = harness.wait()

    assert done.state is MergeQueueState.MERGED
    assert harness.reports == [
        "queued | waiting its turn",
        "testing | checks running",
        "merged | merged as abc",
    ]


@pytest.mark.parametrize(
    "stopped", [MergeQueueState.FAILED, MergeQueueState.TIMED_OUT, MergeQueueState.WITHDRAWN]
)
def test_an_entry_that_stops_without_merging_raises_with_its_reason(
    db_session: Session, entry: MergeQueueEntry, stopped: MergeQueueState
):
    harness = Harness(db_session, entry, [])
    harness.steps = [harness.move(stopped, "conflicts with main")]

    with pytest.raises(EntryDidNotMerge) as raised:
        harness.wait()

    assert raised.value.entry.state is stopped
    assert raised.value.entry.detail == "conflicts with main"


def test_a_server_gone_for_the_unreachable_window_ends_the_wait(
    db_session: Session, entry: MergeQueueEntry
):
    harness = Harness(db_session, entry, [])
    harness.steps = [harness.server(False)]

    with pytest.raises(WaitStopped, match="has not answered /health"):
        harness.wait()

    # Down from the first probe after 30 s, given up 600 s later.
    assert harness.now == pytest.approx(30 + 600)


def test_a_server_that_comes_back_resets_the_unreachable_window(
    db_session: Session, entry: MergeQueueEntry
):
    harness = Harness(db_session, entry, [])
    down_for_most_of_the_window = [harness.server(False)] + [_noop] * 18 + [harness.server(True)]
    harness.steps = [*down_for_most_of_the_window, harness.move(MergeQueueState.MERGED, "merged")]

    assert harness.wait().state is MergeQueueState.MERGED


def test_a_live_entry_at_the_deadline_ends_the_wait(db_session: Session, entry: MergeQueueEntry):
    harness = Harness(db_session, entry, [])

    with pytest.raises(WaitStopped, match="still queued"):
        harness.wait()

    assert harness.now == pytest.approx(POLICY.timeout_seconds)


def test_the_cli_registers_wait_with_its_no_verdict_exit_code():
    args = build_parser().parse_args(["merge-queue", "wait", "some-entry"])

    assert args.entry_id == "some-entry"
    assert args.run.__module__ == "loregarden.cli.merge_queue"
    # Distinct from 1 (the entry stopped) so a caller can tell the two apart.
    assert EXIT_WAIT_STOPPED not in {0, 1, 2}
