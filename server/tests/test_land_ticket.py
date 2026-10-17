"""Landing a finished ticket on its target (lg-milestone-that-768)."""

from __future__ import annotations

import dataclasses
import json
import logging
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

#: lg-milestone-that-928's fallback is logged by the helper that could not answer.
NOCO_LOGGER = "loregarden.services.git_merge_noco"


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


def test_staged_work_in_the_worktree_survives_the_refresh(
    session, workspace, repo, milestone, tmp_path
):
    """A staged addition is the operator's, and the repair may not discard it.

    `read-tree -m -u HEAD` is a *one-tree* read-tree, whose documented meaning
    is "reset the index to this tree". A staged addition is in neither the
    index's tree nor `HEAD`, so it is dropped from the index *and* the working
    tree at rc=0 — no refusal, nothing on the `LandResult`, nothing blocked
    (lg-milestone-that-920). The two-tree form applies the ref move instead.

    `_advance_the_target` is what makes the refresh write a commit, which is
    the only leg that reaches the repair at all; without it this exercises
    `already_contained` and passes on the broken code too.
    """
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-l-23")
    _advance_the_target(repo, target)
    tree = _worktree_on(repo, ticket.branch, tmp_path / "wt-23")
    (tree / "staged.txt").write_text("staged\n")
    git(tree, "add", "staged.txt")

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE
    assert (tree / "staged.txt").exists(), "the staged addition was discarded from the worktree"
    assert (tree / "staged.txt").read_text() == "staged\n"
    assert "A  staged.txt" in git(tree, "status", "--porcelain").stdout, (
        "it is still staged, not merely left on disk"
    )
    # The refresh really ran, so the test cannot pass by the repair never happening.
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


# --- landed_sha has one meaning (lg-milestone-that-928) ----------------------
#
# **D**: `landed_sha` is the FIRST commit on `landed_branch` that contains the
# ticket's work — the merge commit when a merge brought the branch in, the
# branch tip when `landed_branch` fast-forwarded to it. One meaning, the same
# whether the landing ran in the workflow or was made by hand in git, because
# D is a property of the git graph and not of which code path observed it.
#
# 782 recorded `26be6aea` (the landing merge commit) and then `41f216f9` (the
# branch tip) for one landing. #578 stopped the overwrite; it did not stop the
# first write from being wrong, because `_landing_on_record` only suppresses
# the re-write when `landed_sha` is already set. The by-hand leg — a person
# made the landing merge, so the row was never written — still records the
# source tip that `merge_tips` hands back for an already-contained branch.


@pytest.fixture(name="other_milestone")
def other_milestone_fixture(session, workspace):
    """A second subtree, so the two legs below land on two separate targets."""
    ms = Ticket(
        external_id="lg-ms-928",
        workspace_id=workspace.id,
        title="Other milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@dataclasses.dataclass(frozen=True)
class _Landing:
    """What one leg recorded, and the two commits it could have recorded."""

    leg: str
    target: str
    #: The commit that actually brought the branch onto the target.
    merge: str
    #: The ticket branch's tip — the wrong answer, and the one 782 ended with.
    tip: str
    #: `ticket.landed_sha` after the landing.
    recorded: str
    #: `LandResult.landed_sha` from the same landing.
    returned: str


def _merge_by_hand(repo, target, source):
    """A real `git merge --no-ff` of `source` into `target`, in a throwaway worktree.

    What a person does when they resolve a landing conflict themselves: the
    landing merge is in git before any loregarden code sees it, and the ticket
    row still says nothing about it.
    """
    tree = repo.parent / f"byhand-{target.replace('/', '-')}"
    git(repo, "worktree", "add", "-q", str(tree), target)
    try:
        git(tree, "merge", "--no-ff", "-q", "-m", f"Merge {source} into {target}", source)
        return _sha(tree, "HEAD")
    finally:
        git(repo, "worktree", "remove", "--force", str(tree))


def _landed_by_hand(session, workspace, repo, milestone, external_id):
    """A ticket whose landing merge exists in git, with `landed_sha` still empty.

    Returns the ticket, its target, its branch tip and the landing merge. The
    target is moved on past the merge afterwards, so a helper that answered
    with the target's tip instead of the merge commit cannot pass by accident.

    Nothing here calls `land_ticket`: this is fixture, so a failure in it is an
    ERROR and cannot be mistaken for the defect under test.
    """
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, external_id)
    merge = _merge_by_hand(repo, target, ticket.branch)
    commit_on(repo, target, f"{external_id}-sibling.txt", "another ticket\n")
    assert ticket.landed_sha == "", "the by-hand leg is the one where the row was never written"
    assert tip in _parents(repo, merge), "the fixture's merge did not bring the branch in"
    assert _sha(repo, target) != merge, "the target was not moved on past the merge"
    return ticket, target, tip, merge


@pytest.fixture(name="landed_both_ways")
def landed_both_ways_fixture(session, workspace, repo, milestone, other_milestone):
    """The same ticket-shaped work landed twice: once by the workflow, once by hand.

    Two tickets over two targets, not one ticket twice: the first landing
    changes the git graph the second would be observed against. What is
    compared is therefore the *shape* of what each leg recorded — each must be
    the first commit on its own target containing its own branch — which is
    the invariant AC-3 is about.
    """
    workflow_ticket, workflow_target, workflow_tip = _ticket_with_work(
        session, workspace, repo, milestone, "lg-l-928-workflow"
    )
    workflow_result = land_ticket(session, workflow_ticket, workspace)
    workflow_merge = _sha(repo, workflow_target)
    session.refresh(workflow_ticket)
    assert workflow_result.ok, f"the workflow leg did not land: {workflow_result.detail}"
    assert workflow_tip in _parents(repo, workflow_merge), "the workflow leg made no merge commit"

    byhand_ticket, byhand_target, byhand_tip, byhand_merge = _landed_by_hand(
        session, workspace, repo, other_milestone, "lg-l-928-byhand"
    )
    byhand_result = land_ticket(session, byhand_ticket, workspace)
    session.refresh(byhand_ticket)
    assert byhand_result.ok, f"the by-hand leg did not land: {byhand_result.detail}"
    assert byhand_result.skipped is LandSkip.ALREADY_LANDED, "the by-hand merge was re-merged"

    return (
        _Landing(
            leg="workflow",
            target=workflow_target,
            merge=workflow_merge,
            tip=workflow_tip,
            recorded=workflow_ticket.landed_sha,
            returned=workflow_result.landed_sha,
        ),
        _Landing(
            leg="by hand",
            target=byhand_target,
            merge=byhand_merge,
            tip=byhand_tip,
            recorded=byhand_ticket.landed_sha,
            returned=byhand_result.landed_sha,
        ),
    )


def test_both_legs_record_the_commit_that_landed_the_branch(landed_both_ways):
    """R6.1. The cross-leg invariant, in one assertion.

    A pair of per-leg tests, each asserting its own leg's expected value, would
    *both* pass under the bug — which is how it survived #578. What has to be
    pinned is that the two legs agree, and that they agree on the merge commit
    rather than on the branch tip (both legs being wrong the same way would
    satisfy equality alone).
    """
    legs = landed_both_ways
    recorded = {leg.leg: leg.recorded for leg in legs}
    merges = {leg.leg: leg.merge for leg in legs}
    tips = {leg.leg: leg.tip for leg in legs}

    assert recorded == merges and all(recorded[name] != tips[name] for name in recorded), (
        "one landing, one meaning: each leg must record the first commit on its "
        "target that contains its branch, which is the merge commit — never the "
        "branch tip.\n"
        + "\n".join(
            f"  {leg.leg} leg on {leg.target}: recorded {leg.recorded or '<empty>'}, "
            f"merge commit {leg.merge}, branch tip {leg.tip}"
            for leg in legs
        )
    )


def test_the_by_hand_leg_records_the_landing_merge_not_the_branch_tip(
    session, workspace, repo, milestone
):
    """R4.1. The red-to-green case: `landed_sha` empty, the merge already in git.

    `merge_tips` reports `already_contained` with the *source* tip, and
    `_landing_on_record` cannot suppress a write that has nothing to compare
    against, so this leg wrote the branch tip. The row, and the value handed
    back to the caller, must both be the commit that landed it.
    """
    ticket, target, tip, merge = _landed_by_hand(
        session, workspace, repo, milestone, "lg-l-928-row"
    )
    target_tip = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is LandSkip.ALREADY_LANDED
    session.refresh(ticket)
    assert ticket.landed_branch == target
    assert ticket.landed_sha == merge, (
        f"the row must hold the landing merge {merge}, not the branch tip {tip} "
        f"and not the target's tip {target_tip}: got {ticket.landed_sha or '<empty>'}"
    )
    assert result.landed_sha == merge, (
        f"the returned value must be the row's: row {ticket.landed_sha}, "
        f"returned {result.landed_sha or '<empty>'}, branch tip {tip}"
    )


def test_relanding_returns_the_commit_the_row_already_holds(session, workspace, repo, milestone):
    """R4.2. The MCP payload, the `TICKET_LANDED` event and the row name one commit.

    On the already-recorded leg the guard from #578 correctly leaves the row
    alone, and then `LandResult` carried `outcome.sha` — the branch tip — so
    one landing was described three different ways depending on who was asked.
    """
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-928-again")
    first = land_ticket(session, ticket, workspace)
    merge = _sha(repo, target)
    assert first.ok and first.landed_sha == merge, "the first landing must merge"

    second = land_ticket(session, ticket, workspace)

    session.refresh(ticket)
    assert second.ok and second.skipped is LandSkip.ALREADY_LANDED
    assert ticket.landed_sha == merge, "#578's guard: the row is left alone"
    assert second.landed_sha == merge, (
        f"the payload must agree with the row: row {ticket.landed_sha}, "
        f"returned {second.landed_sha}, branch tip {tip}"
    )


def test_a_landing_commit_that_cannot_be_resolved_falls_back_loudly(
    session, workspace, repo, milestone, caplog
):
    """R4.6. The fallback is reachable only through a logged failure.

    When git cannot answer which commit first contained the branch, recording
    the source tip is the best left — but "it failed" must not be
    indistinguishable from "it landed at the tip". The only route to the
    fallback is a '' from `first_containing`, which always logs; the call site
    adds no second, quieter one.
    """
    from loregarden.services import git_merge_noco as module

    ticket, target, tip, merge = _landed_by_hand(
        session, workspace, repo, milestone, "lg-l-928-blind"
    )
    real = module.run_git

    def no_ancestry_path(args, **kwargs):
        if args and args[0] == "rev-list" and "--ancestry-path" in list(args):
            return subprocess.CompletedProcess(
                list(args), 128, stdout="", stderr="fatal: rev-list is on fire"
            )
        return real(args, **kwargs)

    caplog.clear()
    with (
        caplog.at_level(logging.WARNING, logger=NOCO_LOGGER),
        mock.patch.object(module, "run_git", side_effect=no_ancestry_path),
    ):
        result = land_ticket(session, ticket, workspace)

    session.refresh(ticket)
    assert result.ok and result.skipped is LandSkip.ALREADY_LANDED
    assert ticket.landed_sha == tip, (
        f"with no answer from git the source tip {tip} is what is left; the real "
        f"landing commit was {merge}, which nothing here could see: got "
        f"{ticket.landed_sha or '<empty>'}"
    )
    assert result.landed_sha == ticket.landed_sha, "the payload still agrees with the row"
    logged = [
        record.getMessage()
        for record in caplog.records
        if record.name == NOCO_LOGGER and record.levelno >= logging.WARNING
    ]
    assert logged, (
        "a landing recorded at the branch tip because git could not be asked must "
        "say so; silently it reads exactly like a fast-forward landing"
    )


# --- adversarial: the legs R4 does not reach (test-break, 928) --------------


def _noco_warnings(caplog):
    """Every WARNING-or-worse line `git_merge_noco` logged, by message."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == NOCO_LOGGER and record.levelno >= logging.WARNING
    ]


def test_a_target_already_at_the_branch_tip_records_the_tip_and_says_nothing(
    session, workspace, repo, milestone, caplog
):
    """The second legitimate empty range, reached through `land_ticket` itself.

    `merge_tips` short-circuits on `source_sha == target_sha` as well as on
    ancestry, so the already-contained leg is taken for a target that *is* the
    branch tip. Under D the tip is the right answer here — it is the first
    commit on the target containing the work — and the test-break interest is
    that it must arrive as an answer: recorded, returned, and with nothing
    logged at WARNING. An implementation that distinguished "empty rev-list"
    from "could not answer" only by the presence of a merge commit would
    record the right sha here and shout about it, which makes R4.6's warning
    worthless as a signal.
    """
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-l-928-ff")
    git(repo, "update-ref", f"refs/heads/{target}", tip)
    assert _sha(repo, target) == tip, "the fixture did not fast-forward the target"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        result = land_ticket(session, ticket, workspace)

    session.refresh(ticket)
    assert result.ok and result.skipped is LandSkip.ALREADY_LANDED
    assert ticket.landed_sha == tip, (
        f"a target that is the branch tip first contained the work at {tip}: got "
        f"{ticket.landed_sha or '<empty>'}"
    )
    assert result.landed_sha == ticket.landed_sha, "the payload still agrees with the row"
    assert _noco_warnings(caplog) == [], (
        "a fast-forwarded landing is an answer, not a fallback; warning here makes "
        f"the R4.6 warning meaningless: {_noco_warnings(caplog)}"
    )


def test_a_by_hand_landing_git_can_answer_logs_nothing(session, workspace, repo, milestone, caplog):
    """R4.6's control: the warning only means something if the success leg is quiet.

    `test_a_landing_commit_that_cannot_be_resolved_falls_back_loudly` asserts a
    WARNING when git could not answer. On its own that assertion is satisfied
    by an implementation that warns on *every* already-contained landing, at
    which point "it failed" and "it landed" are once again spelled the same.
    """
    ticket, _target, _tip, merge = _landed_by_hand(
        session, workspace, repo, milestone, "lg-l-928-quiet"
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        result = land_ticket(session, ticket, workspace)

    session.refresh(ticket)
    assert result.ok and ticket.landed_sha == merge
    assert _noco_warnings(caplog) == [], (
        "git answered, so nothing failed; a warning on the success leg is the same "
        f"alarm as the failure leg: {_noco_warnings(caplog)}"
    )


def test_a_by_hand_landing_recorded_once_does_not_move_when_landed_again(
    session, workspace, repo, milestone
):
    """Determinism across repeats: the by-hand leg's own 782 scenario.

    782 is a sha that changed on a *second* landing. The first write is what
    R4.1 fixes; this pins that the fixed write then survives the guard from
    #578, which is the half that was already shipped. Landing a third time
    changes nothing either — the row is the same commit on every call, and so
    is the value handed back.
    """
    ticket, _target, tip, merge = _landed_by_hand(
        session, workspace, repo, milestone, "lg-l-928-stable"
    )

    first = land_ticket(session, ticket, workspace)
    session.refresh(ticket)
    after_first = ticket.landed_sha
    second = land_ticket(session, ticket, workspace)
    session.refresh(ticket)
    third = land_ticket(session, ticket, workspace)
    session.refresh(ticket)

    assert after_first == merge, f"the first landing wrote {after_first or '<empty>'}, not {merge}"
    assert ticket.landed_sha == merge, (
        f"a re-landing moved the row to {ticket.landed_sha}; 782 is exactly this, "
        f"with the branch tip {tip}"
    )
    assert {first.landed_sha, second.landed_sha, third.landed_sha} == {merge}, (
        "one landing, one commit, however often it is asked: "
        f"{first.landed_sha!r}, {second.landed_sha!r}, {third.landed_sha!r}; "
        f"merge {merge}, branch tip {tip}"
    )
