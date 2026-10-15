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

    assert prepare_tree_for_parallel_stage(session, dependent, "work", runs) == ""

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
        session, dependent, "work", [make_run(session, workspace, dependent, "p1")]
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


def _handoff(session, workspace, ticket, code: str) -> tuple[str, Path]:
    """One terminal/harness handoff's tree preparation and prompt render.

    The single call both handoff edges make; everything they add afterwards
    (a prompt file, a run view) is downstream of the tree it returns.
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

    _prompt, repo_root = _handoff(session, workspace, dependent, "h1")

    assert repo_root == root, "the handed-off tree is the ticket's worktree, not a fresh one"
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
        prompt, repo_root = _handoff(session, workspace, dependent, "h1")

    assert prompt, "the handoff is not blocked by a tree that could not be refreshed"
    assert repo_root == root
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

    _prompt, repo_root = _handoff(session, workspace, dependent, "h1")

    assert repo_root == root
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

    _prompt, repo_root = _handoff(session, workspace, dependent, "h1")

    assert repo_root == root
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
            session, dependent, "work", [make_run(session, workspace, dependent, "p1")]
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

    _prompt, repo_root = _handoff(session, workspace, dependent, "h1")

    assert repo_root == root
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
