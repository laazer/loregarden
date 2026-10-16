"""Git branch helpers."""

import logging
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import PrimaryCheckoutUse, Ticket, WorkItemType
from loregarden.services.git_branch import (
    default_ticket_branch,
    ensure_ticket_branch,
    resolve_ticket_branch,
    validate_branch_name,
)
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError


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


def test_default_ticket_branch():
    ticket = Ticket(
        external_id="42-my-feature", work_item_type=WorkItemType.TASK, title="x", workspace_id="w"
    )
    assert default_ticket_branch(ticket) == "loregarden/42-my-feature"


def test_default_ticket_branch_uses_milestone_prefix():
    ticket = Ticket(
        external_id="42-my-feature",
        milestone="Q3 Launch",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    assert default_ticket_branch(ticket) == "q3-launch/42-my-feature"


def test_resolve_ticket_branch_prefers_explicit():
    ticket = Ticket(
        external_id="42-my-feature",
        branch="custom/branch",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    assert resolve_ticket_branch(ticket) == "custom/branch"


def test_validate_branch_name_rejects_invalid():
    with pytest.raises(ValueError):
        validate_branch_name("bad branch name")


def test_ensure_ticket_branch_creates_branch(tmp_path, monkeypatch):
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
    branch = ensure_ticket_branch(repo_root, ticket)
    assert branch == "loregarden/99-test-branch-checkout"
    current = _git(["branch", "--show-current"], cwd=repo_root)
    assert current.stdout.strip() == "loregarden/99-test-branch-checkout"
    _git(["checkout", "-"], cwd=repo_root)


def test_ensure_ticket_branch_self_repairs_worktree_lock(tmp_path, monkeypatch):
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
    ensure_ticket_branch(repo_root, ticket)
    _git(["checkout", "main"], cwd=repo_root)
    other = tmp_path / "other-worktree"
    _git(["worktree", "add", str(other), "loregarden/88-worktree-lock"], cwd=repo_root)

    branch = ensure_ticket_branch(repo_root, ticket)
    assert branch == "loregarden/88-worktree-lock"
    current = _git(["branch", "--show-current"], cwd=repo_root)
    assert current.stdout.strip() == "loregarden/88-worktree-lock"
    assert not other.exists()


def test_ensure_ticket_branch_refuses_to_remove_primary_lock(tmp_path, monkeypatch):
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
        ensure_ticket_branch(repo_root, ticket)


def test_ensure_ticket_branch_never_resets_an_existing_branch(tmp_path, monkeypatch):
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
    ensure_ticket_branch(repo_root, ticket)
    (repo_root / "work.txt").write_text("first round\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_root)
    _git(["commit", "-m", "first round"], cwd=repo_root)
    first_round = _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()

    # Something else moves HEAD — another ticket, a chat turn, a person.
    _git(["checkout", "-b", "elsewhere", "main"], cwd=repo_root)

    ensure_ticket_branch(repo_root, ticket)

    assert _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip() == first_round
    assert (repo_root / "work.txt").exists()


def test_ensure_ticket_branch_cuts_a_missing_branch_from_the_start_point(tmp_path, monkeypatch):
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
    ensure_ticket_branch(repo_root, ticket, start_point="main")

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
    """A stale linked worktree holding the ticket's branch — the 2024 case."""
    tree = tmp_path / "scratchpad"
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
    main_holds_the_branch, execution_root, held_ticket, git_calls, caplog
):
    """AC1. The reproduction in the ticket, verbatim.

    `_is_primary_checkout` compared `locked_at` against `repo_root`. Called
    from the ticket's worktree, the operator's main checkout no longer matched,
    so the guard stopped guarding and the removal was aimed at it. git refuses
    to remove a main working tree (exit 128), so nothing was lost — this is
    defence in depth that must defend again.
    """
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket)

    assert "Refusing to remove primary checkout" in caplog.text
    assert _forced_removals(git_calls) == [], (
        "the removal must never be aimed at the repository's main working tree"
    )
    assert _names(str(raised.value), main_holds_the_branch), (
        "the operator has to be told which tree is holding the stage up"
    )


def test_the_refusal_does_not_hand_the_operator_a_command_that_cannot_work(
    main_holds_the_branch, execution_root, held_ticket, git_calls
):
    """AC2/UX-2. `git worktree remove --force <main working tree>` exits 128
    however the operator runs it. The remedy printed has to be one that can
    succeed, and no path may print the one that cannot.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket)

    assert "worktree remove --force" not in str(raised.value)


def test_the_remedy_detaches_when_the_dispatch_had_no_start_point(
    main_holds_the_branch, execution_root, held_ticket, git_calls
):
    """UX-2(b). With nowhere named to send the holder, detaching frees the
    branch without choosing another one on the operator's behalf.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket)

    message = str(raised.value)
    assert "switch --detach" in message
    assert "switch --detach HEAD" not in message, "`--detach` takes no argument here"


def test_the_remedy_names_the_start_point_when_the_dispatch_had_one(
    main_holds_the_branch, execution_root, held_ticket, git_calls
):
    """UX-2(b). A dispatch that knows where the branch was cut from can send the
    holder somewhere, rather than leaving the operator on a detached HEAD.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket, start_point="main")

    assert "switch main" in str(raised.value)
    assert "switch --detach" not in str(raised.value)


def test_a_stale_linked_worktree_is_still_removed_and_the_checkout_retried(
    main_checkout, execution_root, scratchpad, held_ticket, git_calls
):
    """S-9. The guard that stays.

    Without this, "never remove anything" passes every test above. The stale
    scratchpad worktree is the failure the self-repair exists for, and the
    dispatch has to get all the way to the branch.
    """
    assert ensure_ticket_branch(execution_root, held_ticket) == TICKET_BRANCH

    removals = _forced_removals(git_calls)
    assert len(removals) == 1, removals
    assert Path(removals[0][3]).resolve() == scratchpad.resolve()
    assert ["worktree", "prune"] in git_calls, "git's admin data still names the removed tree"
    assert git_calls.index(["worktree", "prune"]) > git_calls.index(removals[0])
    assert _git(["branch", "--show-current"], cwd=execution_root).stdout.strip() == TICKET_BRANCH
    assert not scratchpad.exists()


def test_a_topology_git_cannot_identify_removes_nothing(
    main_checkout, execution_root, scratchpad, held_ticket, git_calls, caplog
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
        ensure_ticket_branch(execution_root, held_ticket)

    assert "Refusing to remove primary checkout" in caplog.text
    assert _forced_removals(git_calls) == []
    message = str(raised.value)
    assert "worktree remove --force" not in message
    assert _names(message, scratchpad), "the operator still needs the path"
    assert "switch --detach" in message, (
        "detaching frees the branch whether or not that tree is the main one"
    )
    assert scratchpad.exists(), "and the tree it could not classify is still there"


def test_the_main_working_tree_is_derived_once_per_call(main_checkout, held_ticket):
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
        ensure_ticket_branch(main_checkout, held_ticket)

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
    main_checkout, execution_root, held_ticket
):
    _dirty_off_the_branch(execution_root)

    with pytest.raises(DirtyPrimaryCheckoutError) as raised:
        ensure_ticket_branch(execution_root, held_ticket)

    assert raised.value.use is PrimaryCheckoutUse.TICKET_WORKTREE_BRANCH_CHECKOUT
    assert raised.value.repo_root == execution_root


def test_a_refused_main_working_tree_keeps_the_shared_checkout_use(main_checkout, held_ticket):
    """The distinction only means something if the old value still happens."""
    _dirty_off_the_branch(main_checkout)

    with pytest.raises(DirtyPrimaryCheckoutError) as raised:
        ensure_ticket_branch(main_checkout, held_ticket)

    assert raised.value.use is PrimaryCheckoutUse.TICKET_BRANCH_CHECKOUT


def test_a_topology_git_cannot_identify_keeps_todays_use(
    main_checkout, execution_root, held_ticket
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
        ensure_ticket_branch(execution_root, held_ticket)

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
    main_checkout, scratchpad, held_ticket, git_calls
):
    """S-9, from the other side of 779.

    Every removal test above is called from the ticket's own worktree, so
    "only remove when the tree I was handed is not the main one" passes them
    all — and silently stops self-repairing the shared-checkout dispatch that
    `_try_free_worktree_lock` was written for. The decision is about
    `locked_at`, never about which tree asked.
    """
    assert ensure_ticket_branch(main_checkout, held_ticket) == TICKET_BRANCH

    removals = _forced_removals(git_calls)
    assert len(removals) == 1, removals
    assert Path(removals[0][3]).resolve() == scratchpad.resolve()
    assert not scratchpad.exists()


def test_the_derivation_is_still_once_when_the_branch_is_locked(
    main_holds_the_branch, execution_root, held_ticket
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
        ensure_ticket_branch(execution_root, held_ticket)

    assert len(reads) == 1, reads


def test_the_derivation_is_still_once_when_the_tree_is_refused(
    main_checkout, execution_root, held_ticket
):
    """S-3 on the refusal path: `require_clean_tree` is reached through the
    same single value, and a second read here would be a second answer.
    """
    _dirty_off_the_branch(execution_root)
    patch, reads = _common_dir_reads()

    with patch, pytest.raises(DirtyPrimaryCheckoutError):
        ensure_ticket_branch(execution_root, held_ticket)

    assert len(reads) == 1, reads


def test_nothing_is_asked_of_git_when_the_path_is_not_a_repository(tmp_path, held_ticket):
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
        ensure_ticket_branch(outside, held_ticket)

    assert reads == [], "the `.git` check answers first, so git is never run"


def test_the_refusal_names_the_holder_and_nothing_else(
    main_holds_the_branch, execution_root, held_ticket, git_calls
):
    """UX-1. One question: which tree is holding this stage up. A message that
    names the ticket's worktree as well — the tree the operator is *not* meant
    to touch — answers it twice, and `.tl-output-title`'s one ellipsised line
    is the surface this lands on.
    """
    with pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket)

    message = str(raised.value)
    assert _names(message, main_holds_the_branch), "the holder, by absolute path"
    assert not _names(message, execution_root), (
        "and not the tree that is merely where the dispatch was standing"
    )


def test_an_unidentified_topology_detaches_even_when_a_start_point_was_given(
    main_checkout, execution_root, scratchpad, held_ticket, git_calls
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
        ensure_ticket_branch(execution_root, held_ticket, start_point="main")

    message = str(raised.value)
    assert "switch --detach" in message
    assert "switch main" not in message


def test_a_linked_worktree_that_will_not_be_removed_keeps_the_remedy_it_has(
    tmp_path, main_checkout, execution_root, held_ticket, git_calls, caplog
):
    """S-5(a), byte for byte, on the path that produces it.

    A locked linked worktree is the one shape where the self-repair runs,
    fails, and the operator is handed the existing `worktree remove --force`
    remedy — which here is a command that *can* succeed (`--force --force`, or
    unlock). Every other 916 test asserts that string is absent, so a fix that
    simply deleted it would read as passing.
    """
    locked = tmp_path / "locked"
    _git(["worktree", "add", "-q", "-b", TICKET_BRANCH, str(locked)], cwd=main_checkout)
    _git(["worktree", "lock", str(locked)], cwd=main_checkout)

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as raised:
        ensure_ticket_branch(execution_root, held_ticket)

    message = str(raised.value)
    assert len(_forced_removals(git_calls)) == 1, "the removal is attempted on a linked worktree"
    assert "worktree remove --force" in message, "the remedy for a worktree is still a removal"
    assert _names(message, locked)
    assert "locked" in message, "and git's own reason for refusing is carried through"
    assert any(record.levelno >= logging.WARNING for record in caplog.records), (
        "a self-repair that could not repair says so; its wording is not pinned here"
    )
    assert locked.exists()


def test_a_checkout_failure_that_is_not_a_lock_still_reports_itself(
    main_checkout, held_ticket, git_calls
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
        ensure_ticket_branch(main_checkout, held_ticket)

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
    target_tree, held_ticket, git_processes
):
    """AC4. Nothing landed since, so there is nothing to check out, nothing to
    stat and nothing to merge — and the one question that establishes it is
    `merge-base --is-ancestor`.
    """
    before = _head(target_tree)
    git_processes.clear()  # only the call under test is counted

    assert (
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH) == TICKET_BRANCH
    ), "the fast path returns the same branch name the slow path returns"

    assert len(_asking(git_processes, "merge-base", "--is-ancestor")) == 1, git_processes
    assert _asking(git_processes, "checkout") == [], git_processes
    assert _asking(git_processes, "status", "--porcelain") == [], git_processes
    assert _asking(git_processes, "merge") == [], git_processes
    assert _asking(git_processes, "rev-parse", "--verify") == [], git_processes
    assert _head(target_tree) == before, "and the tree is exactly where it was"


def test_a_dirty_tree_that_already_has_the_target_is_not_warned_about(
    target_tree, held_ticket, git_processes, caplog
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
            ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH)
            == TICKET_BRANCH
        )

    assert "uncommitted" not in caplog.text, caplog.text
    assert _asking(git_processes, "merge") == [], git_processes
    assert (target_tree / "in-flight.txt").read_text(encoding="utf-8") == (
        "an agent is still writing this\n"
    )


def test_the_merge_still_happens_when_a_sibling_landed_on_the_target(
    main_checkout, target_tree, held_ticket, git_processes
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
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH) == TICKET_BRANCH
    )

    merged = _git(["merge-base", "--is-ancestor", landed, "HEAD"], cwd=target_tree)
    assert merged.returncode == 0, "the landed commit must be reachable from the worktree's HEAD"
    assert (target_tree / "sibling.txt").exists()
    assert _git(["merge-base", "--is-ancestor", own, "HEAD"], cwd=target_tree).returncode == 0, (
        "and the ticket's own commit is not lost in the process"
    )
    assert _asking(git_processes, "merge", "--no-edit") != [], git_processes


def test_a_tree_left_on_another_branch_is_put_back_even_with_nothing_to_merge(
    target_tree, held_ticket, git_processes
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
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH) == TICKET_BRANCH
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


def test_no_start_point_still_goes_the_long_way(target_tree, held_ticket, git_processes):
    """AC4. `start_point=""` is "cut from HEAD, refresh onto nothing", not "the
    target is already reachable". `is_ancestor` answers False for an empty
    argument, so the gate needs no extra branch — this pins that it still does.
    """
    git_processes.clear()  # only the call under test is counted

    assert ensure_ticket_branch(target_tree, held_ticket) == TICKET_BRANCH

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
    assert private == ["services/dependency_readiness.py", "services/handoff_boundary.py"], (
        f"a new private ancestry helper was added: {private}"
    )


def test_the_main_working_tree_is_never_derived_twice_on_the_fast_path(
    target_tree, held_ticket, git_processes
):
    """AC10. `main_working_tree` may move below the fast-path return — its two
    consumers are both unreachable from there — but moving it must not turn it
    into something derived lazily or twice. Two derivations that disagree is
    the defect lg-milestone-that-916 exists for.
    """
    git_processes.clear()  # only the call under test is counted

    ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH)

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
    target_tree, held_ticket, git_processes
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
        ensure_ticket_branch(target_tree, held_ticket, start_point=TARGET_BRANCH) == TICKET_BRANCH
    )

    assert (
        _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_tree).stdout.strip() == TICKET_BRANCH
    ), "a detached tree is owed the fix-up, and the fix-up is only on the slow path"
    assert _head(target_tree) == on_the_branch
    assert _asking(git_processes, "checkout") != [], git_processes
