"""Landing brings the integration branch up to its base before it merges.

Only a dispatch used to refresh an integration branch from the base. A tree
whose last ticket was cut before a fix reached main landed, and verified, every
later ticket against a base without that fix: lg-milestone-that-1423 and 1426
failed land verification four times each on a test main had already fixed.
"""

from __future__ import annotations

import subprocess

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.integration_conflict_card import conflict_card_title
from loregarden.services.land_ticket import land_ticket
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="landing", name="landing", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-refresh",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace, repo, milestone):
    """A child whose branch carries one commit, cut from a freshly made integration branch."""
    ticket = Ticket(
        external_id="lg-refresh-1",
        workspace_id=workspace.id,
        title="Ticket",
        branch="loregarden/lg-refresh-1",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "work.txt", "work\n")
    return ticket


def _sha(repo, ref):
    return git(repo, "rev-parse", ref).stdout.strip()


def _contains(repo, ancestor, descendant):
    """git's own answer: exit 0 means reachable, 1 means not."""
    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert done.returncode in (0, 1), done.stderr
    return done.returncode == 0


def test_a_base_commit_made_after_the_branch_was_cut_is_in_the_landing(
    session, workspace, repo, ticket
):
    base_fix = commit_on(repo, "main", "fix.txt", "a fix that reached main later\n")

    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail
    assert _contains(repo, base_fix, result.landed_sha), (
        "the landing was made on an integration branch that never took main's newer commit"
    )


def test_a_base_that_conflicts_with_the_branch_still_lands_and_files_the_card(
    session, workspace, repo, ticket, milestone
):
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    commit_on(repo, target, "shared.txt", "the tree's version\n")
    commit_on(repo, "main", "shared.txt", "main's version\n")
    target_before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok, result.detail
    assert _contains(repo, target_before, result.landed_sha)
    cards = session.exec(
        select(Approval).where(
            Approval.ticket_id == milestone.id,
            Approval.status == ApprovalStatus.PENDING,
            Approval.title == conflict_card_title(target, "main"),
        )
    ).all()
    assert len(cards) == 1, "the stale integration branch reached no one"
