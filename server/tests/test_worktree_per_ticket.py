"""One worktree per ticket, reused by every stage of that ticket.

The parallel-queue path cuts a worktree per *run*, which is right for N
competing attempts and wrong for a pipeline: a ticket's twelve stages would
each get their own tree and the later ones would not see the earlier ones'
work. These tests pin the reuse, and pin that reuse never resets the ticket's
branch back to its parent.
"""

import subprocess
from pathlib import Path

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    RunStatus,
    StageStatus,
    Ticket,
    WorkItemType,
    Workspace,
    WorktreeState,
)
from loregarden.services.parallel_stage import prepare_tree_for_parallel_stage
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session
from tests.worktree_helpers import (
    at_terminal_stage,
    commit_on,
    git,
    head_branch,
    make_repo,
    make_run,
    make_ticket,
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
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(session, workspace)


def test_first_stage_creates_the_ticket_worktree_on_the_ticket_branch(
    session, workspace, ticket, repo
):
    service = WorktreeService(session, repo_path=str(repo))

    worktree = service.get_or_create_for_ticket(
        ticket, make_run(session, workspace, ticket, "r1").id
    )

    assert worktree is not None
    assert worktree.ticket_id == ticket.id
    assert worktree.branch == "loregarden/lg-1-add-the-thing"
    assert Path(worktree.worktree_path).is_dir()
    assert head_branch(worktree.worktree_path) == "loregarden/lg-1-add-the-thing"
    # The shared checkout keeps its own branch — the whole point of isolation.
    assert head_branch(repo) == "main"


def test_a_later_stage_reuses_the_same_worktree_row_and_path(session, workspace, ticket, repo):
    service = WorktreeService(session, repo_path=str(repo))

    first = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r1").id)
    second = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r2").id)

    assert first is not None and second is not None
    assert second.id == first.id
    assert second.worktree_path == first.worktree_path


def test_reuse_keeps_the_work_the_earlier_stage_committed(session, workspace, ticket, repo):
    """`worktree add -B` would reset the ticket branch to main and throw the
    earlier stage's commits away. Reuse must not go near the branch."""
    service = WorktreeService(session, repo_path=str(repo))
    first = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r1").id)
    assert first is not None
    (Path(first.worktree_path) / "stage-one.txt").write_text("work\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "stage one"]):
        subprocess.run(["git", *args], cwd=first.worktree_path, check=True, capture_output=True)

    second = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r2").id)

    assert second is not None
    assert (Path(second.worktree_path) / "stage-one.txt").exists()


def test_a_worktree_whose_directory_vanished_is_retired_and_replaced(
    session, workspace, ticket, repo
):
    """A removed directory must not be handed back as a working cwd, and the
    branch it still holds in git's admin data must be freed for the new one."""
    service = WorktreeService(session, repo_path=str(repo))
    first = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r1").id)
    assert first is not None
    subprocess.run(
        ["git", "worktree", "remove", "--force", first.worktree_path],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    second = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r2").id)

    assert second is not None
    assert second.id != first.id
    assert Path(second.worktree_path).is_dir()
    assert first.state == WorktreeState.CLEANUP
    assert second.branch == first.branch


def test_two_tickets_get_two_worktrees(session, workspace, ticket, repo):
    other = Ticket(
        external_id="LG-2",
        workspace_id=workspace.id,
        title="Other thing",
        branch="loregarden/lg-2-other-thing",
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    service = WorktreeService(session, repo_path=str(repo))

    one = service.get_or_create_for_ticket(ticket, make_run(session, workspace, ticket, "r1").id)
    two = service.get_or_create_for_ticket(other, make_run(session, workspace, other, "r2").id)

    assert one is not None and two is not None
    assert one.worktree_path != two.worktree_path
    assert head_branch(two.worktree_path) == "loregarden/lg-2-other-thing"


def test_a_ticket_worktree_gets_the_parent_checkout_s_node_modules(
    session, repo, workspace, ticket
):
    """A gate needs a toolchain git will never put in a worktree.

    node_modules is gitignored, so `git worktree add` never creates one. The
    client gate then ran `npx oxlint` against a tree with no oxlint, npx went to
    the network on every gate and blew the 300s budget — which reached the
    orchestrator as a code failure and was handed to the stage's own agent,
    re-running a stage that had already passed. No agent can install a toolchain
    it cannot see, so every ticket looped on every transition.
    """
    modules = Path(repo) / "client" / "node_modules"
    modules.mkdir(parents=True)
    (modules / "marker.txt").write_text("installed", encoding="utf-8")

    service = WorktreeService(session, repo_path=str(repo))
    worktree = service.get_or_create_for_ticket(
        ticket, make_run(session, workspace, ticket, "r1").id
    )

    assert worktree is not None
    linked = Path(worktree.worktree_path) / "client" / "node_modules" / "marker.txt"
    assert linked.exists(), "the gate's toolchain never reached the worktree"


def test_linking_is_best_effort_when_the_parent_has_nothing_installed(
    session, repo, workspace, ticket
):
    """A checkout with no node_modules must still get a worktree."""
    service = WorktreeService(session, repo_path=str(repo))

    worktree = service.get_or_create_for_ticket(
        ticket, make_run(session, workspace, ticket, "r1").id
    )

    assert worktree is not None
    assert not (Path(worktree.worktree_path) / "client" / "node_modules").exists()


# ---------------------------------------------------------------------------
# lg-milestone-that-779 (B): an existing worktree picks up a prerequisite that
# landed after the tree was cut.
#
# 769 added the target-branch refresh (`git_branch._refresh_onto`, reached
# through `ensure_ticket_branch(start_point=...)`), but both dispatch paths
# skipped the call entirely when the run executed in the ticket's own worktree.
# `test_cut_from_target.py` covers the refresh on *creation*; the reuse path
# above returns the existing tree untouched, so a dependent whose prerequisite
# landed mid-flight never saw that work until a brand-new worktree was cut.
# ---------------------------------------------------------------------------


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    """A milestone parent, which is what gives the ticket an integration branch.

    `target_branch_name` returns the base branch for a ticket that is its own
    subtree root, so a parentless ticket would refresh from `main` and never
    exercise the branch a sibling's work actually lands on.
    """
    ms = Ticket(
        external_id="LG-MS",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@pytest.fixture(name="dependent")
def dependent_fixture(session, workspace, milestone):
    ticket = Ticket(
        external_id="LG-9",
        workspace_id=workspace.id,
        title="Dependent",
        branch="loregarden/lg-9-dependent",
        parent_ticket_id=milestone.id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _sha(cwd, ref="HEAD") -> str:
    return git(cwd, "rev-parse", ref).stdout.strip()


def _ancestor(cwd, ancestor: str, descendant: str) -> bool:
    from loregarden.services.git_subprocess import scrubbed_git_env

    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=cwd,
            env=scrubbed_git_env(),
        ).returncode
        == 0
    )


def _dispatch(session, workspace, ticket, code: str):
    """One stage dispatch's tree preparation, as the single-agent path does it."""
    return CliAgentExecutor(session)._prepare_tree(
        make_run(session, workspace, ticket, code),
        ticket,
        workspace,
        workspace_root=Path(workspace.repo_path),
        skip_git_branch=False,
        advance_workflow=False,
    )


def _existing_worktree(session, workspace, ticket) -> Path:
    """The ticket's worktree, cut by a first dispatch and holding its own commit.

    The state every test below starts from: a tree that already exists, so the
    refresh-on-creation path cannot be what makes the assertion pass.
    """
    root, stopped = _dispatch(session, workspace, ticket, "r1")
    assert stopped is None
    assert root != Path(workspace.repo_path), "the first dispatch must cut a worktree"
    (root / "own.txt").write_text("the ticket's own earlier stage\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "stage one")
    return root


def test_an_existing_worktree_picks_up_a_prerequisite_that_landed_since_it_was_cut(
    session, workspace, dependent, repo
):
    """AC4/AC11. The commit must be reachable from the worktree's HEAD after the
    next dispatch — not after the tree is thrown away and re-cut."""
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    assert not (root / "sibling.txt").exists(), (
        "fixture: the worktree must not already have the work, or this proves nothing"
    )

    second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None
    assert second == root, "the ticket's stages share one tree; the refresh happens in it"
    assert _ancestor(root, landed, "HEAD"), "the landed prerequisite is reachable from HEAD"
    assert (root / "sibling.txt").exists()
    assert _ancestor(root, own, "HEAD") and (root / "own.txt").exists(), (
        "and the ticket's own earlier commits survived the refresh"
    )
    # S3: the integration-branch bookkeeping stays in the shared checkout, so
    # the refresh must not have moved it off its own branch.
    assert head_branch(repo) == "main"
    assert head_branch(root) == dependent.branch


def test_a_dirty_worktree_is_left_alone_and_the_stage_still_runs(
    session, workspace, dependent, repo, caplog
):
    """AC5/AC11. Merging over uncommitted work is how it gets lost. The tree is
    dirty precisely because an agent is mid-stage there, so the run proceeds
    un-refreshed and the existing warning is the record.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    (root / "in-flight.txt").write_text("an agent is still writing this\n")
    assert git(root, "status", "--porcelain").stdout.strip(), "fixture: the tree must be dirty"

    with caplog.at_level("WARNING"):
        second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None, "a dirty tree is not a reason to stop the stage"
    assert second == root
    assert (root / "in-flight.txt").read_text() == "an agent is still writing this\n", (
        "the uncommitted work is untouched"
    )
    assert _ancestor(root, own, "HEAD"), "and no commit was lost"
    assert not (root / "sibling.txt").exists(), "no merge was attempted"
    assert any("uncommitted" in r.getMessage() for r in caplog.records), (
        "a skipped refresh must not be silent"
    )


def test_a_conflicting_refresh_fails_the_run_instead_of_escaping(
    session, workspace, dependent, repo
):
    """AC5. `_refresh_onto` raises `ValueError`; the single-agent path records it
    as a failed run with the conflict named, rather than letting it escape as an
    unhandled exception now that this path is no longer skipped."""
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    (root / "shared.txt").write_text("the ticket's version\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ticket side")
    commit_on(Path(repo), target, "shared.txt", "a sibling's version\n")

    second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is not None, "a refresh that conflicts must not be swallowed"
    assert stopped.status == RunStatus.FAILED
    assert "could not take" in (stopped.stderr or ""), stopped.stderr
    assert git(root, "status", "--porcelain").stdout.strip() == "", "the merge was aborted cleanly"
    assert second == root


def test_a_target_branch_that_cannot_take_its_base_fails_the_run_too(
    session, workspace, dependent, repo
):
    """Unskipping the worktree path moves target-branch resolution onto every
    dispatch, so an integration branch that conflicts with its base — which
    previously escaped only on the rare shared-checkout path — must be recorded
    on the common one rather than raised out of the executor.
    """
    _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    commit_on(Path(repo), target, "clash.txt", "integration side\n")
    commit_on(Path(repo), "main", "clash.txt", "main side\n")

    second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is not None, "an unresolvable target branch must not escape the executor"
    assert stopped.status == RunStatus.FAILED
    assert "clash.txt" in (stopped.stderr or ""), stopped.stderr
    assert second is not None


# --- the parallel path prepares the same tree the same way ----------------------


def test_the_parallel_path_refreshes_an_existing_worktree_onto_its_target(
    session, workspace, dependent, repo
):
    """AC4(b). `prepare_tree_for_parallel_stage` returned early whenever the
    execution root was not the workspace root, which is every worktree run."""
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    run = make_run(session, workspace, dependent, "p1")

    assert prepare_tree_for_parallel_stage(session, dependent, "work", [run]) == ""

    assert _ancestor(root, landed, "HEAD"), "the members share a tree that can see the work"
    assert _ancestor(root, own, "HEAD")
    assert head_branch(repo) == "main", "and the shared checkout was not moved onto the branch"


def test_the_parallel_path_blocks_the_stage_when_the_refresh_conflicts(
    session, workspace, dependent, repo
):
    """AC5's other half: the same failure, recorded the way the parallel path
    records every checkout failure — the stage blocked before any member ran."""
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    (root / "shared.txt").write_text("the ticket's version\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ticket side")
    commit_on(Path(repo), target, "shared.txt", "a sibling's version\n")
    at_terminal_stage(session, dependent)
    run = make_run(session, workspace, dependent, "p1")

    message = prepare_tree_for_parallel_stage(session, dependent, "work", [run])

    assert "could not take" in message, message
    session.refresh(dependent)
    assert dependent.workflow_stage_status == StageStatus.BLOCKED
    assert "could not take" in (dependent.blocking_issues or ""), dependent.blocking_issues
