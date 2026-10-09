"""An integration branch that cannot take its base is found, surfaced, and survivable.

lg-durable-remote-335's tree: #560 changed `RunLogModal.tsx` on `main` after
the tree had changed it too. Nothing noticed until a chat turn on a ticket in
the tree tried to cut a worktree, and it answered "Baxter unavailable" with no
card, no block, and nothing else on record.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import integration_branch_health
from loregarden.services.github_pr_service import create_ticket_pull_request
from loregarden.services.integration_branch_health import check_integration_branches
from loregarden.services.integration_conflict_card import conflict_card_title
from loregarden.services.target_branch import IntegrationConflictError, resolve_target_branch
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, head_branch, make_repo

BRANCH = "integration/ms"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="health", name="health", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(autouse=True)
def _fresh_preview_cache():
    integration_branch_health._PREVIEWS.clear()
    yield
    integration_branch_health._PREVIEWS.clear()


def _ticket(session, workspace, external_id, *, parent=None, kind=WorkItemType.TASK):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=external_id,
        branch=f"loregarden/{external_id}",
        parent_ticket_id=parent.id if parent else None,
        state=TicketState.IN_PROGRESS,
        work_item_type=kind,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture(name="tree")
def tree_fixture(session, workspace, repo):
    """A milestone `ms` with child `b`, its integration branch cut from main."""
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    child = _ticket(session, workspace, "b", parent=ms)
    assert resolve_target_branch(session, child, workspace, repo_root=repo) == BRANCH
    return ms, child


def _conflict(repo):
    commit_on(repo, BRANCH, "shared.txt", "integration side\n")
    commit_on(repo, "main", "shared.txt", "main side\n")


def _cards(session, root, status=ApprovalStatus.PENDING):
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == status,
            )
        ).all()
    )


# --- 1. detection when the base moves ---------------------------------------------


def test_the_sweep_finds_a_conflict_the_moment_main_moves(session, repo, tree):
    ms, _ = tree
    _conflict(repo)
    before = git(repo, "rev-parse", BRANCH).stdout.strip()

    report = check_integration_branches(session)

    assert [(r.branch, r.conflicted_files) for r in report] == [(BRANCH, ("shared.txt",))]
    [card] = _cards(session, ms)
    assert card.title == conflict_card_title(BRANCH, "main")
    assert "shared.txt" in card.impact
    assert git(repo, "rev-parse", BRANCH).stdout.strip() == before, "the sweep writes nothing"


def test_the_card_is_filed_once_and_closes_when_the_branch_takes_main(session, repo, tree):
    ms, _ = tree
    _conflict(repo)
    check_integration_branches(session)
    check_integration_branches(session)
    assert len(_cards(session, ms)) == 1

    # Someone resolves it: the integration side gives way.
    tree_dir = repo.parent / "resolve"
    git(repo, "worktree", "add", "-q", str(tree_dir), BRANCH)
    git(tree_dir, "merge", "-q", "-X", "theirs", "main", "-m", "resolve")

    check_integration_branches(session)

    assert _cards(session, ms) == []
    [closed] = _cards(session, ms, ApprovalStatus.APPROVED)
    assert closed.resolved_by == "automation"


def test_a_clean_tree_and_a_finished_tree_raise_nothing(session, repo, tree):
    ms, _ = tree
    commit_on(repo, "main", "other.txt", "unrelated\n")
    assert check_integration_branches(session)[0].conflicted_files == ()

    _conflict(repo)
    ms.state = TicketState.DONE
    session.add(ms)
    session.commit()
    assert check_integration_branches(session) == []
    assert _cards(session, ms) == []


# --- 2 & 3. a cut that hits it files the card; chat survives, stages refuse -----------


def _run(session, workspace, ticket):
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="triage",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_stage_run_still_refuses_and_the_conflict_reaches_the_inbox(
    session, workspace, repo, tree
):
    ms, child = tree
    _conflict(repo)

    with pytest.raises(IntegrationConflictError, match="shared.txt"):
        resolve_execution_root(session, _run(session, workspace, child), child, workspace)

    assert len(_cards(session, ms)) == 1


def test_a_chat_turn_runs_on_the_stale_branch_instead_of_failing(session, workspace, repo, tree):
    ms, child = tree
    _conflict(repo)
    stale_tip = git(repo, "rev-parse", BRANCH).stdout.strip()

    root = resolve_execution_root(
        session, _run(session, workspace, child), child, workspace, stale_target_ok=True
    )

    assert root != repo
    assert head_branch(root) == child.branch
    assert git(root, "merge-base", "--is-ancestor", stale_tip, "HEAD").returncode == 0
    assert len(_cards(session, ms)) == 1


# --- 4. a tree member gets no PR of its own ----------------------------------------


def test_open_pr_refuses_a_ticket_that_ships_in_its_trees_pr(session, workspace, tree):
    _, child = tree
    with pytest.raises(ValueError, match="integration/ms"):
        create_ticket_pull_request(session, child)
