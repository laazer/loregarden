"""A ticket's branch that conflicts with its target is resolved before a person is asked.

init-pre-tinkercg-3, 2026-10-14: 918 and 920 held only 801's commits, already
on the target by squash, and 954 held seven commits of its own against a file
the target had rewritten. All three blocked at dispatch on "could not take",
and the autopilot paused. Both dispatch paths are exercised: the shared
checkout or an existing tree (`git_branch.ensure_ticket_branch`), and a tree
cut for the ticket (`WorktreeService.get_or_create_for_ticket`).
"""

from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Workspace
from loregarden.services import integration_merge_agent
from loregarden.services.branch_takes_target import MergeTurnSeat
from loregarden.services.git_branch import ensure_ticket_branch, resolve_ticket_branch
from loregarden.services.integration_merge_budget import INTEGRATION_MERGE_STAGE
from loregarden.services.worktree_service import WorktreeRefreshError, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, head_branch, make_repo, make_run, make_ticket


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(session, workspace)


def _sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def _cut(repo: Path, branch: str) -> None:
    git(repo, "branch", branch, "main")


@pytest.fixture(name="superseded")
def superseded_fixture(repo, ticket):
    """918's shape: the branch's only work reached `main` as a squash, and `main`
    then edited the file again, so a merge conflicts on history alone."""
    branch = resolve_ticket_branch(ticket)
    _cut(repo, branch)
    commit_on(repo, branch, "feature.txt", "first\n")
    commit_on(repo, "main", "feature.txt", "first\n")
    commit_on(repo, "main", "feature.txt", "first\nthen main\n")
    return branch


@pytest.fixture(name="diverged")
def diverged_fixture(repo, ticket):
    """954's shape: the branch has work of its own on a line `main` rewrote."""
    branch = resolve_ticket_branch(ticket)
    _cut(repo, branch)
    tip = commit_on(repo, branch, "shared.txt", "ticket side\n")
    commit_on(repo, "main", "shared.txt", "main side\n")
    return branch, tip


def _resolving(*_args, tree: Path, **_kwargs) -> str:
    (tree / "shared.txt").write_text("ticket side\nmain side\n")
    git(tree, "add", "shared.txt")
    return "Kept both sides."


def _merge_runs(session: Session, ticket) -> list[AgentRun]:
    return list(
        session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket.id, AgentRun.stage_key == INTEGRATION_MERGE_STAGE
            )
        ).all()
    )


def _clean(tree: Path) -> bool:
    return git(tree, "status", "--porcelain").stdout.strip() == ""


# --- the branch adds nothing its target lacks: moved, no agent ------------------------


def test_a_superseded_branch_is_moved_to_its_target_in_the_checkout(
    session, workspace, ticket, repo, superseded
):
    old = _sha(repo, superseded)

    ensure_ticket_branch(repo, ticket, start_point="main")

    assert _sha(repo, superseded) == _sha(repo, "main")
    assert head_branch(repo) == superseded
    assert (repo / "feature.txt").read_text() == "first\nthen main\n", "the files moved with it"
    assert _clean(repo), git(repo, "status", "--porcelain").stdout
    assert _sha(repo, f"{superseded}@{{1}}") == old, "the old tip stays in the reflog"
    assert _merge_runs(session, ticket) == [], "no agent turn for history alone"


def test_a_superseded_branch_is_moved_before_its_worktree_is_cut(
    session, workspace, ticket, repo, superseded
):
    run = make_run(session, workspace, ticket, "r1")

    tree = WorktreeService(session, repo_path=str(repo)).get_or_create_for_ticket(
        ticket, run.id, parent_branch="main"
    )

    assert tree is not None
    path = Path(tree.worktree_path)
    assert _sha(path, "HEAD") == _sha(repo, "main")
    assert (path / "feature.txt").read_text() == "first\nthen main\n"
    assert _clean(path)


def test_a_superseded_branch_with_work_of_its_own_is_not_moved(
    session, workspace, ticket, repo, superseded
):
    tip = commit_on(repo, superseded, "extra.txt", "only on the branch\n")

    with pytest.raises(ValueError, match="could not take"):
        ensure_ticket_branch(repo, ticket, start_point="main")

    assert _sha(repo, superseded) == tip, "a branch with unique work is never moved"
    assert _clean(repo)


# --- the branch has work of its own: the agent's merge turn -----------------------------


def test_a_conflict_is_merged_by_the_agent_in_the_checkout(
    session, workspace, ticket, repo, diverged
):
    branch, tip = diverged
    main = _sha(repo, "main")

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_resolving) as turn:
        ensure_ticket_branch(
            repo, ticket, start_point="main", seat=MergeTurnSeat(session, workspace, ticket)
        )

    head = _sha(repo, "HEAD")
    assert head == _sha(repo, branch)
    git(repo, "merge-base", "--is-ancestor", tip, head)
    git(repo, "merge-base", "--is-ancestor", main, head)
    assert (repo / "shared.txt").read_text() == "ticket side\nmain side\n"
    assert _clean(repo)
    [run] = _merge_runs(session, ticket)
    assert (run.status, run.start_head_sha) == (RunStatus.SUCCEEDED, tip)
    about = turn.call_args.kwargs["about"]
    assert ticket.external_id in about and "integration branch" not in about, about


def test_a_conflict_is_merged_by_the_agent_before_the_worktree_is_cut(
    session, workspace, ticket, repo, diverged
):
    branch, tip = diverged
    run = make_run(session, workspace, ticket, "r1")

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_resolving):
        tree = WorktreeService(session, repo_path=str(repo)).get_or_create_for_ticket(
            ticket, run.id, parent_branch="main", seat=MergeTurnSeat(session, workspace, ticket)
        )

    assert tree is not None
    path = Path(tree.worktree_path)
    assert (path / "shared.txt").read_text() == "ticket side\nmain side\n"
    git(path, "merge-base", "--is-ancestor", tip, "HEAD")
    git(path, "merge-base", "--is-ancestor", "main", "HEAD")
    assert _clean(path)


def test_a_turn_that_resolves_nothing_leaves_the_branch_and_names_why(
    session, workspace, ticket, repo, diverged
):
    """The suite's stub turn resolves nothing, as an agent that gives up does."""
    branch, tip = diverged

    with pytest.raises(ValueError, match="could not take") as raised:
        ensure_ticket_branch(
            repo, ticket, start_point="main", seat=MergeTurnSeat(session, workspace, ticket)
        )

    assert "shared.txt" in str(raised.value)
    assert _sha(repo, branch) == tip
    assert _clean(repo)
    [run] = _merge_runs(session, ticket)
    assert run.status == RunStatus.FAILED


def test_without_a_seat_a_conflict_still_blocks_and_runs_no_turn(
    session, workspace, ticket, repo, diverged
):
    branch, tip = diverged
    run = make_run(session, workspace, ticket, "r1")

    with pytest.raises(WorktreeRefreshError, match="no merge turn can run"):
        WorktreeService(session, repo_path=str(repo)).get_or_create_for_ticket(
            ticket, run.id, parent_branch="main"
        )

    assert _sha(repo, branch) == tip
    assert _merge_runs(session, ticket) == []
    assert len(git(repo, "worktree", "list").stdout.splitlines()) == 1, "no tree was cut"
