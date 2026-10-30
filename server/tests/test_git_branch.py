"""Git branch helpers."""

import contextlib
import logging
import subprocess
from pathlib import Path
from typing import NamedTuple
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    PrimaryCheckoutUse,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError
from sqlmodel import Session, select
from tests.worktree_helpers import grant_receipt


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    """A live Session, because `ensure_ticket_branch` now requires one.

    lg-external-harness-1452: the checkout self-repair asks the receipt gate
    instead of git, and the gate reads the `worktrees` table. `session` is a
    required keyword-only parameter with no default — `object_session(ticket)`
    was measured dead here, because these tests build bare detached `Ticket`
    objects and it would return `None` at every call site, silently skipping
    the gate.
    """
    with Session(isolated_db) as session:
        yield session


def _grant_the_gate_its_premises(
    session,
    *,
    repo_root: Path,
    tree: Path,
    branch: str,
    external_id: str,
    outside_the_base: bool = False,
) -> Worktree:
    """Give the checkout at `tree` a `worktrees` row and a landing receipt.

    lg-external-harness-1452. The self-repair no longer removes anything
    itself: it asks `checkout_release.release_checkout`, which is the same
    receipt gate every other removal site goes through. So a test whose
    subject is "the stale tree still goes" has to make the tree *releasable* —
    a row the gate can resolve an owner from, and a landing the gate can
    re-verify in git — rather than relying on git's willingness to be told
    `--force`.

    `tree` must live under ``repo_root.parent / ".worktrees"``: that is
    `WorktreeService.worktree_base`, and a path outside it is refused
    OUTSIDE_BASE before any premise is read.
    """
    inside = tree.is_relative_to(repo_root.parent / ".worktrees")
    assert inside is not outside_the_base, (
        f"fixture: {tree} is {'inside' if inside else 'outside'} the worktree base, "
        f"and the caller asked for the other one"
    )
    workspace = Workspace(slug=external_id, name=external_id, repo_path=str(repo_root))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)

    owner = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Owner of {branch}",
        branch=branch,
        state=TicketState.IN_PROGRESS,
    )
    session.add(owner)
    session.commit()
    session.refresh(owner)

    run = AgentRun(
        run_code=f"run-{external_id}",
        ticket_id=owner.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=owner.id,
        parent_branch="main",
        worktree_path=str(tree),
        branch=branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    # A commit of its own, then a real landing of it: `_has_preserved_commits`
    # wants the branch to name something other than its base, and the receipt
    # is re-verified in git rather than read off the column.
    (tree / "landed.txt").write_text("the work this checkout held\n", encoding="utf-8")
    _git(["add", "-A"], cwd=tree)
    _git(["commit", "-m", "work"], cwd=tree)
    grant_receipt(session, owner, repo_root, branch=branch)
    return row


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )


def _init_repo(path: Path) -> None:
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)


def test_ensure_ticket_branch_creates_branch(tmp_path, monkeypatch, session):
    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="99-test-branch-checkout",
        branch="loregarden/99-test-branch-checkout",
        work_item_type=WorkItemType.TASK,
        title="Branch test",
        workspace_id="w",
    )
    branch = ensure_ticket_branch(repo_root, ticket, session=session)
    assert branch == "loregarden/99-test-branch-checkout"
    current = _git(["branch", "--show-current"], cwd=repo_root)
    assert current.stdout.strip() == "loregarden/99-test-branch-checkout"
    _git(["checkout", "-"], cwd=repo_root)


def test_ensure_ticket_branch_self_repairs_worktree_lock(tmp_path, monkeypatch, session):
    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="88-worktree-lock",
        branch="loregarden/88-worktree-lock",
        work_item_type=WorkItemType.TASK,
        title="Worktree lock",
        workspace_id="w",
    )
    ensure_ticket_branch(repo_root, ticket, session=session)
    _git(["checkout", "main"], cwd=repo_root)
    other = tmp_path / ".worktrees" / "other-worktree"
    other.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", str(other), "loregarden/88-worktree-lock"], cwd=repo_root)
    row = _grant_the_gate_its_premises(
        session,
        repo_root=repo_root,
        tree=other,
        branch="loregarden/88-worktree-lock",
        external_id="lg-88-worktree-lock",
    )

    branch = ensure_ticket_branch(repo_root, ticket, session=session)
    assert branch == "loregarden/88-worktree-lock"
    current = _git(["branch", "--show-current"], cwd=repo_root)
    assert current.stdout.strip() == "loregarden/88-worktree-lock"
    assert not other.exists()
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value, (
        "the gate settles the row it released; an ACTIVE row with no checkout "
        "is the stale state AC2's invariant exists to catch"
    )


def test_ensure_ticket_branch_refuses_to_remove_primary_lock(tmp_path, monkeypatch, session):
    from loregarden.services import git_branch as git_branch_mod

    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="primary-lock",
        branch="loregarden/primary-lock",
        work_item_type=WorkItemType.TASK,
        title="Primary",
        workspace_id="w",
    )

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            128,
            ["git", "checkout", "-B", "loregarden/primary-lock"],
            stderr=f"fatal: 'loregarden/primary-lock' is already used by worktree at '{repo_root.resolve()}'",
        )

    monkeypatch.setattr(git_branch_mod, "_checkout_branch", boom)
    # The refusal moved with 916: the holder here IS the main working tree, and
    # "another worktree" (plus the `worktree remove --force` remedy under it)
    # was the wording that sent the operator a command git exits 128 on.
    with pytest.raises(ValueError, match="this repository's main working tree"):
        ensure_ticket_branch(repo_root, ticket, session=session)


def test_ensure_ticket_branch_never_resets_an_existing_branch(tmp_path, monkeypatch, session):
    """A retried ticket keeps its commits (lg-milestone-that-772).

    `checkout -B` resets an existing branch to HEAD. Re-dispatching a ticket
    after HEAD had moved to another branch left every earlier commit on the
    ticket branch unreachable, and the tree looked like a first attempt.
    """
    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())

    ticket = Ticket(
        external_id="772-keeps-its-commits",
        branch="loregarden/772-keeps-its-commits",
        work_item_type=WorkItemType.TASK,
        title="Retry",
        workspace_id="w",
    )
    ensure_ticket_branch(repo_root, ticket, session=session)
    (repo_root / "work.txt").write_text("first round\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_root)
    _git(["commit", "-m", "first round"], cwd=repo_root)
    first_round = _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()

    # Something else moves HEAD — another ticket, a chat turn, a person.
    _git(["checkout", "-b", "elsewhere", "main"], cwd=repo_root)

    ensure_ticket_branch(repo_root, ticket, session=session)

    assert _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip() == first_round
    assert (repo_root / "work.txt").exists()


def test_ensure_ticket_branch_cuts_a_missing_branch_from_the_start_point(
    tmp_path, monkeypatch, session
):
    repo_root = tmp_path / "loregarden"
    repo_root.mkdir()
    _init_repo(repo_root)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo_root))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo_root.resolve())
    main_sha = _git(["rev-parse", "main"], cwd=repo_root).stdout.strip()
    _git(["checkout", "-b", "elsewhere"], cwd=repo_root)
    (repo_root / "noise.txt").write_text("not the base\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_root)
    _git(["commit", "-m", "noise"], cwd=repo_root)

    ticket = Ticket(
        external_id="772-from-start-point",
        branch="loregarden/772-from-start-point",
        work_item_type=WorkItemType.TASK,
        title="Cut",
        workspace_id="w",
    )
    ensure_ticket_branch(repo_root, ticket, start_point="main", session=session)

    assert _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip() == main_sha
    assert not (repo_root / "noise.txt").exists()


# ---------------------------------------------------------------------------
# lg-milestone-that-916: `repo_root` is the ticket's own worktree now.
#
# 779 removed the `repo_root != workspace_root` skip, so every dispatch runs
# `ensure_ticket_branch` inside the ticket's worktree. Two decisions in there
# still read `repo_root` as the repository's main working tree: whether the
# tree holding the branch may be force-removed, and which `PrimaryCheckoutUse`
# is being refused. These pin the first one, from both sides — the guard that
# must start firing, and the guard that must keep NOT firing, because "never
# remove anything" would satisfy the first on its own.
# ---------------------------------------------------------------------------

TICKET_BRANCH = "loregarden/tkt"


def _names(message: str, path: Path) -> bool:
    """Does `message` name `path`, however git spelled it?

    git reports a worktree path as it was recorded; the test knows it as
    `tmp_path` built it. On a platform where `tmp_path` sits behind a symlink
    the two spellings differ, and which one reaches the operator is not what
    these tests are about.
    """
    return str(path) in message or str(path.resolve()) in message


@pytest.fixture(name="main_checkout")
def main_checkout_fixture(tmp_path: Path) -> Path:
    """The repository's main working tree, on `main`, with one commit."""
    root = tmp_path / "primary"
    root.mkdir()
    _init_repo(root)
    return root


@pytest.fixture(name="main_holds_the_branch")
def main_holds_the_branch_fixture(main_checkout: Path) -> Path:
    """The main working tree, sitting on the ticket's branch.

    What every pre-779 worktree-less dispatch and every landing leaves behind,
    and the state the ticket's reproduction starts from.
    """
    _git(["checkout", "-q", "-b", TICKET_BRANCH], cwd=main_checkout)
    return main_checkout


@pytest.fixture(name="execution_root")
def execution_root_fixture(tmp_path: Path, main_checkout: Path) -> Path:
    """The ticket's own worktree, left on a branch that is not the ticket's.

    A crashed stage, a resolver or a person can leave it here; the dispatch
    then has to switch it back, which is where the lock is met.
    """
    tree = tmp_path / "ticket-worktree"
    _git(["worktree", "add", "-q", "-b", "other", str(tree)], cwd=main_checkout)
    return tree


@pytest.fixture(name="scratchpad")
def scratchpad_fixture(tmp_path: Path, main_checkout: Path) -> Path:
    """A stale linked worktree holding the ticket's branch — the 2024 case.

    Under ``tmp_path/".worktrees"`` since lg-external-harness-1452: that is
    `WorktreeService.worktree_base` for both dispatch roots these tests use
    (their parent is `tmp_path` either way), and the gate refuses a path
    outside it as OUTSIDE_BASE without reading a premise. It carries NO
    `worktrees` row — the tests that want it removed call
    `_grant_the_gate_its_premises` for one.
    """
    tree = tmp_path / ".worktrees" / "scratchpad"
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", TICKET_BRANCH, str(tree)], cwd=main_checkout)
    return tree


@pytest.fixture(name="held_ticket")
def held_ticket_fixture() -> Ticket:
    return Ticket(
        external_id="916-holder",
        branch=TICKET_BRANCH,
        work_item_type=WorkItemType.TASK,
        title="Holder",
        workspace_id="w",
    )


@pytest.fixture(name="git_calls")
def git_calls_fixture():
    """Every git argv `git_branch` itself issues, in order."""
    from loregarden.services import git_branch as git_branch_mod

    calls: list[list[str]] = []
    real = git_branch_mod.run_git

    def spy(args, **kwargs):
        calls.append(list(args))
        return real(args, **kwargs)

    with mock.patch.object(git_branch_mod, "run_git", spy):
        yield calls


def _forced_removals(git_calls: list[list[str]]) -> list[list[str]]:
    return [call for call in git_calls if call[:3] == ["worktree", "remove", "--force"]]


def test_a_worktree_dispatch_does_not_force_remove_the_main_working_tree(
    main_holds_the_branch,
    execution_root,
    held_ticket,
    git_calls,
    caplog,
    session,
):
    """AC1. The reproduction in the ticket, verbatim.

    `_is_primary_checkout` compared `locked_at` against `repo_root`. Called
    from the ticket's worktree, the operator's main checkout no longer matched,
    so the guard stopped guarding and the removal was aimed at it. git refuses
    to remove a main working tree (exit 128), so nothing was lost — this is
    defence in depth that must defend again.
    """
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert "Refusing to remove primary checkout" in caplog.text
    assert _forced_removals(git_calls) == [], (
        "the removal must never be aimed at the repository's main working tree"
    )
    assert _names(str(raised.value), main_holds_the_branch), (
        "the operator has to be told which tree is holding the stage up"
    )


def test_the_refusal_does_not_hand_the_operator_a_command_that_cannot_work(
    main_holds_the_branch,
    execution_root,
    held_ticket,
    git_calls,
    session,
):
    """AC2/UX-2. `git worktree remove --force <main working tree>` exits 128
    however the operator runs it. The remedy printed has to be one that can
    succeed, and no path may print the one that cannot.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert "worktree remove --force" not in str(raised.value)


def test_the_remedy_detaches_when_the_dispatch_had_no_start_point(
    main_holds_the_branch,
    execution_root,
    held_ticket,
    git_calls,
    session,
):
    """UX-2(b). With nowhere named to send the holder, detaching frees the
    branch without choosing another one on the operator's behalf.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert "switch --detach" in message
    assert "switch --detach HEAD" not in message, "`--detach` takes no argument here"


def test_the_remedy_names_the_start_point_when_the_dispatch_had_one(
    main_holds_the_branch,
    execution_root,
    held_ticket,
    git_calls,
    session,
):
    """UX-2(b). A dispatch that knows where the branch was cut from can send the
    holder somewhere, rather than leaving the operator on a detached HEAD.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, start_point="main", session=session)

    assert "switch main" in str(raised.value)
    assert "switch --detach" not in str(raised.value)


def test_a_stale_linked_worktree_is_still_removed_and_the_checkout_retried(
    main_checkout,
    execution_root,
    scratchpad,
    held_ticket,
    git_calls,
    session,
):
    """S-9. The guard that stays, through the gate (AC1).

    Without this, "never remove anything" passes every refusal test in this
    module — and that is the whole mutant: a `_try_free_worktree_lock` whose
    body is `return False, note` satisfies "it does not force-remove" and
    leaves every stage that meets a stale scratchpad permanently blocked. So
    the stale tree is given a row and a receipt, and the dispatch still has to
    get all the way to the branch.

    What changed is only who removes it. `git_branch` issues no removal of its
    own any more; the directory going is the gate's doing, and the row moving
    to CLEANUP is how the gate records it.
    """
    row = _grant_the_gate_its_premises(
        session,
        repo_root=main_checkout,
        tree=scratchpad,
        branch=TICKET_BRANCH,
        external_id="lg-s9-worktree",
    )

    assert ensure_ticket_branch(execution_root, held_ticket, session=session) == TICKET_BRANCH

    assert _forced_removals(git_calls) == [], (
        "`git_branch` asks the gate; it never runs `worktree remove` itself (AC1/AC5)"
    )
    assert not scratchpad.exists(), "the stale tree still goes — through the gate"
    assert _git(["branch", "--show-current"], cwd=execution_root).stdout.strip() == TICKET_BRANCH
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value


def test_a_locking_checkout_nested_under_worktrees_is_released_and_the_branch_checks_out(
    tmp_path, session
):
    """AC3. Production geometry the sibling fixtures hide.

    Live dispatch stands in the ticket's own worktree, which already lives
    under `.worktrees/`. Scoping the receipt gate from that path names
    ``<ws_parent>/.worktrees/.worktrees`` as the base, so every real locking
    checkout is OUTSIDE_BASE and self-repair never reaches DELETED.

    These paths are local, not the shared `execution_root` / `scratchpad`
    fixtures: those sit at ``tmp_path/ticket-worktree`` beside
    ``tmp_path/primary``, so both parents are ``tmp_path`` and the lie never
    fires. `_grant_the_gate_its_premises` is called with ``repo_root=main``
    only — never the nested dispatch root — because planting
    ``Workspace.repo_path`` on the nested tree is the same formula as the bug.
    """
    main = tmp_path / "loregarden"
    main.mkdir()
    _init_repo(main)

    dispatch_root = tmp_path / ".worktrees" / "ticket-mine"
    dispatch_root.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", "other", str(dispatch_root)], cwd=main)

    locked = tmp_path / ".worktrees" / "scratchpad"
    branch = "loregarden/nested-lock"
    _git(["worktree", "add", "-q", "-b", branch, str(locked)], cwd=main)

    assert dispatch_root.is_relative_to(main.parent / ".worktrees")
    row = _grant_the_gate_its_premises(
        session,
        repo_root=main,
        tree=locked,
        branch=branch,
        external_id="lg-1488-nested",
    )
    workspace = session.get(Workspace, row.workspace_id)
    assert workspace is not None
    assert workspace.repo_path == str(main)

    ticket = Ticket(
        external_id="lg-1488-nested-dispatch",
        branch=branch,
        work_item_type=WorkItemType.TASK,
        title="Nested dispatch",
        workspace_id="w",
    )

    assert ensure_ticket_branch(dispatch_root, ticket, session=session) == branch
    assert not locked.exists(), "the locking checkout is released through the gate"
    assert _git(["branch", "--show-current"], cwd=dispatch_root).stdout.strip() == branch
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value


def test_a_topology_git_cannot_identify_removes_nothing(
    main_checkout,
    execution_root,
    scratchpad,
    held_ticket,
    git_calls,
    caplog,
    session,
):
    """S-2/UX-6, through the caller.

    The holder here IS a removable linked worktree — the test above proves the
    removal would otherwise be issued. With `main_working_tree` unable to
    answer, it must not be: a guess that reads as safe is the failure mode this
    whole ticket is about.
    """
    from loregarden.services import git_branch as git_branch_mod

    with (
        mock.patch.object(git_branch_mod, "main_working_tree", return_value=None),
        caplog.at_level(logging.WARNING),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert "Refusing to remove primary checkout" in caplog.text
    assert _forced_removals(git_calls) == []
    message = str(raised.value)
    assert "worktree remove --force" not in message
    assert _names(message, scratchpad), "the operator still needs the path"
    assert "switch --detach" in message, (
        "detaching frees the branch whether or not that tree is the main one"
    )
    assert scratchpad.exists(), "and the tree it could not classify is still there"


def test_the_main_working_tree_is_derived_once_per_call(main_checkout, held_ticket, session):
    """S-3. Two decisions read this fact. Derived twice they can disagree, and
    the disagreement — the guard protecting one tree while the message names
    another — is exactly the defect being fixed. One `rev-parse` per dispatch,
    and `ensure_ticket_branch` fires once per stage agent.
    """
    from loregarden.services import worktree_snapshot

    reads: list[list[str]] = []
    real = worktree_snapshot.run_git

    def spy(args, **kwargs):
        reads.append(list(args))
        return real(args, **kwargs)

    with mock.patch.object(worktree_snapshot, "run_git", spy):
        ensure_ticket_branch(main_checkout, held_ticket, session=session)

    common_dir = [args for args in reads if "--git-common-dir" in args]
    assert len(common_dir) == 1, common_dir
    assert common_dir[0] == ["rev-parse", "--path-format=absolute", "--git-common-dir"], (
        "`--path-format=absolute` has to precede `--git-common-dir` to apply to it"
    )


# --- S-6: which tree the refusal names, decided from the one derived fact ----
#
# `require_clean_tree` was called with `TICKET_BRANCH_CHECKOUT` unconditionally.
# Handed the ticket's own worktree, that tells the operator to go and clean the
# shared checkout — which is not the tree holding their work.


def _dirty_off_the_branch(tree: Path) -> None:
    """A tree that must be switched, holding work nobody recorded."""
    _git(["checkout", "-q", "-b", "scratch/left-behind"], cwd=tree)
    (tree / "left-in-flight.txt").write_text("an agent was mid-stage here\n", encoding="utf-8")


def test_a_refused_ticket_worktree_is_named_as_a_ticket_worktree(
    main_checkout,
    execution_root,
    held_ticket,
    session,
):
    _dirty_off_the_branch(execution_root)

    with pytest.raises(DirtyPrimaryCheckoutError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert raised.value.use is PrimaryCheckoutUse.TICKET_WORKTREE_BRANCH_CHECKOUT
    assert raised.value.repo_root == execution_root


def test_a_refused_main_working_tree_keeps_the_shared_checkout_use(
    main_checkout, held_ticket, session
):
    """The distinction only means something if the old value still happens."""
    _dirty_off_the_branch(main_checkout)

    with pytest.raises(DirtyPrimaryCheckoutError) as raised:
        ensure_ticket_branch(main_checkout, held_ticket, session=session)

    assert raised.value.use is PrimaryCheckoutUse.TICKET_BRANCH_CHECKOUT


def test_a_topology_git_cannot_identify_keeps_todays_use(
    main_checkout,
    execution_root,
    held_ticket,
    session,
):
    """S-6. `main_tree is None` fails closed here too: an unknown topology keeps
    the behaviour that shipped rather than guessing the new one.
    """
    from loregarden.services import git_branch as git_branch_mod

    _dirty_off_the_branch(execution_root)

    with (
        mock.patch.object(git_branch_mod, "main_working_tree", return_value=None),
        pytest.raises(DirtyPrimaryCheckoutError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert raised.value.use is PrimaryCheckoutUse.TICKET_BRANCH_CHECKOUT


# ---------------------------------------------------------------------------
# test-break: the mutants the suite above still lets through.
#
# Each test here names a change to `git_branch` that would keep every test
# above green while leaving an acceptance criterion false:
#
#   * remove nothing unless the call came from a linked worktree    -> S-9/AC1
#   * derive the main working tree again inside the lock path       -> S-3
#   * derive it before the `.git` check                             -> S-3
#   * name both trees in the refusal, "to be helpful"               -> UX-1
#   * give UNIDENTIFIED the MAIN shape's `start_point` remedy       -> S-5
#   * give a failed removal of a real linked worktree a new shape   -> S-5(a)
#   * give the no-lock checkout failure a holder shape              -> S-5
# ---------------------------------------------------------------------------


def _common_dir_reads() -> tuple[mock._patch, list[list[str]]]:
    """Patch where `git_reading` resolves `run_git`, and collect the reads.

    Not `git_branch.run_git`: `main_working_tree` goes through
    `worktree_snapshot.git_reading`, which binds `run_git` from *its* module
    globals. A spy on this module's would count zero and pass whether the
    derivation happened once, twice or never.
    """
    from loregarden.services import worktree_snapshot

    reads: list[list[str]] = []
    real = worktree_snapshot.run_git

    def spy(args, **kwargs):
        if "--git-common-dir" in args:
            reads.append(list(args))
        return real(args, **kwargs)

    return mock.patch.object(worktree_snapshot, "run_git", spy), reads


def test_a_stale_worktree_is_removed_when_the_dispatch_runs_in_the_main_tree(
    main_checkout,
    scratchpad,
    held_ticket,
    git_calls,
    session,
):
    """S-9, from the other side of 779, through the gate (AC1).

    Every removal test above is called from the ticket's own worktree, so
    "only remove when the tree I was handed is not the main one" passes them
    all — and silently stops self-repairing the shared-checkout dispatch that
    `_try_free_worktree_lock` was written for. The decision is about
    `locked_at`, never about which tree asked.
    """
    row = _grant_the_gate_its_premises(
        session,
        repo_root=main_checkout,
        tree=scratchpad,
        branch=TICKET_BRANCH,
        external_id="lg-s9-main-tree",
    )

    assert ensure_ticket_branch(main_checkout, held_ticket, session=session) == TICKET_BRANCH

    assert _forced_removals(git_calls) == []
    assert not scratchpad.exists()
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value


def test_the_derivation_is_still_once_when_the_branch_is_locked(
    main_holds_the_branch,
    execution_root,
    held_ticket,
    session,
):
    """S-3 on the path that actually has two readers.

    The counting test above takes the clean path, where the lock code never
    runs — so a `main_working_tree(repo_root)` call inside
    `_try_free_worktree_lock` (the obvious shape, since that is where the
    comparison used to live) is invisible to it. This is the dispatch where
    the guard and the message both read the fact, and where deriving it twice
    is what lets them disagree.
    """
    patch, reads = _common_dir_reads()

    with patch, pytest.raises(ValueError):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert len(reads) == 1, reads


def test_the_derivation_is_still_once_when_the_tree_is_refused(
    main_checkout,
    execution_root,
    held_ticket,
    session,
):
    """S-3 on the refusal path: `require_clean_tree` is reached through the
    same single value, and a second read here would be a second answer.
    """
    _dirty_off_the_branch(execution_root)
    patch, reads = _common_dir_reads()

    with patch, pytest.raises(DirtyPrimaryCheckoutError):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert len(reads) == 1, reads


def test_nothing_is_asked_of_git_when_the_path_is_not_a_repository(tmp_path, held_ticket, session):
    """S-3's placement: *after* the `.git` check.

    Derived before it, a directory with no repository gets a WARNING from
    `git_reading` and a different error than the one this function has always
    raised — and `require_clean_checkout` deliberately lets a non-repository
    through, so this is a shape callers meet.
    """
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    patch, reads = _common_dir_reads()

    with patch, pytest.raises(ValueError, match="not a git repository"):
        ensure_ticket_branch(outside, held_ticket, session=session)

    assert reads == [], "the `.git` check answers first, so git is never run"


def test_the_refusal_names_the_holder_and_nothing_else(
    main_holds_the_branch,
    execution_root,
    held_ticket,
    git_calls,
    session,
):
    """UX-1. One question: which tree is holding this stage up. A message that
    names the ticket's worktree as well — the tree the operator is *not* meant
    to touch — answers it twice, and `.tl-output-title`'s one ellipsised line
    is the surface this lands on.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert _names(message, main_holds_the_branch), "the holder, by absolute path"
    assert not _names(message, execution_root), (
        "and not the tree that is merely where the dispatch was standing"
    )


def test_an_unidentified_topology_detaches_even_when_a_start_point_was_given(
    main_checkout,
    execution_root,
    scratchpad,
    held_ticket,
    git_calls,
    session,
):
    """S-5. UNIDENTIFIED's remedy is `switch --detach` *always*.

    `switch <start_point>` would be the MAIN shape's remedy, and sharing that
    branch is the natural way to write this. It is wrong here for a reason the
    operator cannot see: the tree may be a linked worktree whose branch is the
    only thing holding commits, and `switch main` in it is a different action
    from freeing the branch.
    """
    from loregarden.services import git_branch as git_branch_mod

    with (
        mock.patch.object(git_branch_mod, "main_working_tree", return_value=None),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, start_point="main", session=session)

    message = str(raised.value)
    assert "switch --detach" in message
    assert "switch main" not in message


def test_a_linked_worktree_that_will_not_be_removed_keeps_the_remedy_it_has(
    tmp_path,
    main_checkout,
    execution_root,
    held_ticket,
    git_calls,
    caplog,
    session,
):
    """S-5(a), rewritten for AC11. The refusal, and the remedy that replaces it.

    This used to be the one shape where the self-repair ran, failed, and the
    operator was handed `git worktree remove --force` — the command the gate
    now exists to refuse on their behalf. Under lg-external-harness-1452 a
    locked tree with no `worktrees` row is refused *before* git is asked, so
    git's own "locked" words are unreachable from here and the only thing
    there is to pin is the refusal: the tree survives, the note says so, and
    the remedy printed is the detach/switch one the other two holder shapes
    already use.
    """
    locked = tmp_path / ".worktrees" / "locked"
    locked.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", TICKET_BRANCH, str(locked)], cwd=main_checkout)
    _git(["worktree", "lock", str(locked)], cwd=main_checkout)

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert _forced_removals(git_calls) == [], "the gate is asked; git is not"
    assert "worktree remove" not in message, (
        "the refusal may not hand back the destructive command it just refused (AC11)"
    )
    assert "--force" not in message
    assert "switch --detach" in message, (
        "the detach/switch remedy the other two holder shapes already print"
    )
    assert _names(message, locked), "the operator still needs the path"
    assert any(record.levelno >= logging.WARNING for record in caplog.records), (
        "a self-repair that could not repair says so; its wording is not pinned here"
    )
    assert locked.exists()
    assert session.exec(select(Worktree).where(Worktree.worktree_path == str(locked))).all() == []


# ---------------------------------------------------------------------------
# lg-external-harness-1452: the self-repair asks the receipt gate, and reports
# the gate's answer in words the operator can act on.
#
# The defect: `_try_free_worktree_lock` ran `git worktree remove --force` on
# any path git called a linked worktree. No receipt, no claim, no dirtiness
# probe, no `worktrees` row — a destructive act reached from an observation of
# shape, and the one site lg-external-harness-1026 did not route.
#
# AC1 fixes who decides, AC3/AC10/AC12 fix what the operator reads, AC4 is the
# reproduction, AC5 is the census and AC11 is the remedy copy.
# ---------------------------------------------------------------------------

GIT_BRANCH_SOURCE = (
    Path(__file__).resolve().parents[1] / "loregarden" / "services" / "git_branch.py"
).read_text(encoding="utf-8")

#: AC10. The two verdict sentences, and the budget they were sized to.
PREMISE_VERDICT = "Nothing was deleted; the branch is still held by another checkout."
ERROR_VERDICT = "Nothing was deleted; the worktree gate could not finish its check."
FIRST_LINE_BUDGET = 80


@pytest.fixture(name="unreceipted_scratchpad")
def unreceipted_scratchpad_fixture(scratchpad: Path) -> dict[str, str]:
    """AC4's tree: a linked worktree holding the branch, with work in it and no row.

    One untracked file and one modified tracked file — the two things
    `worktree remove --force` destroys without a word, and the two things the
    repro in the ticket measured gone. The `worktrees` table knows nothing
    about this path, which is the 11-trees-out-of-44 case AC9 measured on live.
    """
    untracked = scratchpad / "draft.txt"
    untracked.write_text("an agent was mid-stage here\n", encoding="utf-8")
    tracked = scratchpad / "README.md"
    tracked.write_text("# test\nedited, never committed\n", encoding="utf-8")
    return {
        str(untracked): untracked.read_text(encoding="utf-8"),
        str(tracked): tracked.read_text(encoding="utf-8"),
    }


def test_a_registered_path_with_no_worktrees_row_keeps_its_tree_and_its_work(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """AC4. The reproduction, as the behaviour it should have had.

    `git -C main worktree remove --force ../agent` exits 0 and takes the
    directory, the untracked file and the uncommitted edit with it. Nothing
    here is recoverable afterwards, which is why the premise has to be a
    record rather than a shape.
    """
    with pytest.raises(ValueError):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert scratchpad.is_dir(), "the checkout the gate had no receipt for was removed"
    for path, content in unreceipted_scratchpad.items():
        assert Path(path).read_text(encoding="utf-8") == content, f"{path} did not survive"
    assert session.exec(select(Worktree)).all() == [], (
        "a refusal writes no row; the gate had nothing to record one against"
    )


def test_the_refusal_leads_with_its_verdict_and_carries_the_gate_s_own_words(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """AC3 + AC10. What the operator reads, in the order they read it.

    `TicketAttention` renders `firstLine(blocking_issues)` on one ellipsed
    line and `.toast__message` clamps to about four. The gate's own sentence
    opens `"Nothing was deleted: {path} has no worktrees row…"`, and every
    live worktree path is 87–88 characters — so returning `outcome.detail` as
    the note puts the path in front of every word that matters. That is the
    defect siblings -1167 and -1208 closed for this same clamp.

    So: one verdict sentence first, inside the budget, with no path and no
    branch name in it. The gate's detail follows, verbatim, on a later line.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    first = message.splitlines()[0]
    assert PREMISE_VERDICT in first, f"the verdict has to lead; got {first!r}"
    assert len(PREMISE_VERDICT) <= FIRST_LINE_BUDGET
    assert not _names(first, scratchpad), "no path on the first line (AC10)"
    assert TICKET_BRANCH not in first, "no branch name on the first line (AC10)"
    assert "has no worktrees row" in message, "the gate's own premise, verbatim, later"
    assert _names(message, scratchpad), "and the path, later"


def test_the_note_reaches_the_operator_ahead_of_the_held_branch_message(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """AC3. The note is not just logged — it is carried on the `ValueError`.

    A refusal recorded only in a log line is a refusal the operator never
    reads: `blocking_issues` and the run's stderr both come from this
    exception, and the reader is reached from `TicketAttention`'s existing
    `Read` button (AC8).
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    held = message.index("is already checked out in another worktree")
    assert message.index(PREMISE_VERDICT) < held, (
        "the note comes ahead of `_held_branch_message`, so 'what was kept' is read first"
    )


def test_a_gate_that_could_not_run_says_so_rather_than_claiming_a_refusal(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session, caplog
):
    """AC12(b). "The gate refused" and "the gate could not run" stay apart.

    Collapsing them is how a broken check reports itself as a clean answer —
    the failure mode `No silent failures` is about. The note carries the
    failure's own words and the log records it at `warning` or above.
    """
    from loregarden.services import git_branch as git_branch_mod

    with (
        mock.patch.object(
            git_branch_mod,
            "release_checkout",
            side_effect=RuntimeError("the gate exploded"),
        ),
        caplog.at_level(logging.WARNING),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    first = message.splitlines()[0]
    assert ERROR_VERDICT in first, f"the ERROR verdict, not the refusal one; got {first!r}"
    assert len(ERROR_VERDICT) <= FIRST_LINE_BUDGET
    assert "has no worktrees row" not in message, (
        "a gate that did not run may not borrow the no-receipt sentence (AC12b)"
    )
    assert "the gate exploded" in message, "the failure's own words reach the operator"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert scratchpad.is_dir()


def test_a_parked_removal_is_a_refusal_and_names_where_the_bytes_went(
    main_checkout, execution_root, scratchpad, held_ticket, session
):
    """AC3, last clause / A4. PARKED is not a repair.

    Every premise held, the removal failed, and a copy was renamed somewhere
    unguessable. The branch is still held, so the checkout retry would fail
    again — and `parked_path` is the only record of where the work went, so
    the note has to name it.
    """
    from loregarden.models.domain import RemovalOutcome
    from loregarden.services import git_branch as git_branch_mod
    from loregarden.services.checkout_release import WorktreeRemoval

    parked = str(scratchpad) + ".parked"
    with (
        mock.patch.object(
            git_branch_mod,
            "release_checkout",
            return_value=WorktreeRemoval(
                outcome=RemovalOutcome.PARKED,
                path=str(scratchpad),
                reason="cleanup_failed",
                detail="Nothing was deleted: the release could not finish.",
                parked_path=parked,
            ),
        ),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert PREMISE_VERDICT in message.splitlines()[0]
    assert parked in message, "the only record of where the bytes went"


def test_an_empty_detail_from_the_gate_still_says_what_was_kept(
    main_checkout, execution_root, scratchpad, held_ticket, session
):
    """AC12(a). A refusal never renders blank."""
    from loregarden.models.domain import RemovalOutcome
    from loregarden.services import git_branch as git_branch_mod
    from loregarden.services.checkout_release import WorktreeRemoval

    with (
        mock.patch.object(
            git_branch_mod,
            "release_checkout",
            return_value=WorktreeRemoval(
                outcome=RemovalOutcome.REFUSED, path=str(scratchpad), reason="", detail=""
            ),
        ),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert PREMISE_VERDICT in message.splitlines()[0]
    assert _names(message, scratchpad)


def test_the_only_empty_note_is_the_holder_that_was_never_a_candidate(
    main_holds_the_branch, execution_root, held_ticket, session
):
    """AC12(a), the other half of the distinction.

    The non-LINKED_WORKTREE early return removes nothing and asks nothing, so
    there is no gate answer to report: `_held_branch_message` alone carries
    the explanation, and prefixing it with a verdict about a check that never
    ran would be a sentence with nothing behind it. This is the ONE permitted
    empty note, and pinning it is what stops "" becoming the fallback
    everywhere else.
    """
    from loregarden.models.domain import BranchHolder
    from loregarden.services import git_branch as git_branch_mod

    repaired, note = git_branch_mod._try_free_worktree_lock(
        TICKET_BRANCH,
        str(main_holds_the_branch),
        holder=BranchHolder.MAIN_WORKING_TREE,
        session=session,
    )

    assert repaired is False
    assert note == ""


def test_a_refused_linked_worktree_never_returns_an_empty_note(
    main_checkout, execution_root, scratchpad, held_ticket, session
):
    """AC12(a). The same function, the shape that must never be blank."""
    from loregarden.models.domain import BranchHolder
    from loregarden.services import git_branch as git_branch_mod

    repaired, note = git_branch_mod._try_free_worktree_lock(
        TICKET_BRANCH,
        str(scratchpad),
        holder=BranchHolder.LINKED_WORKTREE,
        session=session,
    )

    assert repaired is False
    assert note.strip(), "a gate refusal is never reported as silence"
    assert note != "Nothing was deleted: " + str(scratchpad), "the note is composed, not `detail`"


def test_no_operator_facing_string_in_git_branch_recommends_a_destructive_command():
    """AC11 + AC5. The census, as a sentence about this module.

    Four occurrences tripped it when the ticket was filed: the docstring
    defending the string, the operator remedy that printed it, the call
    itself, and the `"worktree remove failed"` error text. All four go — and
    the census in `test_worktree_gate_chokepoint.py` greps the same source, so
    this test and that one are one edit.
    """
    stripped = GIT_BRANCH_SOURCE.replace("# ", "")
    assert "worktree remove" not in stripped
    assert "--force" not in GIT_BRANCH_SOURCE
    assert "shutil.rmtree" not in GIT_BRANCH_SOURCE


def test_git_branch_holds_no_second_answer_to_may_this_checkout_be_removed():
    """AC1. One place decides, and it is not here.

    A `worktrees` query, a second spelling of the no-receipt sentence, a
    dirtiness probe or a `--force` escape hatch in this module would each be a
    second site deciding the question — which is the defect class the ticket
    closes, reintroduced one function away from where it was removed.
    """
    import ast

    #: A query, named as the three calls it is actually made of, rather than as
    #: the substring `"Worktree"`. The constraint is "no `worktrees` query in
    #: this module", and the spec requires `git_branch` to read
    #: `WorktreeRemoval` — so a census spelled as a substring forbids the
    #: annotation the ticket asks for and drives the implementation towards an
    #: unannotated one. lg-external-harness-1452, test-break.
    tree = ast.parse(GIT_BRANCH_SOURCE)
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported & {"select", "Worktree", "WorktreeState", "WorktreeService"}, (
        f"a `worktrees` query's ingredients are imported here: {sorted(imported)}"
    )
    issued = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in {"exec", "scalars"}
    ]
    assert issued == [], f"a query is issued here, at lines {issued}"
    assert "has no worktrees row" not in GIT_BRANCH_SOURCE, "one spelling, in the gate"

    body = next(
        ast.get_source_segment(GIT_BRANCH_SOURCE, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_try_free_worktree_lock"
    )
    for banned in ("--porcelain", "status", "force", "settings."):
        assert banned not in body, (
            f"`_try_free_worktree_lock` names {banned!r}: it adds a premise, a dirtiness "
            "probe, a force switch or a config flag the gate already owns (AC1)"
        )


def test_the_gate_is_imported_at_module_level_and_the_app_still_imports():
    """AC1. No function-local import to dodge the cycle (CLAUDE.md).

    `git_branch -> worktree_lifecycle` used to close a cycle through
    `worktree_receipt`, `worktree_service` and `target_branch` — all three of
    which imported `git_branch` only for a name helper. `services/branch_names.py`
    is what cuts that edge, and with no re-export left behind the cut is real
    rather than cosmetic.
    """
    import ast
    import importlib

    import loregarden.main  # noqa: F401
    from loregarden.services import git_branch as git_branch_mod

    assert (
        git_branch_mod.release_checkout
        is importlib.import_module("loregarden.services.checkout_release").release_checkout
    )

    # Inside a function body, not merely indented: a module-level
    # `if TYPE_CHECKING:` block is an indented import and is not the thing
    # CLAUDE.md forbids, so a col_offset census would reject an
    # implementation the ticket permits. lg-external-harness-1452, test-break.
    module = ast.parse(GIT_BRANCH_SOURCE)
    nested = [
        node.lineno
        for func in ast.walk(module)
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(func)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert nested == [], f"function-local imports at lines {nested}"

    names = importlib.import_module("loregarden.services.branch_names")
    assert callable(names.resolve_ticket_branch)
    assert not hasattr(git_branch_mod, "validate_branch_name"), (
        "no re-export is left behind: the posture is `internal`, so every caller moved"
    )


@pytest.mark.parametrize("func_name", ["ensure_ticket_branch", "_try_free_worktree_lock"])
def test_session_is_required_and_keyword_only(func_name: str):
    """AC1. No default, and not positional.

    `Session | None = None` ships a path where the gate never runs, and
    `object_session(ticket)` was measured dead: this module builds bare
    detached `Ticket` objects, so it would answer `None` at every call site
    and skip the gate in silence. A required parameter is visible churn by
    design.
    """
    import inspect

    from loregarden.services import git_branch as git_branch_mod

    param = inspect.signature(getattr(git_branch_mod, func_name)).parameters["session"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_a_checkout_failure_that_is_not_a_lock_still_reports_itself(
    main_checkout,
    held_ticket,
    git_calls,
    session,
):
    """S-5. The first raise site has no holder, because nothing holds anything.

    Threading a holder into all three raise sites invites the default
    (`LINKED_WORKTREE`) into this one, which would print a worktree remedy for
    a failure that has no worktree in it and bury git's own words.
    """
    from loregarden.services import git_branch as git_branch_mod

    detail = "fatal: invalid reference: refs/heads/nope"

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "checkout"], stderr=detail)

    with (
        mock.patch.object(git_branch_mod, "_checkout_branch", boom),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(main_checkout, held_ticket, session=session)

    message = str(raised.value)
    assert detail in message, "git said why; that is what the operator needs"
    assert "worktree remove --force" not in message
    assert "switch --detach" not in message


# ---------------------------------------------------------------------------
# lg-milestone-that-918: the refresh asks before it works.
#
# Since 779 every stage dispatch puts the ticket's tree on its branch and
# merges its target into it. In the overwhelmingly common case nothing has
# landed on the target since the last dispatch, and the whole sequence —
# `rev-parse --verify`, `checkout`, `status --porcelain`, `merge --no-edit` —
# is four git processes to discover that there was nothing to do. One
# `merge-base --is-ancestor` answers the same question.
#
# The fast path is gated on *both* halves, and neither may be dropped:
#
#   (1) the tree is already on the ticket's branch. When it is not, the slow
#       path's fix-up is the only thing that puts it back, and that fix-up is
#       lg-milestone-that-772's no-loss guarantee.
#   (2) the start point is already an ancestor of HEAD. When it is not, a merge
#       is genuinely owed.
#
# A mutant that drops either half, or that swaps `is_ancestor`'s ancestor and
# descendant, passes any test that only exercises the already-up-to-date case.
# The tests below are the ones it does not pass.
# ---------------------------------------------------------------------------

TARGET_BRANCH = "integration/918"


@pytest.fixture(name="git_processes")
def git_processes_fixture():
    """Every git argv started while the fixture is active, in order.

    Counted at `subprocess.Popen` rather than at `run_git`: `git_branch`,
    `git_merge_noco` and `worktree_snapshot` each bind `run_git` by name, so a
    patch on any one of them counts only its own module's questions.

    The app's reconcile timer is held off for the same reason
    `test_stage_git_snapshot.py` holds it off: a sweep that fires inside the
    window counts its own `for-each-ref` against the call under test, which
    makes the count depend on how long the window lasted.
    """
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


def _asking(git_processes: list[list[str]], *tokens: str) -> list[list[str]]:
    return [argv for argv in git_processes if all(token in argv for token in tokens)]


def _head(tree: Path, ref: str = "HEAD") -> str:
    return _git(["rev-parse", ref], cwd=tree).stdout.strip()


def _commit_on_target(main_checkout: Path, filename: str, content: str) -> str:
    """One commit on the target branch, landed by a sibling since the tree was cut."""
    on = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=main_checkout).stdout.strip()
    _git(["checkout", "-q", TARGET_BRANCH], cwd=main_checkout)
    (main_checkout / filename).write_text(content, encoding="utf-8")
    _git(["add", "."], cwd=main_checkout)
    _git(["commit", "-m", f"{filename} on the target"], cwd=main_checkout)
    landed = _head(main_checkout)
    _git(["checkout", "-q", on], cwd=main_checkout)
    return landed


@pytest.fixture(name="target_tree")
def target_tree_fixture(tmp_path: Path, main_checkout: Path) -> Path:
    """The ticket's own worktree, on its branch, cut from the target.

    The state nearly every dispatch meets: a tree already on the ticket's
    branch whose HEAD already contains everything on the target, plus a commit
    of the ticket's own on top.
    """
    _git(["branch", TARGET_BRANCH, "main"], cwd=main_checkout)
    tree = tmp_path / "ticket-worktree"
    _git(
        ["worktree", "add", "-q", "-b", TICKET_BRANCH, str(tree), TARGET_BRANCH],
        cwd=main_checkout,
    )
    (tree / "own.txt").write_text("the ticket's own earlier stage\n", encoding="utf-8")
    _git(["add", "."], cwd=tree)
    _git(["commit", "-m", "stage one"], cwd=tree)
    return tree


def test_a_tree_that_already_has_the_target_asks_one_question_and_stops(
    target_tree,
    held_ticket,
    git_processes,
    session,
):
    """AC4. Nothing landed since, so there is nothing to check out, nothing to
    stat and nothing to merge — and the one question that establishes it is
    `merge-base --is-ancestor`.
    """
    before = _head(target_tree)
    git_processes.clear()  # only the call under test is counted

    assert (
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH, session=session)
        == TICKET_BRANCH
    ), "the fast path returns the same branch name the slow path returns"

    assert len(_asking(git_processes, "merge-base", "--is-ancestor")) == 1, git_processes
    assert _asking(git_processes, "checkout") == [], git_processes
    assert _asking(git_processes, "status", "--porcelain") == [], git_processes
    assert _asking(git_processes, "merge") == [], git_processes
    assert _asking(git_processes, "rev-parse", "--verify") == [], git_processes
    assert _head(target_tree) == before, "and the tree is exactly where it was"


def test_a_dirty_tree_that_already_has_the_target_is_not_warned_about(
    target_tree,
    held_ticket,
    git_processes,
    caplog,
    session,
):
    """AC4. `_refresh_onto` logs "has uncommitted changes" when it declines to
    merge into a dirty tree. On the fast path there was nothing to refresh, so
    that warning would be telling the operator a refresh was skipped when none
    was owed. The uncommitted work is untouched either way.
    """
    (target_tree / "in-flight.txt").write_text("an agent is still writing this\n", encoding="utf-8")
    git_processes.clear()  # only the call under test is counted

    with caplog.at_level(logging.WARNING):
        assert (
            ensure_ticket_branch(
                target_tree, held_ticket, start_point=TARGET_BRANCH, session=session
            )
            == TICKET_BRANCH
        )

    assert "uncommitted" not in caplog.text, caplog.text
    assert _asking(git_processes, "merge") == [], git_processes
    assert (target_tree / "in-flight.txt").read_text(encoding="utf-8") == (
        "an agent is still writing this\n"
    )


def test_the_merge_still_happens_when_a_sibling_landed_on_the_target(
    main_checkout,
    target_tree,
    held_ticket,
    git_processes,
    session,
):
    """AC5(a). Half (2) of the gate. The target is no longer an ancestor of
    HEAD, so the refresh is owed and must still be made — a mutant that drops
    the ancestry test, or swaps its two arguments, leaves the ticket building
    on work that is three stages stale.
    """
    own = _head(target_tree)
    landed = _commit_on_target(main_checkout, "sibling.txt", "landed after the tree was cut\n")
    git_processes.clear()  # the fixture's own commit must not be counted as the refresh

    assert (
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH, session=session)
        == TICKET_BRANCH
    )

    merged = _git(["merge-base", "--is-ancestor", landed, "HEAD"], cwd=target_tree)
    assert merged.returncode == 0, "the landed commit must be reachable from the worktree's HEAD"
    assert (target_tree / "sibling.txt").exists()
    assert _git(["merge-base", "--is-ancestor", own, "HEAD"], cwd=target_tree).returncode == 0, (
        "and the ticket's own commit is not lost in the process"
    )
    assert _asking(git_processes, "merge", "--no-edit") != [], git_processes


def test_a_tree_left_on_another_branch_is_put_back_even_with_nothing_to_merge(
    target_tree,
    held_ticket,
    git_processes,
    session,
):
    """AC5(b). Half (1) of the gate. The target *is* already an ancestor of the
    ticket branch's tip, so an ancestry-only fast path would return here having
    left the tree on `scratch/left-behind` — and the dispatch would then run,
    commit and hand off on a branch that is not the ticket's
    (lg-milestone-that-772's no-loss guarantee, which lives in the slow path).
    """
    on_the_branch = _head(target_tree)
    _git(["checkout", "-q", "-b", "scratch/left-behind"], cwd=target_tree)
    (target_tree / "left-behind.txt").write_text("a resolver stopped here\n", encoding="utf-8")
    _git(["add", "."], cwd=target_tree)
    _git(["commit", "-m", "left behind"], cwd=target_tree)
    stranded = _head(target_tree)
    git_processes.clear()  # the setup's own `checkout -b` must not count as the fix-up

    assert (
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH, session=session)
        == TICKET_BRANCH
    )

    assert (
        _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_tree).stdout.strip() == TICKET_BRANCH
    ), "the fix-up that puts the tree back is only on the slow path"
    assert _head(target_tree) == on_the_branch
    assert _asking(git_processes, "checkout") != [], git_processes
    assert (
        _git(
            ["merge-base", "--is-ancestor", stranded, "scratch/left-behind"], cwd=target_tree
        ).returncode
        == 0
    ), "and the commits it was left holding are still reachable"


def test_no_start_point_still_goes_the_long_way(target_tree, held_ticket, git_processes, session):
    """AC4. `start_point=""` is "cut from HEAD, refresh onto nothing", not "the
    target is already reachable". `is_ancestor` answers False for an empty
    argument, so the gate needs no extra branch — this pins that it still does.
    """
    git_processes.clear()  # only the call under test is counted

    assert ensure_ticket_branch(target_tree, held_ticket, session=session) == TICKET_BRANCH

    assert _asking(git_processes, "checkout") != [], git_processes


def test_the_ancestry_check_is_the_one_public_helper(target_tree, held_ticket):
    """AC10. Two private `_is_ancestor` helpers already exist in this tree and a
    third would be the one that drifts. `git_branch` imports the public one
    from `git_merge_noco` at module level — the import is cycle-free, since
    `git_merge_noco` imports only `git_subprocess`.
    """
    import re

    import loregarden
    from loregarden.services import git_branch, git_merge_noco

    assert git_branch.is_ancestor is git_merge_noco.is_ancestor, (
        "the ancestry check must be the public helper, bound at module level"
    )
    root = Path(loregarden.__file__).parent
    pattern = re.compile(r"^def _is_ancestor\b", re.MULTILINE)
    private = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )
    # dependency_readiness's copy went when its question moved to work_landed.
    assert private == ["services/handoff_boundary.py"], (
        f"a new private ancestry helper was added: {private}"
    )


def test_the_main_working_tree_is_never_derived_twice_on_the_fast_path(
    target_tree,
    held_ticket,
    git_processes,
    session,
):
    """AC10. `main_working_tree` may move below the fast-path return — its two
    consumers are both unreachable from there — but moving it must not turn it
    into something derived lazily or twice. Two derivations that disagree is
    the defect lg-milestone-that-916 exists for.
    """
    git_processes.clear()  # only the call under test is counted

    ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH, session=session)

    common_dir = _asking(git_processes, "--git-common-dir")
    assert len(common_dir) <= 1, (
        f"the repository's main working tree was derived {len(common_dir)} times: {common_dir}"
    )


# ---------------------------------------------------------------------------
# lg-milestone-that-918, test-break: detached is not "on another branch".
#
# AC5(b) leaves the tree on `scratch/left-behind`, which kills a fast path that
# drops half (1) of the gate outright. It does not kill a gate written as
# `tree.branch in ("", branch)` or `not tree.branch or tree.branch == branch` —
# a plausible shape, since `read_tree` reports `""` for a tree it could not read
# and the author may reason that an unreadable tree has nothing to fix up.
#
# REQ-4 says the opposite in terms: a detached tree does *not* take the fast
# path, "so `tree.branch == branch` is false for any valid branch name". The
# consequence of getting it wrong is worse than the named-branch case, not
# better: a dispatch that runs detached commits to no ref at all, so its work
# is reachable from nothing once the next stage moves HEAD.
# ---------------------------------------------------------------------------


def test_a_detached_tree_that_already_has_the_target_is_still_put_back(
    target_tree,
    held_ticket,
    git_processes,
    session,
):
    """AC5(b). Detached at the ticket branch's own tip, so the target is
    already an ancestor of HEAD and half (2) of the gate is satisfied. Only
    half (1) stands between this tree and a dispatch that commits to nothing.
    """
    on_the_branch = _head(target_tree)
    _git(["checkout", "-q", "--detach"], cwd=target_tree)
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_tree).stdout.strip() == "HEAD", (
        "fixture: the tree must really be detached"
    )
    git_processes.clear()  # the setup's own detach must not count as the fix-up

    assert (
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH, session=session)
        == TICKET_BRANCH
    )

    assert (
        _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_tree).stdout.strip() == TICKET_BRANCH
    ), "a detached tree is owed the fix-up, and the fix-up is only on the slow path"
    assert _head(target_tree) == on_the_branch
    assert _asking(git_processes, "checkout") != [], git_processes


# ---------------------------------------------------------------------------
# lg-external-harness-1452, test-break. The adversarial half: the cases the
# design above either measured against a test constant instead of the produced
# message, or exercised only through a mocked gate.
#
# Four of them come from one observation. Every refusal test above is built on
# the `no_receipt` shape, and `no_receipt` is the ONE refusal whose detail the
# gate composes without a premise member in front of it. Measured against the
# real gate on 2026-10-23, the other two reachable shapes read:
#
#   uncommitted    "Nothing was deleted: It has uncommitted or untracked
#                   changes that exist nowhere else."
#   cleanup_failed "removing its checkout failed — Nothing was deleted:
#                   <88-char path> is still owned — its checkout is outside
#                   the worktrees folder."
#
# The second one opens with a premise and puts the path 55 characters in. A
# note composed as "the no-receipt sentence, reworded" passes every test above
# and clips both of these on the clamp AC10 is sized to.
# ---------------------------------------------------------------------------


def _any_removal(git_calls: list[list[str]]) -> list[list[str]]:
    """Every removal `git_branch` issued, by any spelling.

    `_forced_removals` matches `["worktree", "remove", "--force"]` exactly, so
    a `_try_free_worktree_lock` that dropped the `--force` — or pruned, or
    moved the tree aside — reads as having removed nothing. AC1 is "removes
    nothing itself", not "does not pass `--force`".
    """
    verbs = {"remove", "prune", "move", "repair"}
    return [call for call in git_calls if any(arg in verbs for arg in call)]


@pytest.fixture(name="receipted_scratchpad")
def receipted_scratchpad_fixture(session, main_checkout: Path, scratchpad: Path) -> Worktree:
    """The scratchpad with every premise the gate asks for: a row and a landing.

    The releasable case, so a test can then break exactly one premise and
    watch the gate refuse for a reason that is not `no_receipt`.
    """
    return _grant_the_gate_its_premises(
        session,
        repo_root=main_checkout,
        tree=scratchpad,
        branch=TICKET_BRANCH,
        external_id="lg-break-receipted",
    )


def _refusal_from_the_real_gate(session, path: Path):
    """What the gate answers for `path`, asked before the dispatch asks it.

    Safe to ask twice: a refusal writes no row and removes nothing, so the
    second answer is the first. This is how a test can assert the gate's own
    sentence reaches the operator *verbatim* without restating it — a restated
    sentence is a test that passes when the two drift apart.
    """
    from loregarden.services.checkout_release import release_checkout

    return release_checkout(session, str(path))


def test_the_produced_first_line_is_itself_inside_the_teaser_budget(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """AC10, measured on the message instead of on a constant.

    The design asserts `PREMISE_VERDICT in first` and `len(PREMISE_VERDICT) <=
    80`. Both hold for a first line reading `"Nothing was deleted; the branch
    is still held by another checkout. See /very/long/path for what was
    kept."` — the second clause is past the ellipsis in `TicketAttention` and
    past the clamp in the toast, which is the whole defect -1167 and -1208
    closed. So the subject is the length of the line the code produced, and
    whether it is one sentence.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    first = str(raised.value).splitlines()[0]
    assert len(first) <= FIRST_LINE_BUDGET, (
        f"the first line is {len(first)} characters, past the {FIRST_LINE_BUDGET} "
        f"the clamp allows: {first!r}"
    )
    assert first.strip() == PREMISE_VERDICT, (
        "one sentence carrying the verdict, and nothing trailing it (AC10)"
    )


def test_the_error_first_line_is_itself_inside_the_teaser_budget(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """AC10 + AC12(b), the same measurement on the other verdict."""
    from loregarden.services import git_branch as git_branch_mod

    with (
        mock.patch.object(
            git_branch_mod, "release_checkout", side_effect=RuntimeError("the gate exploded")
        ),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    first = str(raised.value).splitlines()[0]
    assert len(first) <= FIRST_LINE_BUDGET, f"{len(first)} characters: {first!r}"
    assert first.strip() == ERROR_VERDICT


def test_git_branch_issues_no_removal_of_any_spelling(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """AC1/AC5, behaviourally rather than by grepping the source.

    The census in `test_worktree_gate_chokepoint.py` and the one above both
    read `git_branch.py` as text, and text is what an implementer edits. A
    `run_git(["worktree", "remove", locked_at])` with no `--force` satisfies
    `'"worktree", "remove"' not in source`? No — but it does satisfy
    `_forced_removals`, and a `shutil.rmtree` satisfies both of this module's
    behavioural assertions. So: no removal argv at all, and no tree deleted
    out from under the gate.
    """
    import shutil

    removed: list[str] = []
    with (
        mock.patch.object(shutil, "rmtree", side_effect=lambda *a, **k: removed.append(str(a))),
        pytest.raises(ValueError),
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert removed == [], f"`git_branch` deleted a tree directly: {removed}"


def test_a_removal_argv_of_any_spelling_is_a_failure(
    main_checkout,
    execution_root,
    scratchpad,
    unreceipted_scratchpad,
    held_ticket,
    git_calls,
    session,
):
    """The other half of the one above: the argv census, widened past `--force`."""
    with pytest.raises(ValueError):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    assert _any_removal(git_calls) == [], (
        "`git_branch` issues no removal, prune, move or repair of its own (AC1)"
    )
    assert scratchpad.is_dir()


def test_a_refusal_the_gate_words_differently_still_leads_with_the_verdict(
    main_checkout, execution_root, scratchpad, receipted_scratchpad, held_ticket, session
):
    """AC3 + AC10 on the `uncommitted` shape — the likeliest real one.

    A tree with a row and a landing, and an agent mid-stage in it. This is the
    refusal an operator actually meets: the control plane knows whose tree it
    is, and there is unpushed work in it. Its sentence is NOT the no-receipt
    one, so a note that reworded that single sentence rather than composing
    around whatever the gate said passes every refusal test in the design and
    fails here.
    """
    (scratchpad / "in-flight.txt").write_text("an agent was mid-stage here\n", encoding="utf-8")
    expected = _refusal_from_the_real_gate(session, scratchpad)
    assert expected.reason == "uncommitted", f"fixture: got {expected.reason!r}"

    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert message.splitlines()[0].strip() == PREMISE_VERDICT
    assert expected.detail in message, "the gate's own sentence, verbatim, on a later line (AC3)"
    assert scratchpad.is_dir()
    assert (scratchpad / "in-flight.txt").read_text(encoding="utf-8") == (
        "an agent was mid-stage here\n"
    )
    session.refresh(receipted_scratchpad)
    assert receipted_scratchpad.state == WorktreeState.ACTIVE.value, (
        "a refusal settles nothing: the row still speaks for a checkout that is still there"
    )


def test_an_ignored_file_nothing_regenerates_keeps_the_tree_and_is_named(
    main_checkout, execution_root, scratchpad, receipted_scratchpad, held_ticket, session
):
    """The premise `git status` cannot see, through this path.

    An `.env` is invisible to every shape check and is exactly what somebody
    keeps in a worktree and nowhere else. `git worktree remove --force` took
    it without a word. The gate names it, so the note has to carry that name:
    it is the only way the operator learns which file is holding the branch.
    """
    (scratchpad / ".gitignore").write_text(".env\n", encoding="utf-8")
    _git(["add", "-A"], cwd=scratchpad)
    _git(["commit", "-m", "ignore .env"], cwd=scratchpad)
    secret = scratchpad / ".env"
    secret.write_text("TOKEN=keep-me\n", encoding="utf-8")
    expected = _refusal_from_the_real_gate(session, scratchpad)
    assert ".env" in expected.detail, f"fixture: got {expected.detail!r}"

    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert message.splitlines()[0].strip() == PREMISE_VERDICT
    assert expected.detail in message
    assert secret.read_text(encoding="utf-8") == "TOKEN=keep-me\n"


def test_a_refusal_whose_detail_already_opens_with_a_path_is_still_readable(
    tmp_path, main_checkout, execution_root, held_ticket, session
):
    """AC10's worst input, which is a real one.

    The `cleanup_failed` refusal reads `"removing its checkout failed —
    Nothing was deleted: <path> is still owned — its checkout is outside the
    worktrees folder."` Measured 2026-10-23: 55 characters before the path,
    and live paths are 87–88. Composed as "the gate's detail, with a heading"
    the operator reads a premise and a path and never reaches the remedy.
    """
    outside = tmp_path / "elsewhere" / "stale"
    outside.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", TICKET_BRANCH, str(outside)], cwd=main_checkout)
    row = _grant_the_gate_its_premises(
        session,
        repo_root=main_checkout,
        tree=outside,
        branch=TICKET_BRANCH,
        external_id="lg-break-outside",
        outside_the_base=True,
    )
    expected = _refusal_from_the_real_gate(session, outside)
    assert expected.reason == "cleanup_failed", f"fixture: got {expected.reason!r}"

    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    first = message.splitlines()[0]
    assert first.strip() == PREMISE_VERDICT
    assert len(first) <= FIRST_LINE_BUDGET
    assert expected.detail in message
    assert outside.is_dir()
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE.value


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("the gate exploded"),
        OSError("Errno 5: the filesystem would not say"),
        ValueError("the gate was handed something it could not read"),
    ],
    ids=["runtime", "os", "value"],
)
def test_every_kind_of_gate_failure_is_reported_as_one(
    main_checkout,
    execution_root,
    scratchpad,
    unreceipted_scratchpad,
    held_ticket,
    session,
    caplog,
    failure,
):
    """AC12(b), against a handler narrowed to one exception type.

    `except RuntimeError` passes the design's single ERROR test and lets an
    `OperationalError` — which R1 names as the live failure mode if this lands
    before 1026's migration — escape as itself, past the composed note and
    past the log line. The `ValueError` case is the nastier one: it is the type
    `ensure_ticket_branch` raises on purpose, so an unreported one reads to
    every caller as a refusal that was decided rather than a check that died.
    """
    from loregarden.services import git_branch as git_branch_mod

    with (
        mock.patch.object(git_branch_mod, "release_checkout", side_effect=failure),
        caplog.at_level(logging.WARNING),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert message.splitlines()[0].strip() == ERROR_VERDICT
    assert str(failure) in message, "the failure's own words reach the operator (AC12b)"
    assert "has no worktrees row" not in message
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
    assert scratchpad.is_dir()


def test_two_identical_dispatches_refuse_identically(
    main_checkout, execution_root, scratchpad, unreceipted_scratchpad, held_ticket, session
):
    """Determinism, and the retry an operator actually performs.

    The documented remedy ends "then retry the run", so the second answer is
    read as often as the first. A refusal that removed nothing has nothing to
    make the second pass differ — unless it wrote a row, mutated one, or
    cached a decision, each of which is a silent state change on a path whose
    contract is that it changes nothing.
    """
    notes = []
    for _ in range(2):
        with pytest.raises(ValueError) as raised:
            ensure_ticket_branch(execution_root, held_ticket, session=session)
        notes.append(str(raised.value))

    assert notes[0] == notes[1], "the second refusal is not the first"
    assert scratchpad.is_dir()
    for path, content in unreceipted_scratchpad.items():
        assert Path(path).read_text(encoding="utf-8") == content
    assert session.exec(select(Worktree)).all() == []


def test_a_locking_path_spelled_through_a_symlink_is_refused_not_removed(
    tmp_path, main_checkout, execution_root, scratchpad, held_ticket, session
):
    """R7/A2, as behaviour rather than as an accepted risk in prose.

    The lookup key is path equality with no normalization layer (A1), so a
    `locked_at` spelled through a symlinked parent matches no row and refuses.
    That is the safe direction, and it is the direction a test has to pin:
    "normalize the path until it matches a row" is the plausible follow-up
    change, and it re-opens the hole from the other side.
    """
    from loregarden.models.domain import BranchHolder
    from loregarden.services import git_branch as git_branch_mod

    _grant_the_gate_its_premises(
        session,
        repo_root=main_checkout,
        tree=scratchpad,
        branch=TICKET_BRANCH,
        external_id="lg-break-symlink",
    )
    link = tmp_path / "linked-worktrees"
    link.symlink_to(tmp_path / ".worktrees")
    spelled_through_the_link = link / scratchpad.name

    repaired, note = git_branch_mod._try_free_worktree_lock(
        TICKET_BRANCH,
        str(spelled_through_the_link),
        holder=BranchHolder.LINKED_WORKTREE,
        session=session,
    )

    assert repaired is False
    assert note.strip()
    assert scratchpad.is_dir(), "an unrecognised spelling refuses; it does not remove"


def test_a_released_tree_whose_retry_still_fails_keeps_both_halves_of_the_story(
    main_checkout, execution_root, scratchpad, receipted_scratchpad, held_ticket, session
):
    """The branch no test in the design reaches at all.

    `ensure_ticket_branch` has a third outcome: the gate DELETED the tree, the
    checkout was retried, and the retry failed anyway. It composes
    `f"{repair_note} Retry still failed."` — so whatever the repair note says
    is read there too, and a note written only for the refusal case lands in a
    sentence about a success. The operator needs both halves: the tree was
    released (so they do not go looking for it) and the branch is still not
    checked out (so they know the stage is still blocked).

    The gate is also asked exactly once. A retry loop that re-asks it is how a
    path that refuses correctly on Monday removes something on Tuesday.
    """
    from loregarden.models.domain import RemovalOutcome
    from loregarden.services import git_branch as git_branch_mod
    from loregarden.services.checkout_release import WorktreeRemoval, release_checkout

    asked: list[str] = []

    def release_then_leave_the_branch_held(session_, path):
        asked.append(path)
        released = release_checkout(session_, path)
        assert released.outcome is RemovalOutcome.DELETED, f"fixture: {released.reason!r}"
        # The tree is gone, and something else takes the branch before the
        # retry gets to it — a racing dispatch, a person, a resolver.
        _git(
            ["worktree", "add", "-q", str(Path(path).parent / "racer"), TICKET_BRANCH],
            cwd=main_checkout,
        )
        return WorktreeRemoval(outcome=RemovalOutcome.DELETED, path=path)

    with (
        mock.patch.object(
            git_branch_mod, "release_checkout", side_effect=release_then_leave_the_branch_held
        ),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(execution_root, held_ticket, session=session)

    message = str(raised.value)
    assert len(asked) == 1, f"the gate was asked {len(asked)} times; once is the contract"
    assert "Retry still failed" in message, "the stage is still blocked, and says so"
    assert not scratchpad.exists(), "the release did happen, and the note may not deny it"
    assert "worktree remove" not in message and "--force" not in message, (
        "no path out of this function recommends the destructive command (AC11)"
    )


# ---------------------------------------------------------------------------
# AC6. The ticket's reproduction, moved here from
# `test_open_defect_repros.py` now that it XPASSes: `git init`, a branch, a
# linked worktree on it, an untracked file and an uncommitted edit, and no
# `worktrees` row for any of it. Verified by hand on 2026-10-21 against the
# code this ticket replaced — `git -C main worktree remove --force ../agent`
# exits 0 and the directory, the untracked file and the tracked edit are all
# gone.
#
# It overlaps AC4's test above deliberately: that one drives the fixtures this
# module shares, this one drives the world the defect was reported from, with
# the dispatch standing in a third worktree left on another branch.
# ---------------------------------------------------------------------------


class _LockedCheckout(NamedTuple):
    """The reproduction's world: a dispatch root, a tree holding the branch, and
    the two files the force-removal took without a word."""

    execution_root: Path
    holder: Path
    ticket: Ticket
    files: dict[Path, str]


@pytest.fixture(name="a_linked_worktree_holding_the_branch")
def a_linked_worktree_holding_the_branch_fixture(tmp_path: Path) -> _LockedCheckout:
    """Built in a fixture, not in the test body.

    It was an `xfail(strict=True)` repro, and `xfail` absorbs failures in the
    call phase — so a repro whose setup broke would have reported XFAIL and
    proved nothing. A fixture error is an ERROR, which `xfail` does not
    swallow. Kept that way after the move: the shape is the reason it was
    trustworthy as a repro.
    """
    main = tmp_path / "main"
    main.mkdir()
    _init_repo(main)
    (main / "f").write_text("tracked\n", encoding="utf-8")
    _git(["add", "-A"], cwd=main)
    _git(["commit", "-m", "f"], cwd=main)

    branch = "loregarden/lg-1452"
    holder = tmp_path / ".worktrees" / "agent"
    holder.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", branch, str(holder)], cwd=main)
    untracked = holder / "draft.txt"
    untracked.write_text("an agent was mid-stage here\n", encoding="utf-8")
    tracked = holder / "f"
    tracked.write_text("tracked\nedited, never committed\n", encoding="utf-8")

    # The dispatch runs from the ticket's own worktree, left on another branch,
    # which is where it meets the lock (779).
    execution_root = tmp_path / "ticket-worktree"
    _git(["worktree", "add", "-q", "-b", "other", str(execution_root)], cwd=main)

    return _LockedCheckout(
        execution_root=execution_root,
        holder=holder,
        ticket=Ticket(
            external_id="lg-1452",
            branch=branch,
            work_item_type=WorkItemType.TASK,
            title="Holder",
            workspace_id="w",
        ),
        files={untracked: untracked.read_text(), tracked: tracked.read_text()},
    )


def test_a_checkout_the_gate_has_no_receipt_for_survives_the_self_repair(
    a_linked_worktree_holding_the_branch: _LockedCheckout, session
) -> None:
    """The subject is the surviving bytes, so the control flow is suppressed.

    The refusal raises and the removal did not, and neither is what this test
    measures. Deliberately NOT `pytest.raises`: mutate the call site back to
    `run_git(["worktree", "remove", "--force", locked_at])` and the dispatch
    succeeds, so a `raises` wrapper fails on "DID NOT RAISE" — the test
    reporting a difference in control flow while the destroyed work it exists
    to notice goes unmentioned. AC4 asks for the surviving-file assertion to
    be the one that breaks.
    """
    world = a_linked_worktree_holding_the_branch

    with contextlib.suppress(ValueError):
        ensure_ticket_branch(world.execution_root, world.ticket, session=session)

    missing = [str(path) for path in world.files if not path.exists()]
    assert world.holder.is_dir(), (
        f"the self-repair removed {world.holder}, a checkout no receipt spoke for"
    )
    assert missing == [], f"the self-repair destroyed work nothing can regenerate: {missing}"
    for path, content in world.files.items():
        assert path.read_text() == content, f"{path} did not survive byte-identical"


# ---------------------------------------------------------------------------
# lg-external-harness-1488, test-break. The operator note, on the dispatch path.
#
# AC1 pins WORKSPACE_GONE on `retire_checkout`. TicketAttention and the
# reader never see that object — they see the ValueError
# `_checkout_failure_message` prepends `_kept_note` onto. A gone-workspace
# that is REFUSED at the seam and then composed as `_ERROR_VERDICT` (the
# None-service AttributeError leaking into `_try_free_worktree_lock`'s
# handler) is a decided premise reported as a gate crash.
# ---------------------------------------------------------------------------


_WORKSPACE_GONE_CLAUSE = "the workspace that cut this checkout is no longer on record, so the worktrees folder cannot be named"


def test_a_gone_workspace_on_the_dispatch_path_leads_with_the_premise_verdict(tmp_path, session):
    """AC8/AC9, through `ensure_ticket_branch`. Nested geometry, `_service_for`
    returns None. First line stays 1452's `_PREMISE_VERDICT`; the unique clause
    and `RETIREMENT_ROUTE` follow; `_ERROR_VERDICT` does not appear.

    # CHECKPOINT: same FK-safe `_service_for` stub as AC1. Paths are local, not
    the sibling `execution_root` / `scratchpad` fixtures.
    """
    from loregarden.services import checkout_release as checkout_release_mod
    from loregarden.services.checkout_release import RETIREMENT_ROUTE

    main = tmp_path / "loregarden"
    main.mkdir()
    _init_repo(main)

    dispatch_root = tmp_path / ".worktrees" / "ticket-mine"
    dispatch_root.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-q", "-b", "other", str(dispatch_root)], cwd=main)

    locked = tmp_path / ".worktrees" / "scratchpad"
    branch = "loregarden/gone-ws-lock"
    _git(["worktree", "add", "-q", "-b", branch, str(locked)], cwd=main)

    assert dispatch_root.is_relative_to(main.parent / ".worktrees")
    row = _grant_the_gate_its_premises(
        session,
        repo_root=main,
        tree=locked,
        branch=branch,
        external_id="lg-1488-gone-dispatch",
    )
    workspace = session.get(Workspace, row.workspace_id)
    assert workspace is not None
    assert workspace.repo_path == str(main)

    ticket = Ticket(
        external_id="lg-1488-gone-dispatch-ticket",
        branch=branch,
        work_item_type=WorkItemType.TASK,
        title="Gone workspace dispatch",
        workspace_id="w",
    )

    with (
        mock.patch.object(checkout_release_mod, "_service_for", return_value=None, create=True),
        pytest.raises(ValueError) as raised,
    ):
        ensure_ticket_branch(dispatch_root, ticket, session=session)

    message = str(raised.value)
    first = message.splitlines()[0]
    assert first.strip() == PREMISE_VERDICT
    assert len(first) <= FIRST_LINE_BUDGET
    assert _WORKSPACE_GONE_CLAUSE in message
    assert RETIREMENT_ROUTE in message
    assert ERROR_VERDICT not in message
    assert "outside the worktrees folder" not in message
    assert locked.is_dir()
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE.value
    assert row.unresolved_reason
    assert _WORKSPACE_GONE_CLAUSE in row.unresolved_reason
