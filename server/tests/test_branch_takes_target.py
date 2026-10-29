"""A ticket's branch that conflicts with its target is resolved before a person is asked.

init-pre-tinkercg-3, 2026-10-14: 918 and 920 held only 801's commits, already
on the target by squash, and 954 held seven commits of its own against a file
the target had rewritten. All three blocked at dispatch on "could not take",
and the autopilot paused. Both dispatch paths are exercised: the shared
checkout or an existing tree (`git_branch.ensure_ticket_branch`), and a tree
cut for the ticket (`WorktreeService.get_or_create_for_ticket`).
"""

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Workspace
from loregarden.services import integration_merge_agent
from loregarden.services.branch_takes_target import MergeTurnSeat, take_target
from loregarden.services.git_branch import ensure_ticket_branch, resolve_ticket_branch
from loregarden.services.git_merge_noco import squash_twin
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
    """Its first commit is a snapshot `main` also holds, so it takes `main`
    through that twin; it is never moved onto `main`, which would drop the work."""
    tip = commit_on(repo, superseded, "extra.txt", "only on the branch\n")

    ensure_ticket_branch(repo, ticket, start_point="main")

    git(repo, "merge-base", "--is-ancestor", tip, superseded)
    assert _sha(repo, superseded) != _sha(repo, "main"), "a branch with unique work is never moved"
    assert (repo / "extra.txt").read_text() == "only on the branch\n"
    assert (repo / "feature.txt").read_text() == "first\nthen main\n"
    assert _clean(repo)
    assert _merge_runs(session, ticket) == []


# --- the branch holds a squash of its target: history recorded, no agent ---------------

INTEGRATION = "integration/lg-root"


@pytest.fixture(name="twinned")
def twinned_fixture(repo, ticket):
    """1026's shape, 2026-10-20: the branch merged a squash of its integration
    branch (what `publish_tree` pushed under the branch's name) and then edited
    a file the tree added. Every merge of the real branch is add/add in it."""
    branch = resolve_ticket_branch(ticket)
    git(repo, "branch", INTEGRATION, "main")
    landed = commit_on(repo, INTEGRATION, "shared.txt", "the tree's version\n")
    tree = _sha(repo, f"{landed}^{{tree}}")
    squash = git(repo, "commit-tree", tree, "-p", "main", "-m", "publish").stdout.strip()
    git(repo, "branch", branch, squash)
    tip = commit_on(repo, branch, "shared.txt", "the tree's version\nthe ticket's line\n")
    commit_on(repo, INTEGRATION, "later.txt", "landed after the squash\n")
    probe = _merge_tree_names(repo, branch, INTEGRATION)
    assert probe.returncode == 1 and "shared.txt" in probe.stdout, "fixture: must conflict"
    return branch, tip, landed


def _merge_tree_names(repo: Path, ours: str, theirs: str) -> subprocess.CompletedProcess[str]:
    """`merge-tree`, which exits 1 on the conflict these tests look for."""
    return subprocess.run(
        ["git", "merge-tree", "--write-tree", "--name-only", ours, theirs],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_took_the_tree(path: Path, tip: str, landed: str) -> None:
    git(path, "merge-base", "--is-ancestor", tip, "HEAD")
    git(path, "merge-base", "--is-ancestor", landed, "HEAD")
    git(path, "merge-base", "--is-ancestor", INTEGRATION, "HEAD")
    assert (path / "shared.txt").read_text() == "the tree's version\nthe ticket's line\n"
    assert (path / "later.txt").read_text() == "landed after the squash\n"
    assert _clean(path), git(path, "status", "--porcelain").stdout


def test_a_branch_holding_a_squash_of_its_target_takes_it_in_the_checkout(
    session, workspace, ticket, repo, twinned
):
    branch, tip, landed = twinned
    git(repo, "checkout", "-q", branch)

    ensure_ticket_branch(repo, ticket, start_point=INTEGRATION)

    _assert_took_the_tree(repo, tip, landed)
    assert _merge_runs(session, ticket) == [], "no agent turn for history alone"


def test_a_branch_holding_a_squash_of_its_target_takes_it_before_its_worktree_is_cut(
    session, workspace, ticket, repo, twinned
):
    branch, tip, landed = twinned
    run = make_run(session, workspace, ticket, "r1")

    tree = WorktreeService(session, repo_path=str(repo)).get_or_create_for_ticket(
        ticket, run.id, parent_branch=INTEGRATION
    )

    assert tree is not None
    _assert_took_the_tree(Path(tree.worktree_path), tip, landed)
    assert _merge_runs(session, ticket) == []


def test_recording_the_twin_changes_no_file(repo, twinned):
    branch, tip, landed = twinned

    taken = take_target(repo, branch, INTEGRATION)

    assert taken.ok and taken.moved_from == tip and taken.moved_to == _sha(repo, branch)
    assert _sha(repo, f"{branch}^{{tree}}") == _sha(repo, f"{tip}^{{tree}}")
    parents = git(repo, "rev-list", "--parents", "-n1", branch).stdout.split()[1:]
    assert parents == [tip, landed]


def test_a_twin_with_a_real_conflict_behind_it_still_goes_to_the_turn(
    session, workspace, ticket, repo, twinned
):
    """The history is recorded either way; a conflict it does not explain is
    the agent's, and without a seat it blocks naming the file."""
    branch, tip, landed = twinned
    commit_on(repo, branch, "later.txt", "the ticket's own later.txt\n")
    run = make_run(session, workspace, ticket, "r1")

    with pytest.raises(WorktreeRefreshError, match="later.txt"):
        WorktreeService(session, repo_path=str(repo)).get_or_create_for_ticket(
            ticket, run.id, parent_branch=INTEGRATION
        )

    git(repo, "merge-base", "--is-ancestor", landed, branch)
    assert "shared.txt" not in _merge_tree_names(repo, branch, INTEGRATION).stdout


def test_no_twin_on_an_ordinary_conflict(repo, diverged):
    branch, _tip = diverged

    assert squash_twin(repo, _sha(repo, branch), _sha(repo, "main")) is None


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


def test_a_target_that_does_not_exist_is_left_to_the_caller(session, workspace, ticket, repo):
    """A repository whose base is not `main` (CI's `git init` gives `master`):
    no conflict, so nothing to move or merge, and no refusal of its own."""
    branch = resolve_ticket_branch(ticket)
    _cut(repo, branch)
    tip = commit_on(repo, branch, "own.txt", "the ticket's work\n")

    taken = take_target(repo, branch, "no-such-base")

    assert taken.ok and not taken.moved_from
    assert _sha(repo, branch) == tip
    assert _merge_runs(session, ticket) == []


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
