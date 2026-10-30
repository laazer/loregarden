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

from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    RunStatus,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.branch_names import chat_session_branch
from loregarden.services.chat_branch_sweep import sweep_all_chat_branches, sweep_chat_branches
from loregarden.services.chat_worktree import resolve_chat_execution_root
from loregarden.services.directory_release import DirectoryReleaseFailed, ReleaseDisposition
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.worktree_lifecycle import KeepReason, RemovalFailure
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
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


_KEPT_PREFIX = "its worktree is kept: "


def _stated_cause(reason: str) -> str:
    """The refusal a kept-reason names, without the gate's sentence after it.

    `_kept_because` writes `its worktree is kept: <refusal> (<detail>)`. The
    detail is the gate's own wording and may describe the tree truthfully
    (a STILL_PARKED outcome says "its checkout is parked at …"), so asking
    whether a premise "fired" reads the cause, not the whole line.
    """
    assert reason.startswith(_KEPT_PREFIX), reason
    return reason[len(_KEPT_PREFIX) :].split(" (", 1)[0]


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
    assert [name for name, _ in result.kept] == [branch]
    assert KeepReason.UNCOMMITTED.value in result.kept[0][1]
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


def test_a_stranded_commit_is_not_reported_as_uncommitted_work(
    session, workspace, repo, remote, pruning
):
    """The sweep keeps this tree for the right reason, and says so.

    Landed by content, tree clean, branch carrying its own commit — and HEAD
    moved off the branch with a commit of its own, which no ref can reach. The
    sweep inherits the refusal from `retire_worktree`, and what it then shows
    the operator has to be the reason that actually fired: reporting
    "uncommitted work" here would send them to look for edits that are not
    there, and tell them nothing about the commit that is.
    """
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=True)
    git(root, "checkout", "-q", "--detach")
    (root / "off_the_branch.txt").write_text("committed where no ref reaches\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "interrupted rebase")
    stranded = git(root, "rev-parse", "HEAD").stdout.strip()

    result = sweep_chat_branches(session, workspace)

    assert result.removed == []
    assert [name for name, _ in result.kept] == [branch]
    assert KeepReason.STRANDED_HEAD.value in result.kept[0][1]
    assert KeepReason.UNCOMMITTED.value not in result.kept[0][1]
    assert branch in _remote_branches(remote)
    assert root.is_dir()
    assert git(root, "rev-parse", "HEAD").stdout.strip() == stranded


def test_a_checkout_the_server_could_not_remove_keeps_its_branch(
    session, workspace, repo, remote, pruning
):
    """AC4 — the branch outlives a checkout that would not go.

    This is the sweep's most dangerous shape, because every refusal above has
    already passed: the work landed, the tree is clean, no premise fired. Only
    the removal failed. Deleting the ref here would leave a checkout of a branch
    that exists nowhere — not locally, not on the remote — so the one thing on
    disk holding those commits would be the thing the sweep just stopped
    tracking. It is also the shape with no `KeepReason` to report, which is how
    a failed removal came to read as a completed one.
    """
    chat, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=True)

    # lg-external-harness-1026 migrated the seam: `remove_checkout` no longer
    # answers a value a caller can forget to read. It raises, carrying
    # lg-external-harness-1165's `CleanupRefusal` member and the parked path,
    # and `retire_worktree` is the one place that catches it — so the stub
    # raises too, and the shape this test guards is unchanged.
    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=f"{root}.parked",
        ),
    ):
        result = sweep_chat_branches(session, workspace)

    assert result.removed == []
    assert [name for name, _ in result.kept] == [branch]
    reason = result.kept[0][1]
    # The reason names what happened, not a premise that never fired: a sweep
    # log saying "uncommitted changes" about a permission error sends the
    # operator looking for edits that are not there.
    assert RemovalFailure.CLEANUP_FAILED.value in reason
    assert _stated_cause(reason) == RemovalFailure.CLEANUP_FAILED.value
    assert _stated_cause(reason) not in {member.value for member in KeepReason}
    # Local and remote both survive, and so does the checkout and the row.
    assert branch in git(repo, "branch", "--format=%(refname:short)").stdout
    assert branch in _remote_branches(remote)
    assert root.is_dir()
    assert (
        session.exec(select(Worktree).where(Worktree.chat_session_id == chat.id)).one().state
        == WorktreeState.ACTIVE
    )


# --------------------------------------------------------------------------
# Adversarial: a failed removal beside a real one, in one sweep
# — lg-external-harness-954, test-break
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stuck_first", [True, False])
def test_one_checkout_that_will_not_go_does_not_spare_the_branch_beside_it(
    session, workspace, repo, remote, pruning, stuck_first
):
    """AC4 at the volume the sweep runs at, with a per-tree failure.

    The single-thread failure test above stubs `remove_checkout` to one refusal for
    the whole sweep, so every answer in that run is the same answer: a sweep
    that bailed on the first tree it could not retire, or that decided one
    tree's fate from another's outcome, passes it unchanged. Here one thread's
    removal fails and the other's really succeeds, so each branch's fate has to
    come from its own `Retirement`.

    Both orders are run, and that is the point rather than thoroughness for its
    own sake: with the stuck tree last, a `break` in place of the `continue` is
    invisible — verified by mutation, where it survived this module's whole
    pre-existing suite *and* the first draft of this test. The sweep walks rows
    in whatever order the table hands back, so a failure that silently ends the
    pass would leak every branch behind it on a real boot and leak none in a
    test that happened to create the stuck thread second.

    Both threads satisfy every premise and both have landed, which is what makes
    this the dangerous shape: the only thing separating them is whether the
    seam raised, and dropping a ref on the wrong side of it leaves a checkout
    of a branch that exists nowhere.
    """
    first, second = ("Will not go", "Went fine") if stuck_first else ("Went fine", "Will not go")
    threads = {}
    for title, code in ((first, "r1"), (second, "r2")):
        chat, _, branch, root = _thread_that_wrote(
            session, workspace, repo, landed=True, title=title, code=code
        )
        threads[title] = (chat, branch, root)

    stuck_chat, stuck_branch, stuck_root = threads["Will not go"]
    _, clean_branch, clean_root = threads["Went fine"]
    stuck_row = session.exec(
        select(Worktree).where(Worktree.chat_session_id == stuck_chat.id)
    ).one()
    real_cleanup = WorktreeService.remove_checkout

    def refuse_only_the_stuck_one(self, worktree):
        # Never `False`: the seam raises now, and any value it returns reads
        # as "the checkout is gone", so a stub that kept returning `False`
        # would be read as a successful removal.
        if worktree.id == stuck_row.id:
            raise DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(stuck_root),
                parked_path=f"{stuck_root}.parked",
            )
        return real_cleanup(self, worktree)

    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        autospec=True,
        side_effect=refuse_only_the_stuck_one,
    ):
        result = sweep_chat_branches(session, workspace)

    assert result.removed == [clean_branch]
    assert [name for name, _ in result.kept] == [stuck_branch]
    assert RemovalFailure.CLEANUP_FAILED.value in result.kept[0][1]
    assert _stated_cause(result.kept[0][1]) == RemovalFailure.CLEANUP_FAILED.value
    assert _stated_cause(result.kept[0][1]) not in {member.value for member in KeepReason}

    local = git(repo, "branch", "--format=%(refname:short)").stdout
    assert stuck_branch in local
    assert clean_branch not in local
    assert stuck_branch in _remote_branches(remote)
    assert clean_branch not in _remote_branches(remote)
    assert stuck_root.is_dir()
    assert not clean_root.exists()
    assert stuck_row.state == WorktreeState.ACTIVE


# ===========================================================================
# lg-external-harness-1026 — the sweep stops pre-checking, and starts
# reporting what the gate parked.
# ===========================================================================


def test_the_sweep_no_longer_runs_its_own_landed_pre_check(
    session, workspace, repo, remote, pruning
):
    """`branch_work_has_landed` is now enforced *inside* the gate.

    Two implementations of "did this land" is two answers to one question, and
    they drift the first time either moves. The pre-check went; the sweep asks
    `retire_worktree` and reports what it says.
    """
    import inspect

    from loregarden.services import chat_branch_sweep

    source = inspect.getsource(chat_branch_sweep)
    assert "branch_work_has_landed" not in source, (
        "the sweep keeps a second copy of the receipt check"
    )


def test_an_unlanded_chat_branch_is_still_kept_and_its_ref_survives(
    session, workspace, repo, remote, pruning
):
    """The behaviour the pre-check used to provide, now provided by the gate.

    Deleting the pre-check must not delete the protection: an unlanded chat
    branch's checkout stays, its local ref stays, and its remote ref stays.
    """
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=False)

    result = sweep_chat_branches(session, workspace)

    assert result.removed == []
    assert [name for name, _ in result.kept] == [branch]
    assert root.is_dir()
    assert branch in _remote_branches(remote)
    assert branch in git(repo, "branch", "--format=%(refname:short)").stdout


def test_a_parked_outcome_is_reported_with_its_exact_path_and_keeps_both_refs(
    session, workspace, repo, remote, pruning
):
    """The loud case. The directory is neither where it was nor deleted, and
    the parked path is the only thing that knows where it went — so the sweep's
    own report carries it rather than leaving it in a log line."""
    _, _, branch, root = _thread_that_wrote(session, workspace, repo, landed=True)
    parked = f"{root}.parked-feedface"

    with mock.patch.object(
        WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(root),
            parked_path=parked,
        ),
    ):
        result = sweep_chat_branches(session, workspace)

    assert result.removed == []
    assert parked in result.kept[0][1]
    assert branch in _remote_branches(remote)
    assert branch in git(repo, "branch", "--format=%(refname:short)").stdout
