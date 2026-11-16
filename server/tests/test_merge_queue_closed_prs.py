"""A stopped merge-queue entry whose PR was closed outside the queue is settled."""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    MergeQueueEntry,
    MergeQueueState,
    Workspace,
)
from loregarden.models.domain.pull_request_enums import PullRequestState
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.invariants import run_invariant
from loregarden.services.merge_queue_closed_prs import STOPPED_ENTRIES_OUTLIVE_NO_CLOSED_PR
from loregarden.services.merge_queue_reports import report_stopped
from sqlmodel import Session
from tests.factories import make_workspace

_DETAIL = "conflicts with main 6d3d6701d965 in server/loregarden/api/orchestration.py"


class _GitHub:
    """Answers `read_pull_request` from a table of PR states; a missing PR cannot be read."""

    def __init__(self, states: dict[int, PullRequestState]) -> None:
        self.states = states
        self.asked: list[int] = []

    def read_pull_request(self, number: int) -> mock.Mock:
        self.asked.append(number)
        if number not in self.states:
            raise GitHubReadError(f"GitHub has no pull request #{number}")
        return mock.Mock(state=self.states[number])


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    return make_workspace(db_session, slug="queue-closed")


def _entry(
    session: Session, workspace: Workspace, pr: int, state: MergeQueueState, position: int
) -> MergeQueueEntry:
    entry = MergeQueueEntry(
        workspace_id=workspace.id,
        pr_number=pr,
        pr_url=f"https://github.com/o/r/pull/{pr}",
        pr_head_branch=f"claude/pr-{pr}",
        source_branch=f"claude/pr-{pr}",
        head_sha=f"{pr:040x}",
        position=position,
        state=state,
        detail=_DETAIL,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _run(session: Session, github: _GitHub):
    with mock.patch("loregarden.services.merge_queue_closed_prs.github_for", return_value=github):
        return run_invariant(session, STOPPED_ENTRIES_OUTLIVE_NO_CLOSED_PR)


@pytest.mark.parametrize("closed", [PullRequestState.CLOSED, PullRequestState.MERGED])
def test_a_stopped_entry_whose_pr_closed_is_withdrawn_and_its_card_closed(
    db_session: Session, workspace: Workspace, closed: PullRequestState
):
    entry = _entry(db_session, workspace, 879, MergeQueueState.FAILED, 1)
    card = report_stopped(db_session, entry)

    check = _run(db_session, _GitHub({879: closed}))

    assert (check.found, check.repaired) == (1, 1)
    db_session.refresh(entry)
    assert entry.state is MergeQueueState.WITHDRAWN
    assert f"was {closed.value}" in entry.detail
    assert _DETAIL in entry.detail
    assert db_session.get(Approval, card.id).status is ApprovalStatus.APPROVED


def test_a_timed_out_entry_whose_pr_closed_is_withdrawn(db_session: Session, workspace: Workspace):
    entry = _entry(db_session, workspace, 880, MergeQueueState.TIMED_OUT, 1)

    _run(db_session, _GitHub({880: PullRequestState.CLOSED}))

    db_session.refresh(entry)
    assert entry.state is MergeQueueState.WITHDRAWN


def test_a_stopped_entry_whose_pr_is_open_still_waits(db_session: Session, workspace: Workspace):
    entry = _entry(db_session, workspace, 881, MergeQueueState.FAILED, 1)

    check = _run(db_session, _GitHub({881: PullRequestState.OPEN}))

    assert check.found == 0
    db_session.refresh(entry)
    assert entry.state is MergeQueueState.FAILED


def test_a_pr_github_cannot_read_leaves_the_entry_and_warns(
    db_session: Session, workspace: Workspace, caplog
):
    entry = _entry(db_session, workspace, 882, MergeQueueState.FAILED, 1)

    with caplog.at_level("WARNING", logger="loregarden.services.merge_queue_closed_prs"):
        check = _run(db_session, _GitHub({}))

    assert check.found == 0
    db_session.refresh(entry)
    assert entry.state is MergeQueueState.FAILED
    assert [r.levelname for r in caplog.records] == ["WARNING"]


def test_a_superseded_entry_or_a_live_one_is_not_asked_about(
    db_session: Session, workspace: Workspace
):
    old = _entry(db_session, workspace, 883, MergeQueueState.FAILED, 1)
    _entry(db_session, workspace, 883, MergeQueueState.MERGED, 2)
    _entry(db_session, workspace, 884, MergeQueueState.QUEUED, 3)
    _entry(db_session, workspace, 885, MergeQueueState.WITHDRAWN, 4)
    github = _GitHub({883: PullRequestState.MERGED, 884: PullRequestState.CLOSED})

    check = _run(db_session, github)

    assert (check.found, github.asked) == (0, [])
    db_session.refresh(old)
    assert old.state is MergeQueueState.FAILED


def test_an_entry_retried_after_detection_is_left_alone(db_session: Session, workspace: Workspace):
    entry = _entry(db_session, workspace, 886, MergeQueueState.FAILED, 1)
    github = _GitHub({886: PullRequestState.CLOSED})
    with mock.patch("loregarden.services.merge_queue_closed_prs.github_for", return_value=github):
        violations = STOPPED_ENTRIES_OUTLIVE_NO_CLOSED_PR.detect(db_session)
    entry.state = MergeQueueState.QUEUED
    db_session.add(entry)
    db_session.commit()

    assert STOPPED_ENTRIES_OUTLIVE_NO_CLOSED_PR.repair(db_session, violations) == 0
    db_session.refresh(entry)
    assert entry.state is MergeQueueState.QUEUED
