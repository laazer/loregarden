"""Every CI poll tick error is logged when it starts and when it clears.

The tick collected its failures in `WorkspacePoll.errors` and returned them to a
loop that discarded them. Listing failures were logged where they happened, but
the merge queue's own errors (a push GitHub refused, a comment that failed, a
landed PR whose ticket could not be closed) and follow-main's were reported
nowhere.
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest
from loregarden.models.domain import FollowMainState
from loregarden.services import ci_poll_scheduler
from loregarden.services.ci_poll_scheduler import WorkspacePoll, report_tick_errors, run_ci_poll
from loregarden.services.follow_main_status import FollowStatus
from loregarden.services.merge_queue_loop import CycleReport


@pytest.fixture(autouse=True)
def no_standing_errors():
    with mock.patch.dict(ci_poll_scheduler._standing_errors, clear=True):
        yield


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def _tick(caplog: pytest.LogCaptureFixture, **errors: list[str]) -> list[str]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ci_poll_scheduler.__name__):
        report_tick_errors([WorkspacePoll(slug=slug, errors=e) for slug, e in errors.items()])
    return _warnings(caplog)


def test_a_standing_error_is_logged_when_it_starts_and_when_it_clears(caplog):
    assert _tick(caplog, lg=["merge queue: PR #9: push failed: denied"]) == [
        "CI poll for lg: merge queue: PR #9: push failed: denied"
    ]
    assert _tick(caplog, lg=["merge queue: PR #9: push failed: denied"]) == []
    assert _tick(caplog, lg=[]) == [
        "CI poll for lg: cleared: merge queue: PR #9: push failed: denied"
    ]
    assert _tick(caplog, lg=[]) == []


def test_a_new_error_beside_a_standing_one_is_logged(caplog):
    _tick(caplog, lg=["could not list pull requests: no auth"])

    assert _tick(caplog, lg=["could not list pull requests: no auth", "follow main: refused"]) == [
        "CI poll for lg: follow main: refused"
    ]


def test_each_workspace_stands_on_its_own(caplog):
    _tick(caplog, lg=["same"], blobert=["same"])

    assert _tick(caplog, lg=[], blobert=["same"]) == ["CI poll for lg: cleared: same"]


def test_a_merge_queue_error_reaches_the_log_through_the_tick(caplog):
    workspace = mock.Mock(slug="lg")
    cycle = CycleReport(slug="lg", errors=["PR #936 landed; ticket lg-x-1 could not close"])
    with (
        mock.patch.object(ci_poll_scheduler, "active_workspaces", return_value=[workspace]),
        mock.patch.object(
            ci_poll_scheduler, "poll_workspace", side_effect=lambda _s, w: WorkspacePoll(w.slug)
        ),
        mock.patch.object(ci_poll_scheduler, "advance_workspace", return_value=cycle),
        mock.patch.object(ci_poll_scheduler, "is_server_checkout", return_value=False),
        mock.patch.object(ci_poll_scheduler, "CIService"),
        caplog.at_level(logging.WARNING, logger=ci_poll_scheduler.__name__),
    ):
        (poll,) = run_ci_poll(mock.Mock())

    assert poll.errors == ["merge queue: PR #936 landed; ticket lg-x-1 could not close"]
    assert "CI poll for lg: merge queue: PR #936 landed; ticket lg-x-1 could not close" in (
        _warnings(caplog)
    )


@pytest.mark.parametrize(
    ("state", "reported"),
    [(FollowMainState.REFUSED, True), (FollowMainState.WAITING_FOR_IDLE, False)],
)
def test_a_stuck_follow_main_is_a_tick_error(state: FollowMainState, reported: bool):
    poll = WorkspacePoll(slug="loregarden")
    status = FollowStatus(state, "uncommitted changes to server/app.py")
    with mock.patch.object(ci_poll_scheduler, "follow_main", return_value=status):
        ci_poll_scheduler._follow_main(mock.Mock(), mock.Mock(slug="loregarden"), poll)

    assert bool(poll.errors) is reported
