"""Pull requests the merge queue admits by number, with no ticket: an interactive session's.

Same harness as `test_merge_queue_loop`: real git (a bare ``origin`` and a
clone), GitHub faked at the `QueueGitHub` seam. GitHub's ``refs/pull/N/head``
is a symbolic ref in the bare origin pointing at the PR's head branch, so
`fetch_pull_head` fetches it for real and follows every push to the branch,
as GitHub's does.

The property under test beyond "it lands": the head branch is its author's, so
a push the author makes while the entry waits is adopted, never overwritten.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    Approval,
    MergeQueueAdmission,
    MergeQueueState,
    PullRequestState,
)
from loregarden.services.git_automation import push_commit_as
from loregarden.services.merge_queue import QueueRefusal
from loregarden.services.merge_queue_pull_heads import pin_ref
from loregarden.services.merge_queue_reports import queue_card_title, report_stopped
from loregarden.services.merge_queue_requests import enqueue_pull_request
from sqlmodel import select
from tests.test_merge_queue_loop import (  # noqa: F401 - fixtures by name
    FakeGitHub,
    _check,
    _cycle,
    commit_file,
    git,
    github_fixture,
    origin_fixture,
    repo_fixture,
    session_fixture,
    workspace_fixture,
)

BRANCH = "claude/session-abc"
PR = 9


class AuthorsGitHub(FakeGitHub):
    """The loop's fake, plus comments and GitHub's ``refs/pull/N/head``."""

    def __init__(self, origin) -> None:
        super().__init__(origin)
        self.comments: list[tuple[int, str]] = []

    def open(self, number: int, head_branch: str) -> None:
        super().open(number, head_branch)
        git(self.origin, "symbolic-ref", f"refs/pull/{number}/head", f"refs/heads/{head_branch}")

    def comment(self, number: int, body: str) -> str:
        self.comments.append((number, body))
        return ""


@pytest.fixture(name="authors")
def authors_fixture(origin, github):  # noqa: ARG001 - github patches delete_staging_ref
    return AuthorsGitHub(origin)


def _author_pushes(repo, name: str) -> str:
    """The author commits ``name`` on their branch and pushes it, as a session would."""
    sha = commit_file(repo, BRANCH, name, f"{name} by the author\n")
    git(repo, "push", "-q", "--force", "origin", f"{sha}:refs/heads/{BRANCH}")
    return sha


@pytest.fixture(name="opened")
def opened_fixture(repo, authors) -> str:
    git(repo, "branch", BRANCH, "main")
    head = _author_pushes(repo, "session.txt")
    authors.open(PR, BRANCH)
    return head


def _enqueue(session, workspace, authors):
    admission = enqueue_pull_request(session, workspace, PR, github=authors)
    assert admission.outcome is MergeQueueAdmission.ADMITTED, admission.detail
    return admission.entry


def _staged_files(repo, sha: str) -> set[str]:
    return set(git(repo, "ls-tree", "--name-only", sha).splitlines())


def test_a_pr_admitted_by_number_carries_no_ticket_and_lands_its_tested_tree(
    session, workspace, repo, origin, authors, opened
):
    entry = _enqueue(session, workspace, authors)
    assert (entry.ticket_id, entry.head_sha, entry.pr_head_branch) == (None, opened, BRANCH)

    _cycle(session, workspace, authors)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    assert git(origin, "rev-parse", f"refs/heads/{BRANCH}") == entry.staged_sha == entry.pushed_sha

    authors.pass_everything = True
    _cycle(session, workspace, authors)
    session.refresh(entry)

    assert entry.state is MergeQueueState.MERGED, entry.detail
    assert entry.landed_tree == entry.staged_tree
    assert "session.txt" in _staged_files(origin, "refs/heads/main")
    pinned = git(repo, "for-each-ref", "--format=%(refname)", pin_ref(PR))
    assert pinned == ""


def test_an_author_push_while_queued_is_staged_instead_of_overwritten(
    session, workspace, repo, origin, authors, opened
):
    entry = _enqueue(session, workspace, authors)
    newer = _author_pushes(repo, "afterthought.txt")

    _cycle(session, workspace, authors)
    session.refresh(entry)
    assert entry.state is MergeQueueState.QUEUED
    assert entry.head_sha == newer
    assert git(origin, "rev-parse", f"refs/heads/{BRANCH}") == newer, "the push was overwritten"

    _cycle(session, workspace, authors)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    assert {"session.txt", "afterthought.txt"} <= _staged_files(repo, entry.staged_sha)


def test_an_author_push_during_testing_restages_then_stages_that_push(
    session, workspace, repo, authors, opened
):
    entry = _enqueue(session, workspace, authors)
    _cycle(session, workspace, authors)
    session.refresh(entry)
    first_staged = entry.staged_sha
    newer = _author_pushes(repo, "review-fix.txt")

    _cycle(session, workspace, authors)  # the head moved: restage
    _cycle(session, workspace, authors)  # staging sees the author's push: adopt
    _cycle(session, workspace, authors)  # stage the adopted work
    session.refresh(entry)

    assert entry.head_sha == newer
    assert entry.state is MergeQueueState.TESTING
    assert entry.staged_sha != first_staged
    assert "review-fix.txt" in _staged_files(repo, entry.staged_sha)


def test_a_restage_after_main_moves_pushes_over_the_queues_own_commit(
    session, workspace, repo, authors, opened
):
    """`pushed_sha` survives a restage, so the queue's own commit is not read as the author's."""
    entry = _enqueue(session, workspace, authors)
    _cycle(session, workspace, authors)
    commit_file(repo, "main", "other.txt", "moved\n")
    git(repo, "push", "-q", "origin", "main")
    authors.pass_everything = True

    _cycle(session, workspace, authors)  # base moved: restage
    _cycle(session, workspace, authors)  # stage again on the new tip
    session.refresh(entry)

    assert entry.head_sha == opened
    assert entry.state is MergeQueueState.TESTING
    assert entry.attempts == 2


def test_a_pr_that_fails_is_told_on_the_pr_and_gets_one_inbox_card(
    session, workspace, authors, opened
):
    entry = _enqueue(session, workspace, authors)
    _cycle(session, workspace, authors)
    session.refresh(entry)
    authors.checks[entry.staged_sha] = [_check("Server (Python)", "FAILURE")]

    _cycle(session, workspace, authors)
    session.refresh(entry)
    assert entry.state is MergeQueueState.FAILED
    report_stopped(session, entry)  # a later report refreshes the card, never duplicates it

    cards = session.exec(select(Approval).where(Approval.title == queue_card_title(entry))).all()
    assert len(cards) == 1
    assert cards[0].ticket_id is None
    assert [number for number, _ in authors.comments] == [PR]
    assert "Server (Python)" in authors.comments[0][1]
    assert f"pr_number={PR}" in authors.comments[0][1]


def test_a_closed_pr_and_a_trees_publish_pr_are_refused(session, workspace, repo, authors, opened):
    authors.state[PR] = PullRequestState.CLOSED
    with pytest.raises(QueueRefusal, match="closed"):
        enqueue_pull_request(session, workspace, PR, github=authors)

    git(repo, "push", "-q", "origin", f"{opened}:refs/heads/publish/integration/lg-1")
    authors.open(PR + 1, "publish/integration/lg-1")
    with pytest.raises(QueueRefusal, match="by a ticket"):
        enqueue_pull_request(session, workspace, PR + 1, github=authors)


def test_a_push_leased_on_a_stale_head_is_refused(repo, origin, opened):
    newer = _author_pushes(repo, "raced.txt")

    pushed = push_commit_as(repo, BRANCH, git(repo, "rev-parse", "main"), lease=opened)

    assert not pushed.ok
    assert git(origin, "rev-parse", f"refs/heads/{BRANCH}") == newer
