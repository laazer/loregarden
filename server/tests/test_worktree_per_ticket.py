"""One worktree per ticket, reused by every stage of that ticket.

The parallel-queue path cuts a worktree per *run*, which is right for N
competing attempts and wrong for a pipeline: a ticket's twelve stages would
each get their own tree and the later ones would not see the earlier ones'
work. These tests pin the reuse, and pin that reuse never resets the ticket's
branch back to its parent.
"""

import json
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
from loregarden.services.ticket_worktree import resolve_execution_root
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


def _dispatch(session, workspace, ticket, code: str, *, skip_git_branch: bool = False):
    """One stage dispatch's tree preparation, as the single-agent path does it.

    ``skip_git_branch`` is the flag the parallel driver passes its members
    (`_run_and_collect_parallel_results`), which is why it is a parameter here:
    it is the one skip AC4 keeps, and a test has to be able to ask for it.
    """
    return CliAgentExecutor(session)._prepare_tree(
        make_run(session, workspace, ticket, code),
        ticket,
        workspace,
        workspace_root=Path(workspace.repo_path),
        skip_git_branch=skip_git_branch,
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

    assert prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run]) == ""

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

    message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert "could not take" in message, message
    session.refresh(dependent)
    assert dependent.workflow_stage_status == StageStatus.BLOCKED
    assert "could not take" in (dependent.blocking_issues or ""), dependent.blocking_issues


# ---------------------------------------------------------------------------
# lg-milestone-that-779 (B), adversarial: what the refresh must *not* do.
#
# Unskipping `ensure_ticket_branch` for a worktree puts a merge on every stage
# dispatch of every ticket — the most-travelled path in the app. The tests
# above pin that the merge happens; these pin its blast radius, because each
# of them is a way to make them pass and still lose work.
# ---------------------------------------------------------------------------


def test_a_dispatch_with_nothing_new_on_the_target_moves_neither_tree(
    session, workspace, dependent, repo
):
    """Idempotent, and one-directional.

    Two mutants pass every test above. One merges unconditionally and leaves a
    merge commit per stage, so a twelve-stage ticket's history is eleven
    "Merge branch 'integration/...'" commits nobody made. The other merges the
    wrong way and puts the ticket's unfinished work on the integration branch,
    where its siblings would cut from it — landing is a terminal-stage
    transition (768), and nothing before it may publish.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    before_head = _sha(root)
    before_target = _sha(repo, target)
    assert not _ancestor(repo, before_head, target), (
        "fixture: the ticket's own commit must not already be on the target"
    )

    second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None
    assert second == root
    assert _sha(root) == before_head, "nothing landed, so there was nothing to merge"
    assert _sha(repo, target) == before_target, "and the target branch is not written to at all"
    assert not _ancestor(repo, before_head, target), (
        "the ticket's work reaches its target at landing, not at dispatch"
    )
    assert git(root, "status", "--porcelain").stdout.strip() == ""


def test_the_one_remaining_skip_still_skips(session, workspace, dependent, repo):
    """AC4 removes `repo_root != workspace_root` from the skip and keeps
    `skip_git_branch`, which is what the parallel driver passes every member
    (`skip_git_branch=True`) *because* `prepare_tree_for_parallel_stage`
    already did the refresh once. A mutant that drops both skips has every
    member of a fan-out merging concurrently in one tree.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    before_head = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")

    second, stopped = _dispatch(session, workspace, dependent, "r2", skip_git_branch=True)

    assert stopped is None
    assert second == root
    assert _sha(root) == before_head, "the member asked for no branch work and must get none"
    assert not _ancestor(root, landed, "HEAD")


def test_a_worktree_left_on_another_branch_is_put_back_without_losing_its_commits(
    session, workspace, dependent, repo
):
    """The ticket's branch is not where the worktree is guaranteed to be — a
    crashed stage, a resolver, or a person can leave it elsewhere, and until
    now no dispatch into a worktree looked. `ensure_ticket_branch` is also the
    call that once reset an existing ticket branch to HEAD and orphaned its
    commits (lg-milestone-that-772); it now runs on this path too, so the
    no-loss guarantee has to hold here.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    git(root, "checkout", "-q", "-b", "scratch/left-behind")
    assert head_branch(root) != dependent.branch, "fixture: the tree starts off the branch"

    second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None, stopped.stderr if stopped else None
    assert second == root
    assert head_branch(root) == dependent.branch, "the stage runs on the ticket's branch"
    assert _ancestor(root, own, "HEAD"), "and the ticket's earlier stages are still in it"
    assert (root / "own.txt").exists()
    assert _ancestor(root, landed, "HEAD")


def test_every_member_of_a_parallel_stage_shares_the_one_refreshed_tree(
    session, workspace, dependent, repo
):
    """`prepare_tree_for_parallel_stage` resolves the tree once, from `runs[0]`,
    so that the members do not race to create it. With the early return gone
    that single resolution is also the single refresh — a mutant that resolves
    or refreshes per member reintroduces the race it exists to prevent.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    runs = [make_run(session, workspace, dependent, f"p{n}") for n in range(1, 4)]

    assert prepare_tree_for_parallel_stage(session, dependent, "work", runs, stamped=runs) == ""

    assert _ancestor(root, landed, "HEAD")
    for run in runs:
        assert resolve_execution_root(session, run, dependent, workspace) == root, (
            f"{run.run_code} must execute in the tree that was prepared"
        )
    assert git(root, "rev-list", "--merges", "--count", "HEAD").stdout.strip() == "1", (
        "one refresh for the stage, so one merge commit — not one per member"
    )


def test_a_conflicting_parallel_refresh_loses_nothing_in_the_worktree(
    session, workspace, dependent, repo
):
    """The stage is blocked (above); this is what the operator finds in the tree
    afterwards. A conflicted merge left half-applied is how the resolver they
    send in next loses the ticket's side of it.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    (root / "shared.txt").write_text("the ticket's version\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ticket side")
    own = _sha(root)
    commit_on(Path(repo), target, "shared.txt", "a sibling's version\n")
    at_terminal_stage(session, dependent)

    assert "could not take" in prepare_tree_for_parallel_stage(
        session,
        dependent,
        "work",
        [make_run(session, workspace, dependent, "p1")],
        stamped=[make_run(session, workspace, dependent, "p1")],
    )

    assert git(root, "status", "--porcelain").stdout.strip() == "", (
        "the merge was aborted, not left conflicted"
    )
    assert _sha(root) == own, "and the ticket's own commit is still what HEAD points at"
    assert (root / "shared.txt").read_text() == "the ticket's version\n"
    assert head_branch(root) == dependent.branch


# ---------------------------------------------------------------------------
# lg-milestone-that-917: the third dispatch path — the handoff.
#
# `render_stage_prompt` is what `prepare_terminal_handoff` (a human's terminal)
# and `external_harness._checked_out_run_view` (an MCP harness) hand over, and
# it kept the `repo_root == workspace_root` guard the two paths above lost. So
# the one path with nobody watching handed over a tree that was never brought
# up to its target, and the operator or harness then committed on a base that
# could not see a prerequisite that had landed since the tree was cut.
#
# These assert on the same observable facts as the dispatch tests above — what
# is reachable from the worktree's HEAD afterwards — not on the prompt.
# ---------------------------------------------------------------------------


@pytest.fixture(name="agents")
def agents_fixture(session):
    """The built-in agent rows `render_stage_prompt` renders a prompt from.

    The dispatch tests above never reach the registry (`_prepare_tree` only
    prepares a tree), so this file's session is deliberately unseeded. The
    handoff path resolves `run.agent_id` through it, and an unknown agent would
    raise before the tree work this ticket is about — a green-looking red.
    """
    from loregarden.services.seed import seed_database

    seed_database(session)
    return session


def _handoff(session, workspace, ticket, code: str):
    """One terminal/harness handoff's tree preparation and prompt render.

    The single call both handoff edges make; everything they add afterwards
    (a prompt file, a run view) is downstream of the tree it returns.

    Returns `(prompt, PreparedTree)` as of lg-milestone-that-974: the branch the
    tree was brought up to leaves with the tree, so neither boundary re-derives
    it. Compatibility posture is `internal` — every caller moves to the new
    shape in that change, with no second method returning the old one.
    """
    return CliAgentExecutor(session).render_stage_prompt(
        make_run(session, workspace, ticket, code), ticket
    )


def test_render_stage_prompt_refreshes_an_existing_worktree_onto_its_target(
    session, workspace, dependent, repo, agents
):
    """AC5. The defect itself, verified the way 779's AC4 is: a commit that
    landed on the target after the tree was cut must be reachable from the
    handed-off tree's HEAD, and the tree handed over must still be the
    ticket's own worktree — refreshed, not thrown away and re-cut.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    assert not (root / "sibling.txt").exists(), (
        "fixture: the worktree must not already have the work, or this proves nothing"
    )

    _prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert tree.repo_root == root, "the handed-off tree is the ticket's worktree, not a fresh one"
    assert _ancestor(root, landed, "HEAD"), "the landed prerequisite is reachable from HEAD"
    assert (root / "sibling.txt").exists()
    assert _ancestor(root, own, "HEAD") and (root / "own.txt").exists(), (
        "and the ticket's own earlier commits survived the refresh"
    )
    assert head_branch(root) == dependent.branch
    assert head_branch(repo) == "main", "the shared checkout is not moved onto the branch"


def test_a_dirty_worktree_is_still_handed_off_unrefreshed(
    session, workspace, dependent, repo, agents, caplog
):
    """AC6. One handoff in five meets a dirty tree (7 of 39 measured). Merging
    over uncommitted work is how it gets lost, so the refresh is skipped — and
    the handoff is still handed over, because an un-refreshable tree is a
    degraded handoff, not a blocked one.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    (root / "in-flight.txt").write_text("an operator is still writing this\n")
    assert git(root, "status", "--porcelain").stdout.strip(), "fixture: the tree must be dirty"

    with caplog.at_level("WARNING"):
        prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert prompt, "the handoff is not blocked by a tree that could not be refreshed"
    assert tree.repo_root == root
    assert (root / "in-flight.txt").read_text() == "an operator is still writing this\n", (
        "the uncommitted work is untouched"
    )
    assert _sha(root) == own, "HEAD is unmoved: no merge was attempted"
    assert not _ancestor(root, landed, "HEAD")
    assert not (root / "sibling.txt").exists()
    assert any("uncommitted" in r.getMessage() for r in caplog.records), (
        "a skipped refresh must not be silent"
    )


def test_a_conflicting_handoff_refresh_raises_a_valueerror(
    session, workspace, dependent, repo, agents
):
    """AC4. `render_stage_prompt` has no run to fail and no stage to block, so
    it raises — as a `ValueError`, which is what both edges already catch (the
    route maps it to a 400, the MCP dispatcher to an `isError` result).

    `_refresh_onto`'s own `ValueError` passes through **unwrapped**: it already
    carries git's merge output, and re-wrapping would print that output twice in
    the message the route renders into a toast that clamps at four lines.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    (root / "shared.txt").write_text("the ticket's version\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ticket side")
    own = _sha(root)
    commit_on(Path(repo), target, "shared.txt", "a sibling's version\n")

    with pytest.raises(ValueError) as excinfo:
        _handoff(session, workspace, dependent, "h1")

    message = str(excinfo.value)
    assert "could not take" in message, message
    assert message.count("could not take") == 1, (
        f"the refresh's own ValueError was re-wrapped, doubling git's output: {message}"
    )
    # What the operator finds in the tree afterwards. A half-applied merge is
    # how the person sent in to resolve it loses the ticket's side.
    assert git(root, "status", "--porcelain").stdout.strip() == "", (
        "the merge was aborted, not left conflicted"
    )
    assert "<<<<<<<" not in (root / "shared.txt").read_text(), "no conflict markers were left"
    assert _sha(root) == own, "and HEAD still points at the ticket's own commit"


def test_a_target_branch_that_cannot_resolve_raises_a_valueerror_from_the_handoff(
    session, workspace, dependent, repo, agents
):
    """AC3 + AC4. `resolve_execution_root` resolves the target branch itself, so
    it raises `TargetBranchError` — a `RuntimeError`, `target_branch.py:51` —
    *before* any `ensure_ticket_branch` call. It therefore has to sit inside the
    same handler, or a `RuntimeError` escapes the route (a 500) and the harness
    (an unclassified tool crash) on a condition both already render.
    """
    _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    commit_on(Path(repo), target, "clash.txt", "integration side\n")
    commit_on(Path(repo), "main", "clash.txt", "main side\n")

    with pytest.raises(ValueError) as excinfo:
        _handoff(session, workspace, dependent, "h1")

    assert "clash.txt" in str(excinfo.value), str(excinfo.value)


def test_a_handoff_with_nothing_new_on_the_target_moves_neither_tree(
    session, workspace, dependent, repo, agents
):
    """AC7 on the handoff path. The two mutants that pass every test above:
    one merges unconditionally and leaves a merge commit per handoff, the other
    merges the wrong way and publishes the ticket's unfinished work to the
    integration branch its siblings cut from. Landing is a terminal-stage
    transition (768); no dispatch, and no handoff, may publish.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    before_head = _sha(root)
    before_target = _sha(repo, target)
    assert not _ancestor(repo, before_head, target), (
        "fixture: the ticket's own commit must not already be on the target"
    )

    _prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert tree.repo_root == root
    assert _sha(root) == before_head, "nothing landed, so there was nothing to merge"
    assert _sha(repo, target) == before_target, "and the target branch is not written to at all"
    assert not _ancestor(repo, before_head, target), (
        "the ticket's work reaches its target at landing, not at handoff"
    )
    assert git(root, "status", "--porcelain").stdout.strip() == ""


def test_a_handoff_puts_a_worktree_left_on_another_branch_back_on_the_ticket_s(
    session, workspace, dependent, repo, agents
):
    """The tree handed to an operator must be on the ticket's branch, which is
    not where a worktree is guaranteed to be — a crashed stage, a resolver or a
    person can leave it elsewhere. `prepare_terminal_handoff`'s docstring claims
    the handed-off tree is "the ticket's worktree, already on its branch"
    (AC8); until the guard goes, nothing on this path looked.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    own = _sha(root)
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    git(root, "checkout", "-q", "-b", "scratch/left-behind")
    assert head_branch(root) != dependent.branch, "fixture: the tree starts off the branch"

    _prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert tree.repo_root == root
    assert head_branch(root) == dependent.branch
    assert _ancestor(root, own, "HEAD") and (root / "own.txt").exists(), (
        "and the ticket's earlier stages are still in it"
    )
    assert _ancestor(root, landed, "HEAD")


# --- the target is resolved in the shared checkout, on all three paths ---------
#
# 779 S3: resolving a target branch WRITES refs (`ensure_integration_branch`,
# `_refresh_from_tips`), and refs are shared across every worktree of a repo.
# One resolution in the shared checkout is therefore visible in all of them,
# and resolving from a worktree instead is bookkeeping done in the wrong tree.
#
# These three assert on the argument because the AC names the argument (AC1,
# AC2) and because the two spellings are behaviourally near-identical — refs
# being shared is exactly what makes the wrong one survive every other test in
# this file. The spy calls through, so the path under test is the real one.


def _target_branch_spy(calls: list[Path]):
    from loregarden.services.target_branch import resolve_target_branch as real

    def spy(session, ticket, workspace, *, repo_root):
        calls.append(Path(repo_root))
        return real(session, ticket, workspace, repo_root=repo_root)

    return spy


def test_the_handoff_resolves_its_target_in_the_shared_checkout(
    session, workspace, dependent, repo, agents
):
    """AC1."""
    from unittest.mock import patch

    _existing_worktree(session, workspace, dependent)
    calls: list[Path] = []

    with patch("loregarden.agents.executors.cli.resolve_target_branch", _target_branch_spy(calls)):
        _handoff(session, workspace, dependent, "h1")

    assert calls == [Path(repo)], (
        f"the handoff resolved its target branch in {calls}, not the shared checkout"
    )


def test_the_dispatch_path_resolves_its_target_in_the_shared_checkout(
    session, workspace, dependent, repo
):
    """AC2(a). `_ensure_branch_or_fail` passes `repo_root=repo_root` today,
    which is the worktree on every run that has one."""
    from unittest.mock import patch

    _existing_worktree(session, workspace, dependent)
    calls: list[Path] = []

    with patch("loregarden.agents.executors.cli.resolve_target_branch", _target_branch_spy(calls)):
        _, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None
    assert calls == [Path(repo)], (
        f"the dispatch resolved its target branch in {calls}, not the shared checkout"
    )


def test_the_parallel_path_resolves_its_target_in_the_shared_checkout(
    session, workspace, dependent, repo
):
    """AC2(b)."""
    from unittest.mock import patch

    _existing_worktree(session, workspace, dependent)
    calls: list[Path] = []

    with patch(
        "loregarden.services.parallel_stage.resolve_target_branch", _target_branch_spy(calls)
    ):
        message = prepare_tree_for_parallel_stage(
            session,
            dependent,
            "work",
            [make_run(session, workspace, dependent, "p1")],
            stamped=[make_run(session, workspace, dependent, "p1")],
        )

    assert message == ""
    assert calls == [Path(repo)], (
        f"the parallel path resolved its target branch in {calls}, not the shared checkout"
    )


def test_no_dispatch_path_still_asks_whether_it_is_in_the_shared_checkout(
    session, workspace, dependent, repo
):
    """AC2's verification, as a test rather than a grep a reviewer has to run.

    The condition this ticket deletes was in three places and the two the last
    ticket fixed were found by hand. A fourth would be found the same way: by a
    stale handoff nobody noticed. The server's own source is the fixture.
    """
    import re
    from pathlib import Path as _Path

    import loregarden

    root = _Path(loregarden.__file__).parent
    pattern = re.compile(r"repo_root\s*[=!]=\s*workspace_root")
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]

    assert offenders == [], (
        "a dispatch path still decides whether to refresh by asking which tree "
        f"it is in: {offenders}"
    )


# ---------------------------------------------------------------------------
# lg-milestone-that-917, test-break: the handoff's three error contracts, and
# the one argument that decides whose work the switch is allowed to carry.
#
# The tests above cover a clean tree, a dirty tree *on its branch*, a conflict
# and an unresolvable target. Three contracts AC1/AC3/AC4 name are still
# unasserted, and each has a mutant that passes everything above: dropping
# `ignoring=`, re-wrapping `DirtyPrimaryCheckoutError` into a plain
# `ValueError`, and letting a `CalledProcessError` out unconverted.
# ---------------------------------------------------------------------------


def _record_changed_path(session, workspace, ticket, path: str) -> None:
    """Make `path` one of the ticket's own recorded paths.

    `ticket_recorded_paths` reads every run's `changed_paths_json`, which is how
    a ticket's earlier stage declares the uncommitted files it left behind.
    """
    import json

    run = make_run(session, workspace, ticket, f"recorded-{path}")
    run.changed_paths_json = json.dumps([path])
    session.add(run)
    session.commit()


def test_a_tree_left_dirty_on_another_branch_refuses_rather_than_carrying_the_work(
    session, workspace, dependent, repo, agents
):
    """AC4 on the one error the handoff must *not* re-wrap.

    A worktree can be left on another branch by a crashed stage or a person,
    and if it is also dirty, switching it to the ticket's branch would carry
    whoever's work-in-progress that is onto the ticket's branch (864). So this
    refuses — and it refuses as a `DirtyPrimaryCheckoutError`, which is a
    `ValueError` subclass *by design* (`primary_checkout.py:36`) so that the
    route's existing 400 mapping and the harness's error result both keep
    working. Re-wrapping it in a plain `ValueError` loses the dirty-path list
    the inbox card and the park path are built on, and still satisfies every
    other assertion in this file.
    """
    from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError

    root = _existing_worktree(session, workspace, dependent)
    own = _sha(root)
    (root / "someone-elses-wip.txt").write_text("not this ticket's work\n")
    git(root, "checkout", "-q", "-b", "scratch/left-behind")

    with pytest.raises(DirtyPrimaryCheckoutError) as excinfo:
        _handoff(session, workspace, dependent, "h1")

    assert "someone-elses-wip.txt" in str(excinfo.value), str(excinfo.value)
    assert excinfo.value.dirty_paths, "the refusal has to name what it refused on"
    assert head_branch(root) == "scratch/left-behind", "the branch was switched anyway"
    assert (root / "someone-elses-wip.txt").read_text() == "not this ticket's work\n"
    assert _sha(root) == own


def test_the_ticket_s_own_unfinished_work_does_not_block_its_own_handoff(
    session, workspace, dependent, repo, agents
):
    """AC1's `ignoring=ticket_recorded_paths(...)`, which is the argument that
    tells the two cases above apart.

    With the guard deleted, every handoff now runs the clean-tree check the
    dispatch path runs — so a ticket whose earlier stage left its own recorded
    files uncommitted would refuse its own handoff, on its own work, if
    `ignoring=` were dropped. Nothing else in this file notices: the argument
    only matters when the tree is both dirty and on another branch.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    landed = commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")
    (root / "stage-one-left-this.txt").write_text("this ticket's own work in progress\n")
    _record_changed_path(session, workspace, dependent, "stage-one-left-this.txt")
    git(root, "checkout", "-q", "-b", "scratch/left-behind")

    _prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert tree.repo_root == root
    assert head_branch(root) == dependent.branch, "the tree was not put back on its branch"
    assert (root / "stage-one-left-this.txt").read_text() == (
        "this ticket's own work in progress\n"
    ), "the ticket's own uncommitted work was carried across, not lost"
    # Still dirty, so AC6's skip applies and the refresh is not attempted.
    assert not _ancestor(root, landed, "HEAD")


def test_a_git_command_that_failed_outright_reaches_the_edges_as_a_valueerror(
    session, workspace, dependent, repo, agents
):
    """AC4's third clause. A `subprocess.CalledProcessError` out of the tree
    preparation — a locked index, a missing object, a `git` that is not on the
    PATH — is a `subprocess` exception, not a `ValueError`, so it escapes both
    handoff edges uncaught: a 500 from the route and an unclassified crash at
    the harness, on a condition they both already render as a refusal.

    Patched at the one call `render_stage_prompt` makes, because the real
    failures behind it (a `.git/index.lock` another process holds, a corrupt
    object) are not reproducible in a fixture without faking them anyway. The
    contract under test is the conversion, not git's reason for failing.
    """
    from unittest.mock import patch

    _existing_worktree(session, workspace, dependent)
    boom = subprocess.CalledProcessError(
        128, ["git", "checkout", dependent.branch], stderr="fatal: index.lock exists"
    )

    with patch("loregarden.agents.executors.cli.ensure_ticket_branch", side_effect=boom):
        with pytest.raises(ValueError) as excinfo:
            _handoff(session, workspace, dependent, "h1")

    assert not isinstance(excinfo.value, subprocess.SubprocessError)
    assert "128" in str(excinfo.value), (
        f"git's own failure has to survive into the message: {excinfo.value}"
    )


def test_two_handoffs_in_a_row_leave_one_merge_and_not_two(
    session, workspace, dependent, repo, agents
):
    """AC7's idempotence, with something to merge rather than nothing.

    The existing idempotence test starts from a target with nothing new on it,
    where a merge is a no-op in git itself — so an implementation that merges
    unconditionally, or with `--no-ff`, passes it. An operator copying the
    command twice (which the client's own double-fire guard exists because they
    do) must not stack a merge commit per copy on the branch they then push.
    """
    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    commit_on(Path(repo), target, "sibling.txt", "landed after the tree was cut\n")

    _handoff(session, workspace, dependent, "h1")
    after_first = _sha(root)
    count_first = git(root, "rev-list", "--count", "HEAD").stdout.strip()
    _handoff(session, workspace, dependent, "h2")

    assert _sha(root) == after_first, "the second handoff moved HEAD with nothing new to take"
    assert git(root, "rev-list", "--count", "HEAD").stdout.strip() == count_first
    assert git(root, "status", "--porcelain").stdout.strip() == ""


# ---------------------------------------------------------------------------
# lg-milestone-that-916: a refusal names the tree that is actually blocking.
#
# 779 stopped skipping `ensure_ticket_branch` for worktree runs, which made
# this path reachable for the first time: a ticket's own worktree, dirty and on
# another branch, refused with a title, a type name and an enum value that all
# say "shared checkout". The operator is sent to clean a tree that is not the
# one holding their work.
# ---------------------------------------------------------------------------


def _dirty_run(session, workspace, ticket, code: str, *, stage_key: str):
    """A run carrying a stage key, so the title's `— <stage_key>` is a real one."""
    run = make_run(session, workspace, ticket, code)
    run.stage_key = stage_key
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _worktrees_off(session, ticket):
    """No worktree, so the run falls back to the shared checkout."""
    ticket.git_automation_json = json.dumps({"worktree": False})
    session.add(ticket)
    session.commit()
    return ticket


def _error_artifacts(session, ticket):
    from loregarden.models.domain import Artifact, ArtifactKind
    from sqlmodel import select

    return session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.ERROR)
    ).all()


def test_a_dirty_ticket_worktree_on_another_branch_is_refused_as_itself(
    session, workspace, dependent, repo
):
    """AC3/UX-4/UX-5. The tree holding the uncommitted work is the ticket's own
    worktree, so that is what the refusal has to name — in the title the
    operator reads first, and in the `use` value stored beside it.
    """
    root = _existing_worktree(session, workspace, dependent)
    git(root, "checkout", "-q", "-b", "scratch/left-behind")
    (root / "left-in-flight.txt").write_text("an agent was mid-stage here\n")
    assert head_branch(root) != dependent.branch, "fixture: the tree starts off the branch"
    assert git(root, "status", "--porcelain").stdout.strip(), "fixture: and it is dirty"
    run = _dirty_run(session, workspace, dependent, "r2", stage_key="implement")

    _, stopped = CliAgentExecutor(session)._prepare_tree(
        run,
        dependent,
        workspace,
        workspace_root=Path(workspace.repo_path),
        skip_git_branch=False,
        advance_workflow=False,
    )

    assert stopped is not None, "a refused checkout parks the stage, it does not run it"
    assert stopped.status == RunStatus.CANCELLED
    [error] = _error_artifacts(session, dependent)
    assert error.title == "Ticket worktree refused — implement"
    content = json.loads(error.content_json)
    assert content["use"] == "ticket_worktree_branch_checkout"
    assert content["repo_root"] == str(root), "and it names the tree that is actually dirty"
    assert "left-in-flight.txt" in content["dirty_paths"]


# ---------------------------------------------------------------------------
# lg-milestone-that-918: one target-branch resolution per dispatch, and lazy.
#
# 779 put the refresh on every dispatch and paid for the answer twice. A
# sequential dispatch arrives at `_prepare_tree` with no worktree assigned, so
# `resolve_execution_root` resolves the target (to cut the tree from it) and
# `_ensure_branch_or_fail` resolves the same target again (to refresh onto it).
# Resolving is a *write* against refs every worktree of the repository shares —
# `ensure_integration_branch`, `_refresh_from_tips`, and behind them a blocking
# merge turn with an inbox card — so the second reading is not free and not
# harmless.
#
# The counter below is installed on BOTH import bindings on purpose. Every
# consumer binds the name at import (`cli.py`, `ticket_worktree.py`,
# `parallel_stage.py`), so a spy on `services.target_branch` itself counts
# zero. That is how `test_the_dispatch_path_resolves_its_target_in_the_shared_checkout`
# above asserts a one-element call list while two resolutions actually happen:
# it sees one of the two bindings.
#
# And the one resolution has to stay *lazy*. Three dispatch paths resolve zero
# or one times today (see the table in the ticket), one of them the dirty-park
# path where the run never starts at all. Hoisting the resolution to the top of
# `_prepare_tree` would make those paths write shared refs where today they
# write nothing, which is a behaviour change wearing a refactor's clothes.
# ---------------------------------------------------------------------------


def _resolution_spies():
    """`(calls, (cli_patch, worktree_patch))` — call-through spies on both bindings.

    `calls` holds the `repo_root` of every resolution either binding made, so
    `len(calls)` is the total per dispatch and each entry says which tree it
    was resolved against.

    `**extra` is here only for the `ticket_worktree` binding, which passes
    `merge_conflicts=`. The `cli` binding must be called with exactly
    `(session, ticket, workspace, *, repo_root)`: `_target_branch_spy` above is
    frozen to that shape, AC7 forbids editing it, and a memo that passed
    `merge_conflicts=` through would raise `TypeError` inside it.
    """
    from unittest.mock import patch

    from loregarden.services.target_branch import resolve_target_branch as real

    calls: list[Path] = []

    def spy(session, ticket, workspace, *, repo_root, **extra):
        calls.append(Path(repo_root))
        return real(session, ticket, workspace, repo_root=repo_root, **extra)

    return calls, (
        patch("loregarden.agents.executors.cli.resolve_target_branch", spy),
        patch("loregarden.services.ticket_worktree.resolve_target_branch", spy),
    )


def test_one_dispatch_resolves_its_target_branch_once(session, workspace, dependent, repo):
    """AC1. Counted across both import bindings, so the count cannot hide in
    the binding the spy is not on.

    Non-vacuous: against the code as it stands *before* lg-milestone-that-918
    this same test reports **2** — `resolve_execution_root` resolves through
    `ticket_worktree`'s binding and `_ensure_branch_or_fail` resolves again
    through `cli`'s. A spy on `services.target_branch.resolve_target_branch`
    instead of these two would report **0** and pass against anything.
    """
    _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    assert target != "main", (
        "fixture: the target must differ from the base branch, or a resolution "
        "issues no git at all and costs nothing to repeat (R8)"
    )
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        _, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None, stopped.stderr if stopped else None
    assert calls == [Path(repo)], (
        f"one dispatch resolved its target branch {len(calls)} times, in {calls}: "
        "the worktree cut and the branch refresh must share the one answer, "
        "and resolve it in the shared checkout"
    )


def test_a_dirty_shared_checkout_parks_without_resolving_a_target(
    session, workspace, dependent, repo
):
    """AC2(a)/AC8. Worktrees off and the checkout holding someone else's work:
    the run never starts, so nothing may be written to shared refs on its
    behalf. Zero resolutions today, and zero after.

    A mutant that hoists the resolution to the top of `_prepare_tree` fails
    here: it would file an inbox card and refresh an integration branch for a
    run that is parked before it executes a line. It also pins that a refused
    checkout *parks* — cancelled, not failed with a checkout message.
    """
    _worktrees_off(session, dependent)
    (Path(repo) / "someone-elses.txt").write_text("an operator was mid-edit here\n")
    assert git(repo, "status", "--porcelain").stdout.strip(), "fixture: the checkout is dirty"
    run = _dirty_run(session, workspace, dependent, "r2", stage_key="implement")
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        _, stopped = CliAgentExecutor(session)._prepare_tree(
            run,
            dependent,
            workspace,
            workspace_root=Path(workspace.repo_path),
            skip_git_branch=False,
            advance_workflow=False,
        )

    assert stopped is not None, "a refused checkout parks the stage"
    assert stopped.status == RunStatus.CANCELLED, (
        "a dirty checkout is a fact about the machine, not a checkout failure"
    )
    assert not (stopped.stderr or "").startswith("Failed to checkout branch: ")
    assert calls == [], f"a run that never starts resolved {len(calls)} target branches: {calls}"


def test_a_member_that_already_has_its_tree_resolves_no_target(session, workspace, dependent, repo):
    """AC2(b). The parallel driver assigns the worktree and passes
    `skip_git_branch=True` because `prepare_tree_for_parallel_stage` already
    refreshed the one tree for the whole stage. Both of `_prepare_tree`'s
    consumers return early, so nothing resolves — and a hoist would make every
    member of a fan-out resolve and merge against shared refs concurrently.
    """
    from loregarden.models.domain import AgentRun
    from sqlmodel import select

    _existing_worktree(session, workspace, dependent)
    first = session.exec(select(AgentRun).where(AgentRun.run_code == "r1")).one()
    assert first.worktree_id, "fixture: the first dispatch must have cut a worktree"
    member = make_run(session, workspace, dependent, "p1")
    member.worktree_id = first.worktree_id
    session.add(member)
    session.commit()
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        root, stopped = CliAgentExecutor(session)._prepare_tree(
            member,
            dependent,
            workspace,
            workspace_root=Path(workspace.repo_path),
            skip_git_branch=True,
            advance_workflow=False,
        )

    assert stopped is None
    assert root != Path(repo), "the member runs in the tree it was handed"
    assert calls == [], f"a member handed its tree resolved {len(calls)} target branches: {calls}"


def test_a_skipped_branch_step_still_resolves_once_to_cut_from_the_target(
    session, workspace, dependent, repo
):
    """AC2(c). `skip_git_branch` skips the *refresh*, never the cut:
    `test_the_one_remaining_skip_still_skips` above depends on
    `resolve_execution_root` still resolving, because a worktree cut from the
    base instead of the target cannot see a sibling's landed work.

    So this path resolves exactly once, today and after. A mutant that guards
    the memo with `not skip_git_branch` reports zero here.
    """
    _existing_worktree(session, workspace, dependent)
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        _, stopped = _dispatch(session, workspace, dependent, "r2", skip_git_branch=True)

    assert stopped is None
    assert calls == [Path(repo)], (
        f"the skipped-refresh path resolved {len(calls)} target branches: {calls}"
    )


def test_a_target_that_cannot_be_resolved_fails_the_dispatch_with_the_same_words(
    session, workspace, dependent, repo
):
    """AC8. The memo is evaluated by `_prepare_tree` to build `start_point`, so
    a `TargetBranchError` now surfaces from `_prepare_tree`'s own handler
    rather than from inside `_ensure_branch_or_fail`. Both spellings complete
    the run FAILED with `Failed to checkout branch: <exc>`, and that is what
    the operator reads either way.
    """
    _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    commit_on(Path(repo), target, "clash.txt", "integration side\n")
    commit_on(Path(repo), "main", "clash.txt", "main side\n")

    _, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is not None, "an unresolvable target must not escape the executor"
    assert stopped.status == RunStatus.FAILED
    assert (stopped.stderr or "").startswith("Failed to checkout branch: "), stopped.stderr
    assert "clash.txt" in (stopped.stderr or ""), stopped.stderr


def test_ensure_branch_or_fail_is_handed_one_tree_and_a_resolved_start_point(session):
    """AC3. `_ensure_branch_or_fail` has one job — put `repo_root` on the
    ticket's branch — so it takes neither the second root it used to resolve
    against nor the `skip` flag whose evaluation would force the resolution to
    happen eagerly. `_prepare_tree`'s own signature is unchanged, because three
    test call sites pass it by keyword.
    """
    import inspect

    func = CliAgentExecutor._ensure_branch_or_fail
    assert list(inspect.signature(func).parameters) == [
        "self",
        "run",
        "ticket",
        "workspace",
        "repo_root",
        "start_point",
        "advance_workflow",
    ]
    body = inspect.getsource(func).replace(func.__doc__ or "\0", "")
    assert "resolve_target_branch" not in body, "it is handed the answer, it does not resolve one"
    assert "workspace_root" not in body, "and it knows about exactly one tree"
    assert list(inspect.signature(CliAgentExecutor._prepare_tree).parameters) == [
        "self",
        "run",
        "ticket",
        "workspace",
        "workspace_root",
        "skip_git_branch",
        "advance_workflow",
    ], "tests/worktree_helpers.py and this module call `_prepare_tree` by keyword"


# ---------------------------------------------------------------------------
# lg-milestone-that-918, test-break: two mutants the tests above let through.
#
# (1) A memo cached on the executor instead of per dispatch. Every test above
#     dispatches once per `CliAgentExecutor`, so `self._target = ...` counts one
#     resolution in all of them and passes. Assumption A2 of REQ-1 says the memo
#     must be created inside `_prepare_tree` for exactly this reason, and the
#     defect it hides is not the cached *name* — that string does not change —
#     but the resolution's write: `_refresh_from_tips` is what carries the base
#     branch onto the integration branch, so a skipped second resolution makes
#     everything that landed on `main` since the first one invisible.
#
# (2) The fast path implemented but never reached from a dispatch. REQ-5's A5
#     assumes it is, and AC6's whole re-measurement rests on that assumption;
#     R6 says a figure that does not move is evidence of this. Nothing above
#     asks the dispatch path itself whether the saving happened, so a fast path
#     gated on something a dispatch never satisfies passes AC4 and AC5 — which
#     both call `ensure_ticket_branch` directly — and silently buys nothing.
# ---------------------------------------------------------------------------


@pytest.fixture(name="dispatch_git")
def dispatch_git_fixture():
    """Every git argv a dispatch starts, counted at `subprocess.Popen`.

    At `Popen` and not at `run_git` because the dispatch path crosses
    `ticket_worktree`, `target_branch`, `git_branch`, `git_merge_noco` and
    `worktree_snapshot`, and each of them binds `run_git` by name.

    `reconcile_timer._sweep` is held off for the reason `test_stage_git_snapshot.py`
    holds it off: a sweep firing inside the window counts its own `for-each-ref`
    against the dispatch (R7).
    """
    from unittest import mock

    from loregarden.services import reconcile_timer

    argvs: list[list[str]] = []
    original = subprocess.Popen.__init__

    def counting(self, args, *rest, **kwargs):
        argv = [str(part) for part in list(args)]
        if argv[:1] == ["git"]:
            argvs.append(argv)
        original(self, args, *rest, **kwargs)

    with (
        mock.patch.object(subprocess.Popen, "__init__", counting),
        mock.patch.object(reconcile_timer, "_sweep", return_value=[]),
    ):
        yield argvs


def _dispatch_asking(argvs: list[list[str]], *tokens: str) -> list[list[str]]:
    return [argv for argv in argvs if all(token in argv for token in tokens)]


def test_a_second_dispatch_through_one_executor_resolves_its_own_target(
    session, workspace, dependent, repo
):
    """AC1/AC2, REQ-1 A2. The memo's lifetime is one dispatch.

    Both dispatches go through the *same* `CliAgentExecutor`, which is the only
    thing the tests above never do. A memo cached on `self` reports zero
    resolutions for the second dispatch, and the base branch's new commit never
    reaches the integration branch — so the ticket's tree builds on a `main`
    that is a dispatch out of date, with nothing logged and nothing failed.
    """
    root = _existing_worktree(session, workspace, dependent)
    executor = CliAgentExecutor(session)

    first, stopped = executor._prepare_tree(
        make_run(session, workspace, dependent, "r2"),
        dependent,
        workspace,
        workspace_root=Path(workspace.repo_path),
        skip_git_branch=False,
        advance_workflow=False,
    )
    assert stopped is None and first == root

    landed = commit_on(Path(repo), "main", "from-main.txt", "landed on the base branch\n")
    assert not _ancestor(root, landed, "HEAD"), (
        "fixture: the base commit must not already be in the tree, or this proves nothing"
    )
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        second, stopped = executor._prepare_tree(
            make_run(session, workspace, dependent, "r3"),
            dependent,
            workspace,
            workspace_root=Path(workspace.repo_path),
            skip_git_branch=False,
            advance_workflow=False,
        )

    assert stopped is None, stopped.stderr if stopped else None
    assert second == root
    assert calls == [Path(repo)], (
        f"the second dispatch through one executor resolved {len(calls)} target "
        f"branches, in {calls}: the memo is per dispatch, not per executor"
    )
    assert _ancestor(root, landed, "HEAD"), (
        "resolving is the write that carries the base branch onto the target; a "
        "memo that outlives its dispatch skips it and the tree never sees main"
    )


def test_a_dispatch_with_nothing_landed_does_no_checkout_and_no_merge(
    session, workspace, dependent, repo, dispatch_git
):
    """AC4/AC6, REQ-5 A5. The saving has to happen on the measured path.

    AC4 and AC5 call `ensure_ticket_branch` directly, so a fast path whose gate
    a real dispatch never satisfies passes both of them and buys nothing — and
    `GIT_CALLS_PER_STAGE` would then re-measure at roughly 15, which R6 says is
    evidence about REQ-4 rather than a number to accept. This asks the dispatch
    itself: a tree already on its branch with nothing landed since must be left
    alone, with one `merge-base --is-ancestor` in place of the checkout, the
    stat and the merge.
    """
    root = _existing_worktree(session, workspace, dependent)
    before = _sha(root)
    dispatch_git.clear()  # the first dispatch's own branch work must not be counted

    second, stopped = _dispatch(session, workspace, dependent, "r2")

    assert stopped is None and second == root
    assert _sha(root) == before
    assert _dispatch_asking(dispatch_git, "checkout") == [], (
        "a tree already on its branch is not checked out again"
    )
    assert _dispatch_asking(dispatch_git, "merge", "--no-edit") == [], (
        "and nothing landed, so nothing is merged"
    )
    assert _dispatch_asking(dispatch_git, "status", "--porcelain") == [], (
        "nor stat-ed to find out whether it could be"
    )
    assert _dispatch_asking(dispatch_git, "merge-base", "--is-ancestor") != [], (
        "the one question that establishes it must actually be asked"
    )


# ---------------------------------------------------------------------------
# lg-milestone-that-1200: one target-branch resolution per *parallel* stage.
#
# `prepare_tree_for_parallel_stage` still holds the pair 918 removed from the
# single-agent dispatch path: `resolve_execution_root` resolves the target to
# cut the worktree from it, and the `ensure_ticket_branch` call one line later
# resolves the same target for the same ticket against the same refs. Resolving
# is a *write* — `ensure_integration_branch`, `_refresh_from_tips`, and behind
# them a blocking merge turn that files an inbox card — so the second reading is
# two more git processes and a second merge attempt against refs every worktree
# of the repository shares.
#
# The spies below are installed on `parallel_stage`'s and `ticket_worktree`'s
# bindings, and on no others. `_resolution_spies()` above cannot be reused: it
# patches `agents.executors.cli`'s binding, which this function never reaches,
# so it would report 1 whether or not the fix landed. A spy on
# `services.target_branch.resolve_target_branch` itself reports 0 against
# anything, because every consumer binds the name at import.
#
# And the one resolution has to stay *lazy*. `resolve_execution_root` returns
# early when the run already has a worktree, and raises
# `DirtyPrimaryCheckoutError` from `_require_clean_fallback` when worktrees are
# off and the shared checkout holds someone else's work — so there is a path
# that resolves zero times today. A hoist would make that path write shared
# refs where today it writes nothing, which is a behaviour change wearing a
# refactor's clothes. That is what `test_a_dirty_shared_checkout_parks_the_
# parallel_stage_without_resolving` is for; the count on the already-set path
# is 1 either way and kills nothing on its own.
# ---------------------------------------------------------------------------


def _parallel_resolution_spies():
    """`(calls, (parallel_patch, worktree_patch))` — call-through spies on both bindings.

    `calls` holds the `repo_root` of every resolution either binding made, so
    `len(calls)` is the total per parallel stage and each entry says which tree
    it was resolved against.

    `**extra` is here only for the `ticket_worktree` binding, which passes
    `merge_conflicts=`. The `parallel_stage` binding must be called with
    exactly `(session, ticket, workspace, *, repo_root)`: `_target_branch_spy`
    above is frozen to that shape by AC3, and a memo that passed
    `merge_conflicts=` through would raise `TypeError` inside it.
    """
    from unittest.mock import patch

    from loregarden.services.target_branch import resolve_target_branch as real

    calls: list[Path] = []

    def spy(session, ticket, workspace, *, repo_root, **extra):
        calls.append(Path(repo_root))
        return real(session, ticket, workspace, repo_root=repo_root, **extra)

    return calls, (
        patch("loregarden.services.parallel_stage.resolve_target_branch", spy),
        patch("loregarden.services.ticket_worktree.resolve_target_branch", spy),
    )


def test_one_parallel_stage_resolves_its_target_branch_once(session, workspace, dependent, repo):
    """AC1. Counted across both import bindings, so the count cannot hide in
    the binding the spy is not on.

    Non-vacuous: against the code as it stands *before* lg-milestone-that-1200
    this same test reports **2** — `resolve_execution_root` resolves through
    `ticket_worktree`'s binding to cut the tree from the target, and the
    `ensure_ticket_branch` call one line later resolves it again through
    `parallel_stage`'s to refresh onto it. Removing `resolve_target=once` from
    the `resolve_execution_root` call restores that 2.

    No worktree is assigned to `runs[0]` and `config.worktree` is left on, so
    this is the path that resolves on both surfaces: the cut and the refresh.
    """
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    assert target != "main", (
        "fixture: the target must differ from the base branch, or a resolution "
        "issues no git at all and costs nothing to repeat (R7)"
    )
    run = make_run(session, workspace, dependent, "p1")
    assert not run.worktree_id, "fixture: the cut must still be ahead of us"
    calls, (parallel_spy, worktree_spy) = _parallel_resolution_spies()

    with parallel_spy, worktree_spy:
        message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert message == "", message
    assert len(calls) == 1, (
        f"one parallel stage resolved its target branch {len(calls)} times, in "
        f"{calls}: the worktree cut and the branch refresh must share the one answer"
    )
    assert calls == [Path(repo)], f"and it must be resolved in the shared checkout, not in {calls}"


def test_a_parallel_stage_whose_tree_exists_resolves_only_for_the_refresh(
    session, workspace, dependent, repo
):
    """AC2(a). `runs[0]` already has its worktree, so `resolve_execution_root`
    takes its early return and resolves nothing; the one resolution is the
    `ensure_ticket_branch` call site's.

    This pins that the early return stays free. It does *not* kill the hoist
    mutant — that call site is unconditional, so a hoisted memo and a lazy one
    both report 1 here. `test_a_dirty_shared_checkout_parks_the_parallel_stage_
    without_resolving` is the one that does.
    """
    from loregarden.models.domain import AgentRun
    from sqlmodel import select

    _existing_worktree(session, workspace, dependent)
    member = make_run(session, workspace, dependent, "p1")
    first = session.exec(select(AgentRun).where(AgentRun.run_code == "r1")).one()
    member.worktree_id = first.worktree_id
    assert member.worktree_id, "fixture: the first dispatch must have cut a worktree"
    session.add(member)
    session.commit()
    calls, (parallel_spy, worktree_spy) = _parallel_resolution_spies()

    with parallel_spy, worktree_spy:
        message = prepare_tree_for_parallel_stage(
            session, dependent, "work", [member], stamped=[member]
        )

    assert message == "", message
    assert len(calls) == 1, (
        f"a stage handed its tree resolved {len(calls)} target branches: {calls} — "
        "the early return must resolve nothing and the refresh exactly once"
    )
    assert calls == [Path(repo)], (
        f"and the one resolution belongs to the shared checkout, not {calls}"
    )


def test_a_dirty_shared_checkout_parks_the_parallel_stage_without_resolving(
    session, workspace, dependent, repo
):
    """AC2(b)/AC4(b). Worktrees off and the checkout holding someone else's
    work: `_require_clean_fallback` raises before anything is resolved, so the
    stage is parked and nothing may be written to shared refs on its behalf.
    Zero resolutions today, and zero after.

    This is the mutant kill. A memo evaluated above the `resolve_execution_root`
    call reports 1 here: it would file an inbox card and refresh an integration
    branch for a stage whose members never start.
    """
    _worktrees_off(session, dependent)
    (Path(repo) / "someone-elses.txt").write_text("an operator was mid-edit here\n")
    assert git(repo, "status", "--porcelain").stdout.strip(), "fixture: the checkout is dirty"
    at_terminal_stage(session, dependent)
    run = make_run(session, workspace, dependent, "p1")
    calls, (parallel_spy, worktree_spy) = _parallel_resolution_spies()

    with parallel_spy, worktree_spy:
        message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert calls == [], f"a stage that never starts resolved {len(calls)} target branches: {calls}"
    assert message.startswith("Failed to checkout branch: "), message
    assert "someone-elses.txt" in message, message
    session.refresh(dependent)
    assert dependent.workflow_stage_status == StageStatus.BLOCKED
    [error] = _error_artifacts(session, dependent)
    assert error.title == "Shared checkout refused — work", (
        "the ERROR artifact the operator looks for is still filed, with the paths"
    )
    content = json.loads(error.content_json)
    assert "someone-elses.txt" in content["dirty_paths"]


@pytest.mark.parametrize("tree_exists", [False, True], ids=["cut", "already-cut"])
def test_an_unresolvable_target_blocks_the_parallel_stage_from_either_surface(
    session, workspace, dependent, repo, tree_exists
):
    """AC4(a). Handing the memo to `resolve_execution_root` moves where the
    error comes from: on the path that still has to cut a tree the raise now
    surfaces from inside `resolve_execution_root`'s `resolve_target()` call,
    and on the path whose tree exists it surfaces from the caller's own
    evaluation of `start_point`. Both spellings must block the stage with the
    same words, so the operator reads the same thing either way.

    The `except (ValueError, subprocess.CalledProcessError, TargetBranchError)`
    tuple is what catches it on both, and must not be narrowed, widened or
    reordered to make one of these pass.
    """
    from unittest.mock import patch

    from loregarden.models.domain import AgentRun
    from loregarden.services.target_branch import TargetBranchError
    from sqlmodel import select

    if tree_exists:
        _existing_worktree(session, workspace, dependent)
    at_terminal_stage(session, dependent)
    run = make_run(session, workspace, dependent, "p1")
    if tree_exists:
        first = session.exec(select(AgentRun).where(AgentRun.run_code == "r1")).one()
        run.worktree_id = first.worktree_id
        session.add(run)
        session.commit()
    exc = TargetBranchError("'integration/lg-ms' has no base to take")

    def raising(session, ticket, workspace, *, repo_root, **extra):
        raise exc

    with patch("loregarden.services.parallel_stage.resolve_target_branch", raising):
        message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert message == f"Failed to checkout branch: {exc}", message
    session.refresh(dependent)
    assert dependent.workflow_stage_status == StageStatus.BLOCKED
    assert str(exc) in (dependent.blocking_issues or ""), dependent.blocking_issues


def test_the_git_processes_one_parallel_stage_starts_are_measured(
    session, workspace, dependent, repo, dispatch_git, record_property
):
    """AC6. The saving is measured on the path AC1 counts, not estimated.

    `dispatch_git` counts every `git` argv started under `subprocess.Popen`,
    which is where the count has to be taken: the path crosses
    `ticket_worktree`, `target_branch`, `git_branch` and `git_merge_noco`, and
    each of them binds `run_git` by name. `reconcile_timer._sweep` is held off
    for the same reason it is there — a sweep firing inside the window would
    count its own `for-each-ref` against the stage.

    The figure is recorded rather than pinned to a constant. A second
    resolution is the two processes `resolve_target_branch` repeats
    (`for-each-ref` for the integration ref, `merge-base --is-ancestor` for its
    base), so this test asserts the *structural* fact that makes the figure
    move — the ticket's integration ref is looked up once per stage — and
    leaves the absolute totals to the stage report. A delta of 0 would be a
    reportable measurement, not a failure here.
    """
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    assert target != "main", "fixture: a ticket with no integration branch resolves no refs"
    run = make_run(session, workspace, dependent, "p1")
    dispatch_git.clear()  # the fixture's own resolution must not be counted

    message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert message == "", message
    record_property("parallel_stage_git_processes", len(dispatch_git))
    record_property("parallel_stage_git_argvs", [" ".join(argv) for argv in dispatch_git])
    assert dispatch_git, "the measurement is vacuous if the stage started no git at all"
    assert len(_dispatch_asking(dispatch_git, "for-each-ref")) == 1, (
        f"the ticket's integration ref is looked up once per stage, not "
        f"{len(_dispatch_asking(dispatch_git, 'for-each-ref'))} times, in "
        f"{[' '.join(argv) for argv in dispatch_git]}"
    )


# ---------------------------------------------------------------------------
# lg-milestone-that-1200, test-break: four mutants the designed suite lets
# through, and the two contracts no path-level count can see.
#
# (1) **The one answer is the wrong answer.** Every test above counts
#     resolutions and asserts the tree they were taken in; none of them asks
#     what the shared answer *was*. So `start_point=ticket.branch` — a plausible
#     slip while rewiring that very argument, "the tree is already on the ticket
#     branch" — resolves exactly once, in the shared checkout, returns `""`, and
#     looks up the integration ref once. It passes AC1, AC2(a), AC3 and AC6. On
#     the already-cut path `test_the_parallel_path_refreshes_an_existing_
#     worktree_onto_its_target` kills it, because the landed sibling never
#     arrives. On the *cut* path — which is the path AC1 and AC6 both count —
#     nothing above looks at where the tree was cut from, so a memo answering
#     `main` cuts the stage's tree off the base and loses every sibling's landed
#     work (lg-milestone-that-769) while reporting a count of 1.
#
# (2) **A memo that outlives its stage.** AC1 counts one stage. A memo cached on
#     the module, or keyed by ticket id, counts 1 there and passes — and the
#     defect is not the cached *name*, which does not change, but the skipped
#     *write*: `_refresh_from_tips` is what carries the base branch onto the
#     integration branch, so a second stage that reuses the first stage's answer
#     never sees what landed on `main` between them. This is mutant (1) of
#     lg-milestone-that-918's own test-break block, in the module 1200 copies
#     the memo into.
#
# (3) **A hoist killed by one fixture.** AC2(b) rests entirely on the
#     dirty-primary-checkout park, which needs `_worktrees_off` *and* a dirty
#     checkout. A second, independent zero-resolution path — a recorded worktree
#     whose directory is gone, falling back onto a dirty checkout
#     (`workspace_paths.resolve_run_root` → `_require_clean_fallback`) — keeps
#     the kill when someone changes how worktrees are switched off. plan-risk
#     proposed this one and plan-synthesis recorded it as the alternative.
#
# (4) **An evaluation above the guards.** `prepare_tree_for_parallel_stage`
#     returns `""` three times before it would resolve anything: no workspace,
#     no members, and a workspace root that is not a directory. A factory built
#     or evaluated above those returns writes shared refs for a stage with no
#     members at all.
#
# And two properties of the memo itself that no path through this function can
# observe, because no path calls `once()` twice: that building it resolves
# nothing, and that a failure is not cached. They are asserted directly.
# ---------------------------------------------------------------------------


def test_a_parallel_stage_cuts_its_tree_from_the_target_and_not_the_base(
    session, workspace, dependent, repo
):
    """The one answer must be the ticket's target branch.

    This is the cut path's half of lg-milestone-that-769: a sibling's landed
    work lives on the integration branch and never on `main` until the tree
    completes, so a stage whose tree is cut from the base starts from a tree
    that cannot see it. Counting resolutions cannot tell the two apart — both
    are one call in the shared checkout — so the assertion is on the tree git
    actually produced.

    Passes before lg-milestone-that-1200 and after: handing the answer over
    must not change *which* answer it is. It fails for any memo that returns
    the base branch, another ticket's branch, or `ticket.branch`.
    """
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    landed = commit_on(Path(repo), target, "sibling.txt", "a sibling landed before the stage\n")
    assert not _ancestor(Path(repo), landed, "main"), (
        "fixture: the work must be on the integration branch and *only* there, "
        "or a tree cut from the base would see it anyway and the test proves nothing"
    )
    run = make_run(session, workspace, dependent, "p1")
    assert not run.worktree_id, "fixture: the cut must still be ahead of us"

    assert prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run]) == ""

    session.refresh(run)
    worktree = WorktreeService(session, repo_path=str(repo)).active_worktree_for_ticket(
        dependent.id
    )
    assert worktree is not None, "the stage must have cut a tree of its own"
    root = Path(worktree.worktree_path)
    assert _ancestor(root, landed, "HEAD"), (
        f"the stage's tree was cut from the base, not from {target}: the sibling "
        "work every member needs is not in it"
    )


def test_a_second_parallel_stage_resolves_its_own_target(session, workspace, dependent, repo):
    """One resolution *per stage*, not one per ticket or per process.

    The memo has to be built inside `prepare_tree_for_parallel_stage`, because
    the thing being saved is a write and not a lookup: resolving is what carries
    the base branch onto the integration branch, so a memo that outlives its
    stage makes everything that landed on `main` since the first stage invisible
    to the second. A count of 1 on a single stage cannot see that.

    Two stages, so this reports **4** against the code before
    lg-milestone-that-1200 and 2 after — and the refresh assertion is red for
    any memo cached beyond the stage, whatever it counts.
    """
    first = make_run(session, workspace, dependent, "p1")
    assert (
        prepare_tree_for_parallel_stage(session, dependent, "work", [first], stamped=[first]) == ""
    )
    session.refresh(first)
    assert first.worktree_id, "fixture: the first stage cut the ticket's tree"
    tree = WorktreeService(session, repo_path=str(repo)).active_worktree_for_ticket(dependent.id)
    assert tree is not None
    root = Path(tree.worktree_path)
    landed = commit_on(Path(repo), "main", "between-stages.txt", "landed between the stages\n")
    assert not _ancestor(root, landed, "HEAD"), "fixture: the first stage cannot have seen it"
    second = make_run(session, workspace, dependent, "p2")
    calls, (parallel_spy, worktree_spy) = _parallel_resolution_spies()

    with parallel_spy, worktree_spy:
        message = prepare_tree_for_parallel_stage(
            session, dependent, "review", [second], stamped=[second]
        )

    assert message == "", message
    assert len(calls) == 1, (
        f"the second parallel stage resolved its target {len(calls)} times, in "
        f"{calls}: each stage resolves once, and resolves for itself"
    )
    session.refresh(second)
    assert second.worktree_id == first.worktree_id, (
        "fixture: the second stage must reuse the ticket's tree, or it would see "
        "the new work by being re-cut rather than by being refreshed"
    )
    assert _ancestor(root, landed, "HEAD"), (
        "the second stage reused the first stage's answer: what landed on the "
        "base branch between them never reached the integration branch, and the "
        "members work in a tree that cannot see it"
    )


def test_a_vanished_worktree_over_a_dirty_checkout_parks_without_resolving(
    session, workspace, dependent, repo
):
    """The second zero-resolution path, so the hoist mutant's kill does not rest
    on one fixture.

    A recorded worktree whose directory is gone falls back to the shared
    checkout (`workspace_paths.resolve_run_root`), and the fallback is refused
    when that checkout holds work which is not this ticket's — before anything
    is resolved. Unlike AC2(b)'s park this needs no `git_automation_json`, so a
    change to how worktrees are switched off cannot take the kill with it.

    Zero resolutions today and zero after. A memo evaluated above the
    `resolve_execution_root` call reports 1 here and refreshes shared refs for a
    stage whose members never start.
    """
    import shutil

    root = _existing_worktree(session, workspace, dependent)
    worktree = WorktreeService(session, repo_path=str(repo)).active_worktree_for_ticket(
        dependent.id
    )
    assert worktree is not None
    shutil.rmtree(root)
    assert not root.is_dir(), "fixture: the recorded tree is gone from disk"
    (Path(repo) / "someone-elses.txt").write_text("an operator was mid-edit here\n")
    at_terminal_stage(session, dependent)
    run = make_run(session, workspace, dependent, "p1")
    run.worktree_id = worktree.id
    session.add(run)
    session.commit()
    calls, (parallel_spy, worktree_spy) = _parallel_resolution_spies()

    with parallel_spy, worktree_spy:
        message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert calls == [], f"a stage that never starts resolved {len(calls)} target branches: {calls}"
    assert message.startswith("Failed to checkout branch: "), message
    assert "someone-elses.txt" in message, message
    session.refresh(dependent)
    assert dependent.workflow_stage_status == StageStatus.BLOCKED
    [error] = _error_artifacts(session, dependent)
    assert error.title == "Shared checkout refused — work"


def test_a_parallel_stage_with_nothing_to_prepare_resolves_nothing(
    session, workspace, dependent, repo
):
    """The three guards above the resolution, each of which returns `""`.

    Resolving writes shared refs and can block on a merge turn, so a stage with
    no members, or a workspace whose checkout is not on this disk, must not
    reach it. A factory built or evaluated above these returns does.
    """
    run = make_run(session, workspace, dependent, "p1")
    calls, (parallel_spy, worktree_spy) = _parallel_resolution_spies()

    with parallel_spy, worktree_spy:
        assert prepare_tree_for_parallel_stage(session, dependent, "work", [], stamped=[]) == ""
        assert calls == [], f"a stage with no members resolved a target branch: {calls}"

        workspace.repo_path = str(Path(repo) / "not-checked-out-here")
        session.add(workspace)
        session.commit()
        assert (
            prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run]) == ""
        )

    assert calls == [], (
        f"a stage whose workspace checkout is not on this disk resolved {calls} — "
        "the guard is above the resolution for a reason"
    )
    session.refresh(dependent)
    assert dependent.workflow_stage_status != StageStatus.BLOCKED, (
        "and neither guard is a failure: nothing was prepared and nothing blocked"
    )


def test_the_target_branch_memo_is_lazy_and_does_not_cache_a_failure(
    session, workspace, dependent, repo
):
    """The two properties of the memo that no path through the function shows.

    No path calls `once()` twice — `resolve_execution_root` takes it or skips
    it, and `start_point` evaluates it once — so neither "building it resolves
    nothing" nor "a failure is retried" is visible from a count taken around
    the stage. Both are asserted here directly, because the first is what makes
    the zero-resolution paths zero and the second is what stops a transient
    merge-turn failure from being remembered as this ticket's answer for the
    rest of the stage.
    """
    from unittest.mock import patch

    from loregarden.services.parallel_stage import _target_branch_once
    from loregarden.services.target_branch import TargetBranchError

    answers: list[str] = []

    def answering(session, ticket, workspace, *, repo_root, **extra):
        answers.append(f"integration/call-{len(answers) + 1}")
        return answers[-1]

    with patch("loregarden.services.parallel_stage.resolve_target_branch", answering):
        once = _target_branch_once(session, dependent, workspace, workspace_root=Path(repo))
        assert answers == [], "building the memo must resolve nothing — that is what makes it lazy"
        assert once() == "integration/call-1"
        assert once() == "integration/call-1", "the second reading is the first answer"
        assert len(answers) == 1, f"the memo resolved {len(answers)} times, not once"

    attempts: list[int] = []

    def failing(session, ticket, workspace, *, repo_root, **extra):
        attempts.append(1)
        raise TargetBranchError("the merge turn was not granted")

    with patch("loregarden.services.parallel_stage.resolve_target_branch", failing):
        failing_once = _target_branch_once(session, dependent, workspace, workspace_root=Path(repo))
        for _ in range(2):
            with pytest.raises(TargetBranchError):
                failing_once()

    assert len(attempts) == 2, (
        "a failure must not be cached: the memo holds answers, and a raise is not one"
    )


def test_an_integration_branch_that_cannot_take_its_base_blocks_the_real_cut(
    session, workspace, dependent, repo
):
    """AC4(a)'s cut surface, without the mock that stands in for it.

    The parametrized test above patches `parallel_stage.resolve_target_branch`
    to raise `TargetBranchError` directly, so it would pass even if the error
    the real resolver raises never reached that handler —
    `IntegrationConflictError` is a subclass today, and nothing above says so.
    This drives the real conflict: the integration branch and `main` both touch
    one file, so `_refresh_from_tips` files the inbox card and raises on its
    own, from inside `resolve_execution_root`'s call to the memo.

    The stage blocks with git's own words, and no tree is cut for a stage whose
    members never start.
    """
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    commit_on(Path(repo), target, "clash.txt", "integration side\n")
    commit_on(Path(repo), "main", "clash.txt", "main side\n")
    at_terminal_stage(session, dependent)
    run = make_run(session, workspace, dependent, "p1")

    message = prepare_tree_for_parallel_stage(session, dependent, "work", [run], stamped=[run])

    assert message.startswith("Failed to checkout branch: "), message
    assert "clash.txt" in message, message
    session.refresh(dependent)
    assert dependent.workflow_stage_status == StageStatus.BLOCKED
    assert "clash.txt" in (dependent.blocking_issues or ""), dependent.blocking_issues
    assert (
        WorktreeService(session, repo_path=str(repo)).active_worktree_for_ticket(dependent.id)
        is None
    ), "a stage that cannot resolve its target must not leave a tree behind"


# ---------------------------------------------------------------------------
# lg-milestone-that-974: one target-branch resolution per *handoff*, carried
# outward as one noun.
#
# `render_stage_prompt` holds the same pair 918 removed from the dispatch path
# and 1200 from the parallel path: it calls `resolve_execution_root` with no
# `resolve_target=` (so that call resolves the target to cut the tree from it)
# and then resolves the same target again for `start_point`. For a ticket with
# no worktree yet, one handoff therefore resolves TWICE before either boundary
# read makes it three — an undercount in the ticket's own title.
#
# The existing `test_the_handoff_resolves_its_target_in_the_shared_checkout`
# above cannot see that: it pre-creates a worktree, so `resolve_execution_root`
# takes its early return and resolves nothing. It stays (it pins *which tree*
# the resolution happens in, which is 779 S3's question) and it is not evidence
# for the count.
#
# And the one resolution has to stay LAZY. `render_stage_prompt` has a path
# that resolves zero times today — worktrees off over a checkout holding
# someone else's work, where `_require_clean_fallback` raises before anything
# is resolved — and resolving is a *write*: `ensure_integration_branch`,
# `_refresh_from_tips`, and behind them a blocking merge turn that files an
# inbox card. A memo evaluated above the `try` would write shared refs for a
# handoff that never happens.
# ---------------------------------------------------------------------------


def _ensure_branch_spy(start_points: list[str]):
    """A call-through spy on `ensure_ticket_branch` recording each `start_point`.

    Call-through, so the tree really is refreshed and `repo_root` really is the
    tree the handoff hands over — the point of AC2 is that the branch reported
    and the branch merged in are one answer, which a stub cannot show.
    """
    from loregarden.services.git_branch import ensure_ticket_branch as real

    def spy(repo_root, ticket, *, session, start_point="", ignoring=(), seat=None):
        start_points.append(start_point)
        return real(
            repo_root,
            ticket,
            session=session,
            start_point=start_point,
            ignoring=ignoring,
            seat=seat,
        )

    return spy


def test_the_handoff_hands_back_the_branch_it_refreshed_the_tree_onto(
    session, workspace, dependent, repo, agents
):
    """AC2. One answer, not two readings.

    The returned `PreparedTree.target_branch` has to be the very string passed
    as `ensure_ticket_branch(start_point=...)` on that call. Counting
    resolutions cannot tell a right answer from a wrong one — `target_branch`
    set to `ticket.branch`, or to the base branch, resolves exactly once and
    reports a count of 1 while telling the operator their tree carries a branch
    it does not. So this asserts on the value, against the argument the refresh
    actually received.
    """
    from unittest.mock import patch

    root = _existing_worktree(session, workspace, dependent)
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    assert target != "main", (
        "fixture: the target must differ from the base branch, or `main` and the "
        "right answer are indistinguishable"
    )
    assert target != dependent.branch
    start_points: list[str] = []

    with patch(
        "loregarden.agents.executors.cli.ensure_ticket_branch", _ensure_branch_spy(start_points)
    ):
        _prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert start_points == [target], (
        f"the refresh was brought up to {start_points}, and the handoff reports "
        f"{tree.target_branch!r}"
    )
    assert tree.target_branch == start_points[0], (
        "the branch handed to the boundary is not the branch the tree was "
        "refreshed onto: two readings of one question"
    )
    assert tree.repo_root == root, "and the tree reported is the one that call prepared"


def test_one_handoff_resolves_its_target_branch_once(session, workspace, dependent, repo, agents):
    """AC3. Counted across BOTH import bindings, for a ticket with no worktree.

    Non-vacuous: against the code as it stands *before* this change the same
    test reports **2** — `resolve_execution_root` resolves through
    `ticket_worktree`'s binding to cut the tree from the target, and
    `render_stage_prompt` resolves again through `cli`'s to build `start_point`.
    A spy on `services.target_branch.resolve_target_branch` instead of these two
    reports **0** and passes against anything, because every consumer binds the
    name at import.

    No worktree is created up front, deliberately: that is the fixture shape
    the existing shared-checkout test lacks, and it is the only one in which
    the second resolution is observable at all.
    """
    target = resolve_target_branch(session, dependent, workspace, repo_root=Path(repo))
    assert target != "main", (
        "fixture: the target must differ from the base branch, or a resolution "
        "issues no git at all and costs nothing to repeat"
    )
    assert (
        WorktreeService(session, repo_path=str(repo)).active_worktree_for_ticket(dependent.id)
        is None
    ), "fixture: the cut must still be ahead of us, or `resolve_execution_root` resolves nothing"
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        _prompt, tree = _handoff(session, workspace, dependent, "h1")

    assert len(calls) == 1, (
        f"one handoff resolved its target branch {len(calls)} times, in {calls}: the "
        "worktree cut and the branch refresh must share the one answer"
    )
    assert calls == [Path(repo)], (
        f"and it must be resolved in the shared checkout, not in {calls} — resolving "
        "writes refs, and refs are shared by every worktree of a repository"
    )
    assert tree.target_branch == target
    assert tree.repo_root != Path(repo), "the handoff still cut the ticket its own tree"


def test_a_handoff_that_parks_on_a_dirty_checkout_resolves_nothing(
    session, workspace, dependent, repo, agents
):
    """AC4. The resolution stays lazy.

    Worktrees off and the shared checkout holding work that is not this
    ticket's: `_require_clean_fallback` raises `DirtyPrimaryCheckoutError`
    before anything is resolved, and the handoff never happens. Nothing may be
    written to shared refs on its behalf — zero resolutions today, zero after.

    This is the mutant kill for the whole change. A `PreparedTree` built above
    the `try`, or a memo evaluated when it is created rather than when it is
    read, reports 1 here: it would refresh an integration branch and file an
    inbox card for a handoff that is refused before it starts.
    """
    _worktrees_off(session, dependent)
    (Path(repo) / "someone-elses.txt").write_text("an operator was mid-edit here\n")
    assert git(repo, "status", "--porcelain").stdout.strip(), "fixture: the checkout is dirty"
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        with pytest.raises(ValueError) as excinfo:
            _handoff(session, workspace, dependent, "h1")

    assert "someone-elses.txt" in str(excinfo.value), str(excinfo.value)
    assert calls == [], (
        f"a handoff that never happens resolved {len(calls)} target branches: {calls}"
    )
    integration = [
        line
        for line in git(repo, "for-each-ref", "--format=%(refname)").stdout.splitlines()
        if "integration" in line
    ]
    assert integration == [], (
        f"a refused handoff created {integration}: resolving is a write, and the park "
        "path must write nothing"
    )


# --- lg-milestone-that-974, test-break: two mutants the designed suite lets live


def test_a_second_handoff_through_one_executor_resolves_its_own_target(
    session, workspace, dependent, repo, agents
):
    """The memo's lifetime is one handoff, not one executor.

    `test_one_handoff_resolves_its_target_branch_once` counts a single handoff,
    so an implementation that hangs the memo on `self` instead of building it
    per call reports **1** there and passes. It is wrong in the direction that
    does not fail: the second handoff reports a count of 0, the integration
    branch is never brought up to whatever landed on the base in between, and
    the operator is handed a tree built on a base that is one handoff out of
    date — with nothing logged and nothing raised.

    `test_a_second_dispatch_through_one_executor_resolves_its_own_target` makes
    the same point for `_prepare_tree`; this is the handoff edge of it, which
    is the one lg-milestone-that-974 moves.
    """
    root = _existing_worktree(session, workspace, dependent)
    executor = CliAgentExecutor(session)

    _prompt, first_tree = executor.render_stage_prompt(
        make_run(session, workspace, dependent, "h1"), dependent
    )
    assert first_tree.repo_root == root

    landed = commit_on(Path(repo), "main", "from-main.txt", "landed on the base branch\n")
    assert not _ancestor(root, landed, "HEAD"), (
        "fixture: the base commit must not already be in the tree, or this proves nothing"
    )
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy:
        _prompt, second_tree = executor.render_stage_prompt(
            make_run(session, workspace, dependent, "h2"), dependent
        )

    assert len(calls) == 1, (
        f"the second handoff through the same executor resolved {len(calls)} times: a "
        "memo cached on `self` answers from the first handoff and never carries the "
        "base branch onto the target again"
    )
    assert second_tree.target_branch == first_tree.target_branch
    assert _ancestor(root, landed, "HEAD"), (
        "the base commit that landed between the two handoffs never reached the "
        "handed-over tree: the second handoff reused the first's answer"
    )


def test_a_handoff_refused_before_it_starts_resolves_nothing(
    session, workspace, dependent, repo, agents
):
    """AC4's other zero-resolution path, and the one a hoist survives.

    `test_a_handoff_that_parks_on_a_dirty_checkout_resolves_nothing` fails a
    handoff from *inside* the `try`, so a `PreparedTree` or a memo evaluated at
    the top of the method — but still below the agent and workspace guards —
    passes it. `render_stage_prompt` refuses three things before that: an
    unknown agent, an unknown workspace, and a workspace root that is not a
    directory. None of them may write a shared ref on the way out.

    An unknown agent is the live one: `_begin_external_stage` renders whatever
    `start_stage_execution` opened, and a stage naming an agent the registry
    lost reaches here. Resolving for it would create the integration branch,
    merge the base into it, and be able to spend a merge turn — for a handoff
    that returns a 400 either way.
    """
    assert (
        WorktreeService(session, repo_path=str(repo)).active_worktree_for_ticket(dependent.id)
        is None
    ), "fixture: no tree yet, or a resolution on this path would be unobservable"
    run = make_run(session, workspace, dependent, "h1")
    run.agent_id = "an_agent_the_registry_no_longer_has"
    session.add(run)
    session.commit()
    calls, (cli_spy, worktree_spy) = _resolution_spies()

    with cli_spy, worktree_spy, pytest.raises(ValueError) as excinfo:
        CliAgentExecutor(session).render_stage_prompt(run, dependent)

    # lg-milestone-that-975 re-points this from `Unknown agent`, which was the
    # whole of what the operator got: no ticket, no stage, no next action, and
    # an empty name when `run.agent_id` was blank. The refusal still has to be
    # about the agent — the assertion below is what this test is for, and it is
    # only meaningful if the handoff was refused for the reason it was set up to
    # be refused for.
    message = str(excinfo.value)
    assert "an_agent_the_registry_no_longer_has" in message, message
    assert "agent registry" in message, message
    assert calls == [], (
        f"a handoff refused before it started resolved {len(calls)} target branches: "
        f"{calls} — the memo must not be read until a consumer asks"
    )
    integration = [
        line
        for line in git(repo, "for-each-ref", "--format=%(refname)").stdout.splitlines()
        if "integration" in line
    ]
    assert integration == [], f"a refused handoff created {integration}"
