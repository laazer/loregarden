"""The merge queue advances between CI ticks while its head waits on GitHub.

#980's checks were green at 11:37:48 and it merged at 11:39:55: the queue only
moved on the 120s CI tick (lg-milestone-that-2808).
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest
from loregarden.api import ci as ci_api
from loregarden.models.domain import MergeQueueState
from loregarden.services import ci_poll_scheduler
from loregarden.services.ci_poll_scheduler import (
    WorkspacePoll,
    run_merge_queue_fast_tick,
    start_merge_queue_fast_loop,
)
from loregarden.services.merge_queue_loop import CycleReport


@pytest.fixture(autouse=True)
def isolated_state():
    with (
        mock.patch.dict(ci_poll_scheduler._standing_errors, clear=True),
        mock.patch.dict(ci_poll_scheduler._queue_locks, clear=True),
    ):
        yield


def _tick(heads: dict[str, MergeQueueState | None], cycle: CycleReport | None = None):
    workspaces = [mock.Mock(id=slug, slug=slug) for slug in heads]

    def head(_session, workspace_id):
        state = heads[workspace_id]
        return None if state is None else mock.Mock(state=state)

    advance = mock.Mock(return_value=cycle or CycleReport(slug="x"))
    with (
        mock.patch.object(ci_poll_scheduler, "active_workspaces", return_value=workspaces),
        mock.patch.object(ci_poll_scheduler, "head_entry", side_effect=head),
        mock.patch.object(ci_poll_scheduler, "advance_workspace", advance),
    ):
        polls = run_merge_queue_fast_tick(mock.Mock())
    return polls, [call.args[1].slug for call in advance.call_args_list]


def test_only_queues_whose_head_waits_on_github_are_advanced():
    _, advanced = _tick(
        {
            "testing": MergeQueueState.TESTING,
            "merging": MergeQueueState.MERGING,
            "queued": MergeQueueState.QUEUED,
            "empty": None,
        }
    )

    assert advanced == ["testing", "merging"]


def test_a_queue_whose_cycle_is_already_running_is_skipped():
    lock = ci_poll_scheduler._queue_lock("busy")
    lock.acquire()
    try:
        _, advanced = _tick({"busy": MergeQueueState.TESTING, "free": MergeQueueState.TESTING})
    finally:
        lock.release()

    assert advanced == ["free"]
    assert not lock.locked()


def test_a_fast_tick_error_reaches_the_log(caplog: pytest.LogCaptureFixture):
    cycle = CycleReport(slug="lg", errors=["PR #9: push failed: denied"])
    with caplog.at_level(logging.WARNING, logger=ci_poll_scheduler.__name__):
        (poll,), _ = _tick({"lg": MergeQueueState.TESTING}, cycle)

    assert poll.errors == ["merge queue: PR #9: push failed: denied"]
    assert "CI poll for lg: merge queue: PR #9: push failed: denied" in caplog.messages


def test_a_cycle_that_raises_releases_its_lock_and_is_reported():
    poll = WorkspacePoll(slug="lg")
    workspace = mock.Mock(id="lg", slug="lg")
    with mock.patch.object(ci_poll_scheduler, "advance_workspace", side_effect=RuntimeError("x")):
        ci_poll_scheduler.advance_merge_queue_now(mock.Mock(), workspace, poll)

    assert poll.errors == ["merge queue: x"]
    assert not ci_poll_scheduler._queue_lock("lg").locked()


@pytest.mark.parametrize(("enabled", "tick"), [(False, 15.0), (True, 0.0)])
def test_the_fast_loop_is_not_started_when_off(enabled: bool, tick: float):
    with mock.patch.object(ci_poll_scheduler.settings, "ci_enabled", enabled):
        assert start_merge_queue_fast_loop(tick) is None


def test_a_finished_run_webhook_advances_the_queue_too():
    workspace = mock.Mock(id="lg", slug="lg")
    session = mock.MagicMock()
    session.__enter__.return_value.exec.return_value.first.return_value = workspace
    poll = WorkspacePoll(slug="lg")
    with (
        mock.patch.object(ci_api, "Session", return_value=session),
        mock.patch.object(ci_api, "poll_workspace", return_value=poll),
        mock.patch.object(ci_api, "advance_merge_queue_now") as advance,
    ):
        assert ci_api._poll_now("lg") is poll

    advance.assert_called_once_with(session.__enter__.return_value, workspace, poll)
