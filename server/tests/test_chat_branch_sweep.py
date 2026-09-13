"""What the chat branch sweep removes, and — mostly — what it refuses to.

A rail that commits and pushes on every acting turn leaves a branch per
conversation behind. The sweep clears them, which makes it the one piece of this
feature that destroys something, so the tests that matter here are the refusals:
a branch whose work has not landed, a tree holding uncommitted changes, a
workspace that never opted in. Each of those is a way for a sweep to eat work
nobody got back.

The landed case is asserted against a real remote, because "did the ref actually
go" is not a question a mock can answer.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    RunStatus,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.chat_branch_sweep import sweep_all_chat_branches, sweep_chat_branches
from loregarden.services.chat_worktree import resolve_chat_execution_root
from loregarden.services.git_branch import chat_session_branch
from loregarden.services.orchestration_profile import GitAutomationConfig
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A repo with a real bare remote, so a deleted ref is observably deleted."""
    repo = make_repo(tmp_path)
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


@pytest.fixture(name="remote")
def remote_fixture(tmp_path):
    return tmp_path / "origin.git"


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="loregarden", name="loregarden", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="pruning")
def pruning_fixture(monkeypatch):
    """The shipped policy, minus the parts these tests do not exercise."""
    monkeypatch.setattr(
        "loregarden.services.chat_branch_sweep.resolve_git_automation",
        lambda _workspace: GitAutomationConfig(
            commit=True, push=True, prune_landed_chat_branches=True
        ),
    )


def _acting_thread(session, workspace, title="Rename the pane editor", code="r1"):
    chat = BaxterChatSession(workspace_id=workspace.id, title=title)
    run = AgentRun(
        run_code=code,
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key="home-chat",
        status=RunStatus.SUCCEEDED,
    )
    session.add(chat)
    session.add(run)
    session.commit()
    session.refresh(chat)
    session.refresh(run)
    return chat, run


def _thread_that_wrote(session, workspace, repo, *, landed: bool, **kwargs):
    """A thread with a committed, pushed branch — optionally merged into main."""
    chat, run = _acting_thread(session, workspace, **kwargs)
    root = resolve_chat_execution_root(session, run, chat, workspace)
    branch = chat_session_branch(chat)
    (root / f"{branch.replace('/', '-')}.txt").write_text("work\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"work from {branch}")
    git(root, "push", "-q", "-u", "origin", branch)
    if landed:
        # Squash-merged, as this repository's PRs are: the branch's own commits
        # never appear on main, only their combined content.
        git(repo, "merge", "-q", "--squash", branch)
        git(repo, "commit", "-q", "-m", f"squash: {branch}")
    return chat, run, branch, root


def _remote_branches(remote) -> str:
    return git(remote, "branch", "--format=%(refname:short)").stdout


# --------------------------------------------------------------------------
# What it removes
# --------------------------------------------------------------------------


def test_a_landed_branch_is_removed_locally_and_from_the_remote(
    session, workspace, repo, remote, pruning
):
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=True)
    assert branch in _remote_branches(remote)

    result = sweep_chat_branches(session, workspace)

    assert result.removed == [branch]
    assert result.remote_failures == []
    assert branch not in git(repo, "branch", "--format=%(refname:short)").stdout
    assert branch not in _remote_branches(remote)
    assert not root.is_dir()


def test_the_worktree_row_is_retired_rather_than_left_pointing_at_nothing(
    session, workspace, repo, pruning
):
    chat, _, _, _ = _thread_that_wrote(session, workspace, repo, landed=True)

    sweep_chat_branches(session, workspace)

    worktree = session.exec(select(Worktree).where(Worktree.chat_session_id == chat.id)).one()
    assert worktree.state == WorktreeState.CLEANUP


def test_a_branch_never_pushed_is_still_removed_locally(session, workspace, repo, pruning):
    """No upstream means nothing to clear remotely — not a reason to keep the ref."""
    chat, run = _acting_thread(session, workspace)
    root = resolve_chat_execution_root(session, run, chat, workspace)
    branch = chat_session_branch(chat)
    (root / "local_only.txt").write_text("work\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "local only")
    git(repo, "merge", "-q", "--squash", branch)
    git(repo, "commit", "-q", "-m", f"squash: {branch}")

    result = sweep_chat_branches(session, workspace)

    assert result.removed == [branch]
    assert result.remote_failures == []


# --------------------------------------------------------------------------
# What it refuses — the half that matters
# --------------------------------------------------------------------------


def test_work_that_has_not_landed_is_left_alone(session, workspace, repo, remote, pruning):
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=False)

    result = sweep_chat_branches(session, workspace)

    assert result.removed == []
    assert [name for name, _ in result.kept] == [branch]
    assert "not yet in" in result.kept[0][1]
    assert branch in _remote_branches(remote)
    assert root.is_dir()


def test_a_tree_holding_uncommitted_work_keeps_its_branch(
    session, workspace, repo, remote, pruning
):
    """Landed by content, but the tree has since been written to again.

    Deleting here would take edits that exist in exactly one place. The branch
    looks finished from outside, which is precisely why the check has to be of
    the tree rather than of the ref.
    """
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=True)
    (root / "not_yet_committed.txt").write_text("work nobody has seen\n")

    result = sweep_chat_branches(session, workspace)

    assert result.removed == []
    assert result.kept == [(branch, "its worktree holds uncommitted work")]
    assert branch in _remote_branches(remote)
    assert (root / "not_yet_committed.txt").exists()


def test_a_workspace_that_did_not_opt_in_is_untouched(
    session, workspace, repo, remote, monkeypatch
):
    """`push` must not imply this. Publishing is not permission to delete."""
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=True)
    monkeypatch.setattr(
        "loregarden.services.chat_branch_sweep.resolve_git_automation",
        lambda _workspace: GitAutomationConfig(commit=True, push=True),
    )

    result = sweep_chat_branches(session, workspace)

    assert result.examined == 0
    assert branch in _remote_branches(remote)
    assert root.is_dir()


def test_a_repository_with_no_base_ref_removes_nothing(session, workspace, pruning, monkeypatch):
    """Unprovable is not proven. Without a base, nothing can be shown to have landed."""
    monkeypatch.setattr("loregarden.services.chat_branch_sweep.git_base_ref", lambda _root: None)

    result = sweep_chat_branches(session, workspace)

    assert result.removed == []


def test_a_ticket_worktree_is_not_a_chat_worktree(session, workspace, repo, pruning):
    """The sweep selects on `chat_session_id`, and must not widen to every tree."""
    from loregarden.services.ticket_worktree import resolve_execution_root
    from tests.worktree_helpers import make_ticket

    ticket = make_ticket(session, workspace)
    run = AgentRun(
        run_code="t1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    root = resolve_execution_root(session, run, ticket, workspace)

    result = sweep_chat_branches(session, workspace)

    assert result.examined == 0
    assert root.is_dir()


# --------------------------------------------------------------------------
# Across workspaces
# --------------------------------------------------------------------------


def test_one_unreadable_workspace_does_not_stop_the_others(session, workspace, repo, pruning):
    """Best-effort per workspace, matching the rest of the reconciliation pass."""
    broken = Workspace(slug="gone", name="gone", repo_path="/nonexistent/repo")
    session.add(broken)
    session.commit()
    _, _, branch, _ = _thread_that_wrote(session, workspace, repo, landed=True)

    removed = sweep_all_chat_branches(session)

    assert removed == 1
    assert branch not in git(repo, "branch", "--format=%(refname:short)").stdout


def test_two_threads_are_judged_independently(session, workspace, repo, remote, pruning):
    _, _, landed, _ = _thread_that_wrote(
        session, workspace, repo, landed=True, title="Finished", code="r1"
    )
    _, _, open_branch, _ = _thread_that_wrote(
        session, workspace, repo, landed=False, title="Still going", code="r2"
    )

    result = sweep_chat_branches(session, workspace)

    assert result.removed == [landed]
    assert [name for name, _ in result.kept] == [open_branch]
    assert landed not in _remote_branches(remote)
    assert open_branch in _remote_branches(remote)


def test_an_unreachable_remote_does_not_hold_the_boot_open(
    session, workspace, repo, pruning, monkeypatch
):
    """The sweep runs at startup, and this is its only network call.

    Without a bound, an unreachable remote holds boot open for git's own retry
    behaviour, once per landed branch. The timeout is reported like any other
    refusal: the local ref still goes, because its content landed either way.
    """
    import subprocess

    from loregarden.services import chat_branch_sweep

    _, _, branch, _ = _thread_that_wrote(session, workspace, repo, landed=True)
    real_run_git = chat_branch_sweep.run_git

    def hang_on_delete(args, **kwargs):
        if args[:1] == ["push"] and "--delete" in args:
            raise subprocess.TimeoutExpired(cmd=["git", *args], timeout=20)
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(chat_branch_sweep, "run_git", hang_on_delete)

    result = chat_branch_sweep.sweep_chat_branches(session, workspace)

    assert result.removed == [branch]
    assert len(result.remote_failures) == 1
    assert "did not answer" in result.remote_failures[0][1]
    assert branch not in git(repo, "branch", "--format=%(refname:short)").stdout
