"""The merge queue loop: stage, wait, land exactly the tested tree (lg-milestone-that-969).

git is real: a bare ``origin`` and a clone the workspace points at, so the
staging cut, the push and the fetch of the base all run for real. GitHub is
faked at the `QueueGitHub` seam, and its fake merges the way GitHub's squash
does — a new commit on origin's base whose tree is the head's merged onto the
base — so the tree-equality assertion is checked against git, not a stub.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    PullRequestState,
    Ticket,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
    utcnow,
)
from loregarden.services import merge_queue_loop
from loregarden.services.git_merge_noco import MergeOutcome
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.merge_queue import Candidate, enqueue, transition, withdraw
from loregarden.services.merge_queue_github import StagedPullRequest
from loregarden.services.merge_queue_loop import MergeQueueLoop, MergeVerdict, merge_verdict
from loregarden.services.pull_request_merge import MergeFailed
from loregarden.services.ticket_pull_request import GhCheck
from sqlmodel import Session

REQUIRED = ["Server (Python)", "Client (TypeScript)", "PR UX section", "PR fix coverage"]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} in {cwd}: {result.stderr.strip()}"
    return result.stdout.strip()


def commit_file(repo: Path, branch: str, name: str, content: str) -> str:
    git(repo, "checkout", "-q", branch)
    (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{name} on {branch}")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    return sha


@pytest.fixture(name="origin")
def origin_fixture(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    # The fake GitHub commits its squash here; a CI runner has no ambient identity.
    git(origin, "config", "user.email", "github@example.com")
    git(origin, "config", "user.name", "GitHub")
    return origin


@pytest.fixture(name="repo")
def repo_fixture(tmp_path, origin):
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "seed.txt").write_text("one\ntwo\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    return repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    # A template, so a conflict can file its bug ticket the way production does.
    template = WorkflowTemplate(
        slug="mq",
        name="mq",
        version=1,
        stages_json=json.dumps(
            [
                {"key": "implement", "name": "Implement", "agent_id": "implementer", "order": 1},
                {"key": "done", "name": "Done", "agent_id": "", "order": 2, "terminal": True},
            ]
        ),
        transitions_json="[]",
    )
    session.add(template)
    session.commit()
    ws = Workspace(slug="mq", name="mq", repo_path=str(repo), workflow_template_id=template.id)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session, workspace, external_id: str) -> Ticket:
    """A tree root: a feature, which may hold the bug a conflict files."""
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"t {external_id}",
        work_item_type=WorkItemType.FEATURE,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _check(name: str, conclusion: str) -> GhCheck:
    return GhCheck(name=name, status="COMPLETED", conclusion=conclusion)


class FakeGitHub:
    """GitHub over the bare origin: PR heads are origin's ``publish/*`` refs."""

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.prs: dict[int, str] = {}
        self.state: dict[int, PullRequestState] = {}
        self.merged: dict[int, tuple[str, str]] = {}
        #: Checks per head commit; a commit with none has nothing reported.
        self.checks: dict[str, list[GhCheck]] = {}
        self.pass_everything = False
        self.unreachable = False
        self.head_tree_override = ""
        self.merges: list[tuple[int, str]] = []
        self.on_read = None

    def open(self, number: int, head_branch: str) -> None:
        self.prs[number] = head_branch
        self.state[number] = PullRequestState.OPEN

    def required_contexts(self, base_branch: str) -> list[str]:
        if self.unreachable:
            raise GitHubReadError("GitHub did not answer within 30s")
        return list(REQUIRED)

    def read_pull_request(self, number: int) -> StagedPullRequest:
        if self.on_read is not None:
            self.on_read()
        if self.unreachable:
            raise GitHubReadError("GitHub did not answer within 30s")
        head = git(self.origin, "rev-parse", f"refs/heads/{self.prs[number]}")
        checks = self.checks.get(head, [])
        if self.pass_everything:
            checks = [_check(name, "SUCCESS") for name in REQUIRED]
        merge_commit, merge_tree = self.merged.get(number, ("", ""))
        return StagedPullRequest(
            number=number,
            url=f"https://github.com/o/r/pull/{number}",
            state=self.state[number],
            head_branch=self.prs[number],
            head_sha=head,
            head_tree=self.head_tree_override or git(self.origin, "rev-parse", f"{head}^{{tree}}"),
            base_branch="main",
            base_sha=git(self.origin, "rev-parse", "refs/heads/main"),
            merge_commit=merge_commit,
            merge_tree=merge_tree,
            checks=checks,
        )

    def failure_log(self, checks: list[GhCheck]) -> str:
        return "E   AssertionError: the tail of the log"

    def merge(self, number: int, head_sha: str) -> str:
        """GitHub's squash: the head's tree merged onto the base, as one new commit."""
        self.merges.append((number, head_sha))
        head = git(self.origin, "rev-parse", f"refs/heads/{self.prs[number]}")
        if head != head_sha:
            raise MergeFailed("Head branch was modified")
        base = git(self.origin, "rev-parse", "refs/heads/main")
        tree = git(self.origin, "merge-tree", "--write-tree", base, head).splitlines()[0]
        commit = git(self.origin, "commit-tree", tree, "-p", base, "-m", f"squash #{number}")
        git(self.origin, "update-ref", "refs/heads/main", commit, base)
        self.state[number] = PullRequestState.MERGED
        self.merged[number] = (commit, tree)
        return commit


@pytest.fixture(name="github")
def github_fixture(origin):
    fake = FakeGitHub(origin)
    with mock.patch.object(merge_queue_loop, "delete_staging_ref", return_value=""):
        yield fake


def _admit(
    session, workspace, repo, github, ticket, *, pr: int, branch: str, name: str
) -> MergeQueueEntry:
    git(repo, "branch", branch, "main")
    head = commit_file(repo, branch, name, f"{name} work\n")
    publish = f"publish/{branch}"
    git(repo, "push", "-q", "origin", f"{head}:refs/heads/{publish}")
    github.open(pr, publish)
    admission = enqueue(
        session,
        ticket,
        workspace,
        Candidate(
            pr_number=pr,
            pr_url=f"https://github.com/o/r/pull/{pr}",
            pr_head_branch=publish,
            source_branch=branch,
            head_sha=head,
            base_branch="main",
        ),
    )
    assert admission.admitted
    return admission.entry


def _cycle(session, workspace, github) -> merge_queue_loop.CycleReport:
    return MergeQueueLoop(session, workspace, github).advance()


def _move_main(repo: Path, name: str) -> str:
    sha = commit_file(repo, "main", name, f"{name} on main\n")
    git(repo, "push", "-q", "origin", "main")
    return sha


@pytest.fixture(name="entry")
def entry_fixture(session, workspace, repo, github):
    ticket = _ticket(session, workspace, "lg-1")
    return _admit(
        session,
        workspace,
        repo,
        github,
        ticket,
        pr=7,
        branch="integration/lg-1",
        name="feature.txt",
    )


def test_a_staged_commit_whose_checks_pass_lands_its_own_tree(
    session, workspace, origin, github, entry
):
    _cycle(session, workspace, github)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    staged_tree = entry.staged_tree

    github.pass_everything = True
    _cycle(session, workspace, github)
    session.refresh(entry)

    assert entry.state is MergeQueueState.MERGED
    landed = git(origin, "rev-parse", "refs/heads/main")
    assert entry.merge_commit == landed
    assert entry.landed_tree == staged_tree == git(origin, "rev-parse", f"{landed}^{{tree}}")
    assert github.merges == [(7, entry.staged_sha)]


def test_a_base_that_moves_after_staging_restages_instead_of_merging(
    session, workspace, repo, origin, github, entry
):
    _cycle(session, workspace, github)
    session.refresh(entry)
    first_staged = entry.staged_sha
    moved_to = _move_main(repo, "other.txt")

    github.pass_everything = True
    _cycle(session, workspace, github)
    session.refresh(entry)

    assert github.merges == []
    assert entry.state is MergeQueueState.QUEUED
    assert entry.wait is MergeQueueWait.BASE_MOVED
    assert entry.staged_sha == ""

    _cycle(session, workspace, github)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    assert entry.staged_sha != first_staged
    assert git(repo, "rev-list", "--parents", "-n", "1", entry.staged_sha).split()[1:] == [moved_to]
    assert entry.attempts == 2


def test_a_tree_mismatch_refuses_the_merge_naming_both_oids(session, workspace, github, entry):
    _cycle(session, workspace, github)
    session.refresh(entry)
    github.pass_everything = True
    github.head_tree_override = "f" * 40

    _cycle(session, workspace, github)
    session.refresh(entry)

    assert github.merges == []
    assert entry.state is MergeQueueState.FAILED
    assert entry.staged_tree in entry.detail and "f" * 40 in entry.detail
    assert entry.detail.startswith("refused to merge")


def test_a_conflict_fails_with_its_paths_and_the_next_entry_still_runs(
    session, workspace, repo, github, entry, caplog
):
    conflicting = _ticket(session, workspace, "lg-2")
    git(repo, "branch", "integration/lg-2", "main")
    head = commit_file(repo, "integration/lg-2", "seed.txt", "one\nTWO-branch\nthree\n")
    git(repo, "push", "-q", "origin", f"{head}:refs/heads/publish/integration/lg-2")
    github.open(8, "publish/integration/lg-2")
    # Queued first, so it is the head of the queue.
    transition(session, entry, MergeQueueState.QUEUED, position=50)
    blocked = enqueue(
        session,
        conflicting,
        workspace,
        Candidate(
            8,
            "https://github.com/o/r/pull/8",
            "publish/integration/lg-2",
            "integration/lg-2",
            head,
            "main",
        ),
    ).entry
    transition(session, blocked, MergeQueueState.QUEUED, position=1)
    commit_file(repo, "main", "seed.txt", "one\nTWO-main\nthree\n")
    git(repo, "push", "-q", "origin", "main")

    with caplog.at_level(logging.INFO, logger="loregarden.services"):
        report = _cycle(session, workspace, github)
    session.refresh(blocked)
    session.refresh(entry)

    assert blocked.state is MergeQueueState.FAILED
    assert blocked.conflicted_files() == ["seed.txt"]
    assert "CONFLICT" in blocked.detail
    assert blocked.artifact_ref, "a person needs an addressable artifact to resolve it through"
    assert entry.state is MergeQueueState.TESTING, report.actions
    first = [r for r in caplog.records if "PR #8" in r.getMessage()]
    assert any(r.levelno == logging.WARNING for r in first)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="loregarden.services"):
        _cycle(session, workspace, github)
    later = [r for r in caplog.records if "PR #8" in r.getMessage()]
    assert later and all(r.levelno == logging.INFO for r in later)
    assert any(blocked.artifact_ref in r.getMessage() for r in later)


def test_a_wait_that_expires_times_out_with_the_elapsed_time(session, workspace, github, entry):
    _cycle(session, workspace, github)
    session.refresh(entry)
    transition(
        session, entry, MergeQueueState.TESTING, testing_started_at=utcnow() - timedelta(hours=2)
    )

    _cycle(session, workspace, github)
    session.refresh(entry)

    assert entry.state is MergeQueueState.TIMED_OUT
    assert "did not finish within 90 minutes" in entry.detail
    assert "waited 120m" in entry.detail


def test_unreported_checks_and_an_unreachable_github_are_distinct_waits(
    session, workspace, github, entry
):
    _cycle(session, workspace, github)
    _cycle(session, workspace, github)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    assert entry.wait is MergeQueueWait.CHECKS_UNREPORTED

    github.unreachable = True
    _cycle(session, workspace, github)
    session.refresh(entry)
    assert entry.state is MergeQueueState.TESTING
    assert entry.wait is MergeQueueWait.GITHUB_UNREACHABLE
    assert "did not answer" in entry.detail


def test_a_failing_required_check_fails_the_entry_with_its_log(session, workspace, github, entry):
    _cycle(session, workspace, github)
    session.refresh(entry)
    github.checks[entry.staged_sha] = [
        _check("Server (Python)", "FAILURE"),
        *[_check(name, "SUCCESS") for name in REQUIRED[1:]],
    ]

    _cycle(session, workspace, github)
    session.refresh(entry)

    assert entry.state is MergeQueueState.FAILED
    assert "Server (Python)" in entry.detail
    assert "the tail of the log" in entry.log_tail


def test_a_merge_check_that_cannot_answer_is_undetermined_never_clean():
    assert (
        merge_verdict(MergeOutcome(ok=False, detail="fatal: bad object"))
        is MergeVerdict.UNDETERMINED
    )
    assert merge_verdict(MergeOutcome(ok=False, conflicted_files=("a",))) is MergeVerdict.CONFLICTED
    assert merge_verdict(MergeOutcome(ok=True, sha="x")) is MergeVerdict.CLEAN


def test_a_preservation_check_that_raises_fails_the_entry_as_undetermined(
    session, workspace, github, entry
):
    with mock.patch.object(
        merge_queue_loop, "target_preserved", side_effect=RuntimeError("git diff failed")
    ):
        _cycle(session, workspace, github)
    session.refresh(entry)

    assert entry.state is MergeQueueState.FAILED
    assert "could not be determined" in entry.detail and "git diff failed" in entry.detail
    assert github.merges == []


def test_an_entry_withdrawn_under_the_loop_is_left_alone(session, workspace, github, entry):
    _cycle(session, workspace, github)
    github.on_read = lambda: withdraw(session, entry.id, reason="operator")

    report = _cycle(session, workspace, github)
    session.refresh(entry)

    assert entry.state is MergeQueueState.WITHDRAWN
    assert report.errors == []
    assert any("withdrawn" in action for action in report.actions)


def test_the_loop_only_merges_the_candidate_onto_the_base(
    session, workspace, repo, origin, github, entry
):
    source_before = git(repo, "rev-parse", "refs/heads/integration/lg-1")
    base_before = git(origin, "rev-parse", "refs/heads/main")

    _cycle(session, workspace, github)
    session.refresh(entry)

    assert git(repo, "rev-list", "--parents", "-n", "1", entry.staged_sha).split()[1:] == [
        base_before
    ]
    assert git(repo, "rev-parse", "refs/heads/integration/lg-1") == source_before
    assert git(origin, "rev-parse", "refs/heads/main") == base_before


def test_two_candidates_land_in_turn_each_on_the_tip_the_first_produced(
    session, workspace, repo, origin, github, entry
):
    second_ticket = _ticket(session, workspace, "lg-3")
    second = _admit(
        session,
        workspace,
        repo,
        github,
        second_ticket,
        pr=9,
        branch="integration/lg-3",
        name="third.txt",
    )
    github.pass_everything = True

    for _ in range(6):
        _cycle(session, workspace, github)
    session.refresh(entry)
    session.refresh(second)

    assert entry.state is MergeQueueState.MERGED
    assert second.state is MergeQueueState.MERGED
    assert second.staged_base_sha == entry.merge_commit
    assert git(origin, "rev-parse", "refs/heads/main") == second.merge_commit
    assert second.landed_tree == git(origin, "rev-parse", "refs/heads/main^{tree}")
