"""Landing a finished ticket on its target (lg-milestone-that-768)."""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest import mock

import pytest
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session
from tests.history_helpers import decision_kinds
from tests.worktree_helpers import (
    at_terminal_stage,
    blocking_text,
    commit_on,
    git,
    make_repo,
)


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
        external_id="lg-ms-768",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


def _child(session, workspace, milestone, external_id, **fields):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=milestone.id if milestone else None,
        state=TicketState.IN_PROGRESS,
        **fields,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD"):
    return git(cwd, "rev-parse", ref).stdout.strip()


def _parents(repo, sha):
    return git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def _is_ancestor(repo, ancestor, descendant):
    """Whether `ancestor` is reachable from `descendant` — git's own answer."""
    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return done.returncode == 0


def _worktree_on(repo, branch, path):
    """A second worktree holding `branch`, as a ticket's own worktree does."""
    git(repo, "worktree", "add", "-q", str(path), branch)
    return path


def _ticket_with_work(session, workspace, repo, milestone, external_id, **fields):
    """A child ticket whose branch carries one commit past the integration branch."""
    ticket = _child(session, workspace, milestone, external_id, **fields)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    tip = commit_on(repo, ticket.branch, f"{external_id}.txt", "work\n")
    return ticket, target, tip


# --- land_ticket -----------------------------------------------------------


def test_landing_merges_the_branch_into_the_target_with_a_merge_commit(
    session, workspace, repo, milestone
):
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-1")
    before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is None
    after = _sha(repo, target)
    assert after != before
    assert result.landed_sha == after
    assert set(_parents(repo, after)) == {before, tip}
    session.refresh(ticket)
    assert ticket.landed_sha == after
    assert ticket.landed_branch == target


def test_landing_is_idempotent(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-2")
    first = land_ticket(session, ticket, workspace)
    landed = _sha(repo, target)

    second = land_ticket(session, ticket, workspace)

    assert second.ok and second.skipped is LandSkip.ALREADY_LANDED
    assert _sha(repo, target) == landed, "nothing merged the second time"
    assert first.landed_sha == landed


def test_landing_never_touches_a_checkout(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-3")
    git(repo, "checkout", "-q", "-b", "somewhere-else")

    land_ticket(session, ticket, workspace)

    assert git(repo, "branch", "--show-current").stdout.strip() == "somewhere-else"
    assert git(repo, "status", "--porcelain").stdout.strip() == ""
    listed = git(repo, "worktree", "list", "--porcelain").stdout
    assert target not in listed


def test_a_ticket_with_no_branch_has_nothing_to_land(session, workspace, milestone):
    ticket = _child(session, workspace, milestone, "lg-l-4")
    result = land_ticket(session, ticket, workspace)
    assert result.ok and result.skipped is LandSkip.NO_BRANCH
    assert ticket.landed_sha == ""


def test_a_top_level_ticket_is_left_for_the_publish_leg(session, workspace, repo):
    ticket = _child(session, workspace, None, "lg-l-5")
    git(repo, "branch", ticket.branch, "main")
    commit_on(repo, ticket.branch, "solo.txt", "solo\n")
    main_before = _sha(repo, "main")

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.BASE_TARGET
    assert _sha(repo, "main") == main_before, "main is never moved locally"


def test_a_conflict_is_returned_with_its_files_and_nothing_is_written(
    session, workspace, repo, milestone
):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-6")
    commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")
    before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert not result.ok and result.conflicted
    assert result.conflicted_files == ("shared.txt",)
    assert _sha(repo, target) == before
    assert ticket.landed_sha == ""


# --- at the terminal stage ---------------------------------------------------


def _finish(session, ticket):
    """What the orchestrator does when the last stage passes: advance with no route."""
    at_terminal_stage(session, ticket)
    OrchestrationService(session).advance_stage(ticket)
    session.refresh(ticket)


def test_finishing_the_workflow_lands_the_work_and_marks_the_ticket_done(
    session, workspace, repo, milestone
):
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-7")

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert ticket.landed_branch == target
    assert tip in _parents(repo, ticket.landed_sha)


def test_landing_runs_once_per_finish(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-8")

    _finish(session, ticket)
    landed = _sha(repo, target)
    # A second pass over the finished workflow — a requeue, a re-run — lands
    # nothing new.
    OrchestrationService(session).advance_stage(ticket)

    assert _sha(repo, target) == landed
    assert len(git(repo, "rev-list", "--merges", target).stdout.split()) == 1


def test_a_conflict_blocks_the_ticket_naming_the_files(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-9")
    # The person-facing block is what this checks; the resolver is on by default.
    ticket.git_automation_json = json.dumps({"auto_resolve_conflicts": False})
    session.add(ticket)
    session.commit()
    commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")

    _finish(session, ticket)

    assert ticket.state == TicketState.BLOCKED, "not done: the work is not on its target"
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert "shared.txt" in ticket.blocking_issues
    assert ticket.landed_sha == ""


def test_landing_happens_before_done_is_derived(session, workspace, repo, milestone):
    """717's exact failure: the terminal stage wrote `done` a second before the
    orchestration's completion hook looked, and the hook skipped a done
    ticket. Landing has to happen on the way to done, not after it."""
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-10")
    seen: list[TicketState] = []
    from loregarden.services import landing as module

    real = module.land_ticket

    def observe(session_, ticket_, workspace_):
        seen.append(ticket_.state)
        return real(session_, ticket_, workspace_)

    with mock.patch.object(module, "land_ticket", side_effect=observe):
        _finish(session, ticket)

    assert seen == [TicketState.IN_PROGRESS], "landed while still in progress"
    assert ticket.state == TicketState.DONE
    assert ticket.landed_sha


def test_a_git_failure_blocks_with_its_text(session, workspace, repo, milestone):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-11")
    from loregarden.services import git_merge_noco as module

    real = module.run_git

    def broken(args, **kwargs):
        if args and args[0] == "commit-tree":
            return subprocess.CompletedProcess(
                args, 128, stdout="", stderr="fatal: object store is on fire"
            )
        return real(args, **kwargs)

    with mock.patch.object(module, "run_git", side_effect=broken):
        _finish(session, ticket)

    assert ticket.state == TicketState.BLOCKED
    assert "on fire" in blocking_text(session, ticket), "git's own words"


def test_a_workspace_with_no_repository_finishes_without_landing(session, tmp_path):
    """Test workspaces (and misconfigured ones) point at paths that are not
    repositories. Nothing ran there, so nothing lands — and the workflow must
    still finish rather than block on git's FileNotFoundError."""
    ws = Workspace(slug="no-repo", name="no repo", repo_path=str(tmp_path / "nowhere"))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(
        external_id="lg-l-12", workspace_id=ws.id, title="No repo", state=TicketState.IN_PROGRESS
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    result = land_ticket(session, ticket, ws)
    assert result.ok and result.skipped is LandSkip.NO_REPOSITORY

    _finish(session, ticket)
    assert ticket.state == TicketState.DONE


# --- refreshing the branch before the land (lg-milestone-that-801) -----------
#
# 754's shape: every stage passed, then the target moved, and the land met a
# tree no stage had seen. The refresh merges the target *into* the ticket
# branch first, with the same no-checkout plumbing the land uses, so the
# conflict (if any) lands on the branch the resolver is told to work on and the
# landed tree is the tree the gate ran against.


def _advance_the_target(repo, target):
    """Another ticket lands on the target after this one's gate passed."""
    commit_on(repo, target, "sibling.txt", "another ticket\n")
    return _sha(repo, target)


def _refusing_refresh(branch):
    """A `run_git` that fails the refresh's ref move and nothing else.

    Only the refresh moves the *ticket* branch's ref; the land moves the
    target's. A compare-and-swap refusal is one of the non-conflict failures
    AC3 names, and it leaves `conflicted_files` empty.
    """
    from loregarden.services import git_merge_noco as module

    real = module.run_git

    def broken(args, **kwargs):
        if list(args[:2]) == ["update-ref", f"refs/heads/{branch}"]:
            return subprocess.CompletedProcess(
                args, 128, stdout="", stderr="fatal: update-ref refused: the ref moved"
            )
        return real(args, **kwargs)

    return module, broken


def test_the_target_is_merged_into_the_branch_before_the_land(session, workspace, repo, milestone):
    """The ticket branch ends up containing the target it is about to land on."""
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-13")
    target_before = _advance_the_target(repo, target)

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    branch_tip = _sha(repo, ticket.branch)
    assert _is_ancestor(repo, target_before, branch_tip), (
        "the target was never brought onto the ticket branch"
    )
    # A merge, not a rebase: the branch's own commit keeps its identity, so
    # every open review delta computed against it still holds.
    assert _is_ancestor(repo, tip, branch_tip)
    assert set(_parents(repo, branch_tip)) == {tip, target_before}


def test_a_refresh_failure_blocks_with_gits_text_and_reaches_no_resolver(
    session, workspace, repo, milestone
):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-14")
    target_before = _advance_the_target(repo, target)
    module, broken = _refusing_refresh(ticket.branch)

    with mock.patch.object(module, "run_git", side_effect=broken):
        _finish(session, ticket)

    text = blocking_text(session, ticket)
    assert ticket.state == TicketState.BLOCKED
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert "update-ref refused" in text, "git's own words"
    assert "refresh" in text.lower(), "which step failed, so the land is not blamed for it"
    assert ticket.next_agent != REPAIR_AGENT_ID, "there is no conflict to resolve"
    assert "dispatched_landing_resolver" not in decision_kinds(session, ticket.id)
    assert _sha(repo, target) == target_before, "the land merge never ran"
    assert ticket.landed_sha == ""


def test_a_refresh_failure_carries_no_conflicted_files(session, workspace, repo, milestone):
    """What routes it: an empty `conflicted_files` is the non-conflict leg."""
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-15")
    _advance_the_target(repo, target)
    module, broken = _refusing_refresh(ticket.branch)

    with mock.patch.object(module, "run_git", side_effect=broken):
        result = land_ticket(session, ticket, workspace)

    assert not result.ok and not result.conflicted
    assert result.conflicted_files == ()
    assert "update-ref refused" in result.detail


def test_a_worktree_holding_the_branch_is_left_consistent(
    session, workspace, repo, milestone, tmp_path
):
    """The refreshed ref is normally checked out in the ticket's own worktree.

    `update-ref` moves it without refusing or warning, which leaves that
    worktree's index showing the incoming files as staged deletions — and the
    orchestrator commits the whole working tree when a stage finishes.
    """
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-16")
    _advance_the_target(repo, target)
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-16")

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert git(tree, "status", "--porcelain").stdout.strip() == "", (
        "a staged deletion would be swept"
    )
    assert (tree / "sibling.txt").read_text() == "another ticket\n"


def test_an_untracked_file_in_the_refreshs_way_blocks_the_landing(
    session, workspace, repo, milestone, tmp_path
):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-17")
    target_before = _advance_the_target(repo, target)
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-17")
    (tree / "sibling.txt").write_text("mine\n")

    _finish(session, ticket)

    text = blocking_text(session, ticket)
    assert ticket.state == TicketState.BLOCKED
    assert str(tree) in text, "which worktree is in the way"
    assert "sibling.txt" in text and "untracked" in text.lower(), "git's own words"
    assert ticket.next_agent != REPAIR_AGENT_ID
    assert "dispatched_landing_resolver" not in decision_kinds(session, ticket.id)
    assert _sha(repo, target) == target_before, "nothing landed over a stale worktree"
    assert (tree / "sibling.txt").read_text() == "mine\n", "their file is not discarded"


def test_unrelated_work_in_the_worktree_survives_the_refresh(
    session, workspace, repo, milestone, tmp_path
):
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-18")
    _advance_the_target(repo, target)
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-18")
    (tree / "seed.txt").write_text("edited\n")
    (tree / "notes.txt").write_text("notes\n")

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert (tree / "seed.txt").read_text() == "edited\n", "a tracked edit is not theirs to discard"
    assert (tree / "notes.txt").read_text() == "notes\n"
    assert (tree / "sibling.txt").read_text() == "another ticket\n"


def test_a_worktree_is_untouched_when_the_refresh_wrote_nothing(
    session, workspace, repo, milestone, tmp_path
):
    """No ref moved, so no index is stale and nothing may be run in there.

    A *staged* change is how this test can tell: `read-tree -m -u HEAD` resets
    the index to HEAD, so it would discard `A  staged.txt`. Measured.
    """
    ticket, _target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-19")
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-19")
    (tree / "staged.txt").write_text("staged\n")
    git(tree, "add", "staged.txt")
    before = git(tree, "status", "--porcelain").stdout

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert git(tree, "status", "--porcelain").stdout == before


def test_a_worktree_path_cannot_forge_a_branch_line(session, workspace, repo, milestone, tmp_path):
    """`--porcelain` emits a worktree's path raw, and escapes no newline in it.

    So a worktree whose path ends in a newline plus `branch
    refs/heads/<ticket branch>` makes a newline-split parse credit that line
    to the worktree listed *before* it — and `read-tree -m -u HEAD` then runs
    in a working tree on another branch, resetting its index to its own HEAD
    and discarding the staged work there. `-z` is what contains the newline.
    """
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-20")
    _advance_the_target(repo, target)
    git(repo, "branch", "bystander", "main")
    victim = _worktree_on(repo, "bystander", tmp_path / "victim")
    (victim / "theirs.txt").write_text("another ticket's uncommitted work\n")
    git(victim, "add", "theirs.txt")
    before = git(victim, "status", "--porcelain").stdout
    forged = tmp_path / f"victim\nbranch refs/heads/{ticket.branch}"
    git(repo, "worktree", "add", "-q", "--detach", str(forged))

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert git(victim, "status", "--porcelain").stdout == before, "an unrelated worktree"
    assert (victim / "theirs.txt").read_text() == "another ticket's uncommitted work\n"


def test_a_worktree_whose_directory_is_gone_does_not_stop_the_land(
    session, workspace, repo, milestone, tmp_path
):
    """git keeps listing it, with its branch, until someone prunes it.

    A stale Claude or Cursor scratchpad worktree is a common cause. `run_git`
    hands `cwd` to `subprocess.run`, so a gone directory raises
    `FileNotFoundError` before git runs at all and `check=False` does not
    absorb it. The record carries `prunable`, which is how it is skipped.
    """
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-21")
    target_before = _advance_the_target(repo, target)
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-21")
    shutil.rmtree(tree)

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE, "a worktree nobody has is not a reason to block"
    assert _is_ancestor(repo, target_before, _sha(repo, ticket.branch)), "it still refreshed"
    assert ticket.landed_sha != ""


def test_a_worktree_that_vanishes_mid_refresh_blocks_rather_than_raising(
    session, workspace, repo, milestone, tmp_path
):
    """The same directory, lost between the listing and the read-tree.

    Nothing between `run_git` and `_finish_workflow` catches an `OSError`, so
    unhandled it leaves the ticket mid-flight with no block and no landing.
    """
    from loregarden.services import land_ticket as module

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-22")
    target_before = _advance_the_target(repo, target)
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-22")
    real = module.run_git

    def vanishing(args, **kwargs):
        if list(args[:1]) == ["read-tree"]:
            raise FileNotFoundError(2, "No such file or directory", str(tree))
        return real(args, **kwargs)

    with mock.patch.object(module, "run_git", side_effect=vanishing):
        _finish(session, ticket)

    text = blocking_text(session, ticket)
    assert ticket.state == TicketState.BLOCKED
    assert str(tree) in text, "which worktree"
    assert "No such file or directory" in text, "the OS's own words"
    assert ticket.next_agent != REPAIR_AGENT_ID, "there is no conflict to resolve"
    assert _sha(repo, target) == target_before, "the land merge never ran"
