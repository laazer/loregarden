"""Releasing a directory: the rename IS the claim, and a failure keeps the resource.

lg-external-harness-1026, R6/R7. Two defects live in the old spelling
(`worktree_service.cleanup_worktree`), and this module is where both die:

1. **Check-then-act.** It asked `worktree_path.exists()`, then removed. Between
   the question and the act anything can happen, and the answer to the question
   is what licensed the act. The replacement never asks: it `os.replace`s the
   live path to an unguessable name under a sibling park root, and *the
   rename's own failure is the check*. One syscall, no window.

2. **A failure reported as an outcome.** It answered `False` for "the path is
   outside the base", for "`worktree remove` raised", and for "`rmtree` raised"
   alike, and a `False` read as "not removed" while the directory was in fact
   half gone. The replacement raises, and the raise carries the original error,
   what was not cleaned, and where it was parked.

A note the spec measured and this module must not contradict: under
`server/.venv` (3.11.15) `Path.is_dir()` and `.exists()` already **raise**
`PermissionError` on EACCES — `pathlib._IGNORED_ERRNOS == (2, 20, 9, 62)`. No
test here may assert they swallow it. What `directory_state` buys is the
*stated* contract (ENOENT and ENOTDIR are the only absences) and the one place
to convert it, so a sweep loop is not crashed by a stat nobody thought about.
"""

from __future__ import annotations

import dataclasses
import enum
import errno
import json
import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import TicketState, Workspace, Worktree, WorktreeState
from loregarden.services import (
    checkout_release,
    directory_release,
    worktree_lifecycle,
    worktree_service,
)
from loregarden.services.checkout_release import release_checkout
from loregarden.services.directory_release import (
    DirectoryOutsideBase,
    DirectoryReleaseFailed,
    DirectoryState,
    ReleaseDisposition,
    directory_state,
    release_directory,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import (
    REMOVAL_REFUSED_OUTSIDE_BASE,
    KeepReason,
    RemovalFailure,
)
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import (
    git,
    grant_receipt,
    make_repo,
    make_run,
    make_ticket_on_id_branch,
)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="base")
def base_fixture(repo: Path) -> Path:
    """`<repo_parent>/.worktrees`, the directory a release must stay inside."""
    base = repo.parent / ".worktrees"
    base.mkdir(exist_ok=True)
    return base


@pytest.fixture(name="park_root")
def park_root_fixture(repo: Path) -> Path:
    """`<repo_parent>/.worktrees-releasing`, a *sibling* of the base.

    A sibling under the same parent, so `os.replace` is a same-device rename.
    A park root on another filesystem answers EXDEV and is not atomic, which
    would put the check-then-act window straight back.
    """
    return repo.parent / ".worktrees-releasing"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    """A session on a throwaway database, for the arms driven through the seam.

    AC5 asks for the row and the payload as well as the exception, and both of
    those only exist on the other side of `retire_worktree` — so three of the
    tests below need a real `worktrees` row rather than a path alone.
    """
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo: Path):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="checkout")
def checkout_fixture(repo: Path, base: Path) -> Path:
    """A real `git worktree` under the base, holding a committed file."""
    path = base / "tree-one"
    git(repo, "worktree", "add", "-q", "-b", "feature/one", str(path))
    (path / "work.txt").write_text("committed work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    return path


# ---------------------------------------------------------------------------
# AC8 — directory_state: a permission or I/O error is never absence
# ---------------------------------------------------------------------------


def test_a_directory_that_is_there_and_one_that_is_not(base: Path):
    there = base / "present"
    there.mkdir()

    assert directory_state(there) is DirectoryState.PRESENT
    assert directory_state(base / "never-existed") is DirectoryState.ABSENT


def test_a_path_whose_parent_is_a_file_is_absent_not_an_error(base: Path):
    """ENOTDIR. `<a-file>/child` cannot exist, which is an answer, not a fault."""
    afile = base / "a-file"
    afile.write_text("not a directory\n")

    assert directory_state(afile / "child") is DirectoryState.ABSENT


def test_a_plain_file_is_not_a_present_directory(base: Path):
    afile = base / "a-file"
    afile.write_text("not a directory\n")

    assert directory_state(afile) is DirectoryState.ABSENT


def test_a_stat_the_kernel_refuses_raises_and_never_reads_as_absence(base: Path):
    """The failure the whole helper exists for.

    `EACCES` is the kernel saying "I will not tell you", and every bool-shaped
    helper in this tree turned that into "there is nothing here" — one step
    short of a delete. It raises, with the errno intact, so the decision site
    can record uncertainty instead of acting on it.
    """
    locked = base / "locked"
    locked.mkdir()
    inner = locked / "inner"
    inner.mkdir()
    locked.chmod(0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root ignores the mode bits this case is about")
        with pytest.raises(PermissionError) as caught:
            directory_state(inner)
        assert caught.value.errno == errno.EACCES
    finally:
        locked.chmod(stat.S_IRWXU)


def test_every_errno_that_is_not_an_absence_is_raised(base: Path):
    """Pinned as a rule rather than one example: the helper has exactly two
    absences (ENOENT, ENOTDIR) and no third answer. A later edit that adds a
    "well, EIO probably means gone too" arm fails here."""
    target = base / "whatever"
    target.mkdir()
    for code in (errno.EACCES, errno.EIO, errno.ELOOP, errno.EBADF, errno.ENAMETOOLONG):
        with mock.patch("os.stat", side_effect=OSError(code, os.strerror(code))):
            with pytest.raises(OSError) as caught:
                directory_state(target)
            assert caught.value.errno == code


def test_the_answer_is_an_enum_and_not_a_bool(base: Path):
    """`directory_state` must not be usable as a predicate.

    A bool brings back exactly the collapse this replaces: `if not state:` is
    the line that deleted on an unreadable stat.
    """
    there = base / "present"
    there.mkdir()
    assert directory_state(there) is not True
    assert directory_state(base / "nope") is not False
    assert isinstance(directory_state(there), DirectoryState)
    assert {member.name for member in DirectoryState} == {"PRESENT", "ABSENT"}


# ---------------------------------------------------------------------------
# AC9 — rename, then delete, with no check-then-act window
# ---------------------------------------------------------------------------


def test_a_release_removes_the_checkout_and_leaves_nothing_parked(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    release_directory(checkout, inside=base, park_root=park_root)

    assert not checkout.exists()
    leftovers = list(park_root.iterdir()) if park_root.exists() else []
    assert leftovers == [], f"a successful release parks nothing: {leftovers}"


def test_the_live_path_is_never_stat_ed_before_the_rename(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """The window itself, asserted.

    `os.replace` either moves the directory or raises; asking first adds nothing
    and licenses the act on an answer that may already be stale. So no
    `exists`/`is_dir`/`stat` may be aimed at the live path before the rename.
    """
    seen: list[str] = []
    real_replace = os.replace
    renamed: list[str] = []

    def record_replace(src, dst, **kwargs):
        renamed.append(str(src))
        return real_replace(src, dst, **kwargs)

    real_stat = os.stat

    def record_stat(path, *args, **kwargs):
        if not renamed:
            seen.append(str(path))
        return real_stat(path, *args, **kwargs)

    with mock.patch("os.replace", side_effect=record_replace):
        with mock.patch("os.stat", side_effect=record_stat):
            release_directory(checkout, inside=base, park_root=park_root)

    assert renamed and renamed[0] == str(checkout.resolve())
    assert str(checkout) not in seen and str(checkout.resolve()) not in seen, (
        f"the live path was probed before the rename: {seen}"
    )


def test_git_worktree_remove_is_never_run(repo: Path, base: Path, park_root: Path, checkout: Path):
    """`worktree remove` is the old spelling and it is check-then-act in git's
    own words: it inspects the tree and declines or forces. The rename is the
    claim; the prune afterwards is bookkeeping."""
    calls: list[list[str]] = []
    from loregarden.services import directory_release

    real_run_git = directory_release.run_git

    def record(args, **kwargs):
        calls.append(list(args))
        return real_run_git(args, **kwargs)

    with mock.patch.object(directory_release, "run_git", side_effect=record):
        release_directory(checkout, inside=base, park_root=park_root)

    assert calls, "the prune must still run"
    for args in calls:
        assert args[:2] != ["worktree", "remove"], f"worktree remove was run: {args}"
    assert ["worktree", "prune"] in [args[:2] for args in calls]


def test_a_worktree_can_be_added_back_at_the_same_path_afterwards(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """What the prune is for. Without it git still holds metadata for the path
    and `worktree add` refuses it as "already registered"."""
    release_directory(checkout, inside=base, park_root=park_root)

    git(repo, "worktree", "add", "-q", "-b", "feature/again", str(checkout))

    assert checkout.is_dir()


def test_the_parked_name_is_unguessable_and_differs_between_two_releases(
    repo: Path, base: Path, park_root: Path
):
    """The park name must not be derivable from the worktree id or its path.

    A derivable name is a name an attacker — or a second sweep — can create
    first, and `os.replace` onto an existing directory is a silent overwrite.
    Two releases of the *same* path must land on two different names.
    """
    parked: list[str] = []
    real_replace = os.replace

    def record(src, dst, **kwargs):
        parked.append(str(dst))
        return real_replace(src, dst, **kwargs)

    for round_number in (1, 2):
        path = base / "tree-same-name"
        git(repo, "worktree", "add", "-q", "-b", f"feature/r{round_number}", str(path))
        with mock.patch("os.replace", side_effect=record):
            release_directory(path, inside=base, park_root=park_root)

    assert len(parked) == 2
    assert parked[0] != parked[1]
    for name in (Path(p).name for p in parked):
        assert "tree-same-name" not in name
        assert len(name) >= 16, "a park name short enough to guess is a park name to race"
    for p in parked:
        assert Path(p).parent == park_root.resolve()


def test_a_sibling_sharing_a_string_prefix_with_the_base_is_refused(
    repo: Path, base: Path, park_root: Path
):
    """The containment bug this replaces, in one line.

    `str(path).startswith(str(base))` accepts `.worktrees-old/x` against a base
    of `.worktrees`, because a path is not a string. `Path.is_relative_to` is
    segment-aware and refuses it.
    """
    sibling = repo.parent / ".worktrees-old" / "x"
    sibling.mkdir(parents=True)
    assert str(sibling).startswith(str(base)), "fixture: the old check would have accepted this"

    with pytest.raises(DirectoryOutsideBase) as caught:
        release_directory(sibling, inside=base, park_root=park_root)

    assert sibling.is_dir(), "a refused release deletes nothing"
    assert str(sibling) in str(caught.value)
    assert str(base) in str(caught.value)


def test_containment_is_judged_after_resolving_both_sides(repo: Path, base: Path, park_root: Path):
    """A symlink into the base is not inside the base, and a path reached
    through `..` that *is* inside the base is."""
    outside = repo.parent / "elsewhere"
    outside.mkdir()
    link = base / "looks-inside"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(DirectoryOutsideBase):
        release_directory(link, inside=base, park_root=park_root)
    assert outside.is_dir()

    indirect = base / "real" / ".." / "real"
    (base / "real").mkdir()
    release_directory(indirect, inside=base, park_root=park_root)
    assert not (base / "real").exists()


def test_a_containment_refusal_is_a_directory_release_failure(
    repo: Path, base: Path, park_root: Path
):
    """One `except` at the decision site, not two.

    AC14 makes `retire_worktree` the single place that catches this. Giving
    containment its own unrelated exception type would mean a second handler
    that someone forgets, and an uncaught raise out of a sweep is the
    crash-in-a-loop failure AC11 forbids.
    """
    assert issubclass(DirectoryOutsideBase, DirectoryReleaseFailed)


# ---------------------------------------------------------------------------
# AC10 — a failed cleanup raises with the original failure, and says where
# ---------------------------------------------------------------------------


def test_an_rmtree_that_fails_raises_carrying_the_original_error_and_the_end_state(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """The three `parked_path` assertions this test used to make were REWRITTEN,
    not deleted (lg-external-harness-1413 AC-M).

    They asserted a truthy `parked_path` and the park path inside
    `str(failure)` for a case whose `os.replace` back SUCCEEDED — so they
    pinned a report that named a path which no longer existed, for a checkout
    sitting exactly where its row says it is. The spec no longer wants that:
    the recovered arm reports `RESTORED` and carries no park path, and the
    disposition is what tells a reader which end state this is.
    """
    boom = OSError(errno.EIO, "I/O error")

    with mock.patch("shutil.rmtree", side_effect=boom):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    failure = caught.value
    assert failure.__cause__ is boom, "the original failure is the cause, not a summary of it"
    assert failure.uncleaned_path
    assert failure.disposition is ReleaseDisposition.RESTORED
    assert failure.parked_path == ""
    assert str(park_root) not in str(failure)


def test_a_failed_rmtree_puts_the_directory_back_where_it_was(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """Restored, so the next sweep meets the tree where its row says it is.

    A tree left under the park root is a tree no row points at and no sweeper
    revisits — the orphan this whole ticket exists to stop creating.
    """
    with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    assert checkout.is_dir()
    assert (checkout / "work.txt").read_text() == "committed work\n"
    assert caught.value.uncleaned_path == str(checkout.resolve())


def test_a_restore_that_also_fails_logs_exactly_one_error_naming_the_parked_path(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog
):
    """The worst case, and the one that must be loud.

    The checkout is not where it was, it is not deleted, and only the log knows
    where it went. One ERROR record, naming the exact path, is the whole
    recovery procedure — so it is asserted rather than hoped for.
    """
    parked: list[str] = []
    real_replace = os.replace

    def replace_once_then_fail(src, dst, **kwargs):
        if not parked:
            parked.append(str(dst))
            return real_replace(src, dst, **kwargs)
        raise OSError(errno.EIO, "the restore failed too")

    with caplog.at_level(logging.ERROR):
        with mock.patch("os.replace", side_effect=replace_once_then_fail):
            with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
                with pytest.raises(DirectoryReleaseFailed) as caught:
                    release_directory(checkout, inside=base, park_root=park_root)

    assert caught.value.disposition is ReleaseDisposition.STILL_PARKED
    assert caught.value.parked_path == parked[0]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, f"expected exactly one ERROR record, got {len(errors)}"
    assert parked[0] in errors[0].getMessage()
    assert Path(parked[0]).is_dir(), "the tree really is where the record says it is"


def test_it_never_reports_success_while_still_holding_the_resource(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """The invariant underneath AC10, stated once as a rule.

    For every way the delete can fail, the call raises. There is no arm that
    returns normally with the bytes still on disk somewhere.
    """
    for failure in (
        OSError(errno.EIO, "I/O error"),
        OSError(errno.EACCES, "permission denied"),
        OSError(errno.EBUSY, "device or resource busy"),
    ):
        path = base / f"tree-{failure.errno}"
        git(repo, "worktree", "add", "-q", "-b", f"feature/e{failure.errno}", str(path))
        with mock.patch("shutil.rmtree", side_effect=failure):
            with pytest.raises(DirectoryReleaseFailed):
                release_directory(path, inside=base, park_root=park_root)
        assert path.is_dir() or any(park_root.iterdir())


def test_a_park_root_on_another_device_is_refused_rather_than_silently_copied(
    repo: Path, base: Path, checkout: Path, tmp_path: Path
):
    """EXDEV. A cross-device `os.replace` is not a rename and not atomic, so a
    park root that is not a sibling of the base must be rejected at the door
    rather than degraded into a copy-then-delete with a window in it."""
    with mock.patch("os.replace", side_effect=OSError(errno.EXDEV, "cross-device link")):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=tmp_path / "far-away")

    assert checkout.is_dir()
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.EXDEV


# ---------------------------------------------------------------------------
# AC1 / AC5 (lg-external-harness-1413) — the raise reports what is TRUE of the
# bytes, not how far the attempt got
#
# The defect these pin: `_restore` answered `None` whether it put the tree back
# or not, so the raise named a park path that no longer existed for a checkout
# sitting exactly where its row says it is — the COMMON case. And the prune and
# the delete shared one `try`, so a pruned, partly-deleted tree and an
# untouched, still-registered one came out of one handler wearing one sentence.
#
# Every test below asserts on RETURNED VALUES. None asserts on the wording of
# `_restore`'s ERROR record: a mutant that collapses the three dispositions
# leaves that record untouched, so a test reading its prose would pass while
# the defect is live (ticket Constraint 5). Its COUNT and the presence of the
# park path in it are existing behaviour and are still asserted.
# ---------------------------------------------------------------------------


def _prune_exits_non_zero(real_run_git):
    """`git worktree prune` exiting non-zero, every other git call left real.

    A non-zero exit rather than a patched `_prune_metadata`: the raise under
    test is the one `_prune_metadata` builds from git's own exit status, and a
    stubbed helper would prove nothing about that conversion.
    """

    def run(args, **kwargs):
        if list(args[:2]) == ["worktree", "prune"]:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=1,
                stdout="",
                stderr="fatal: could not prune\n",
            )
        return real_run_git(args, **kwargs)

    return run


def _fail_the_second_replace():
    """The claim succeeds; the restore does not. Returns (side_effect, parked)."""
    parked: list[str] = []
    real_replace = os.replace

    def replace_once_then_fail(src, dst, **kwargs):
        if not parked:
            parked.append(str(dst))
            return real_replace(src, dst, **kwargs)
        raise OSError(errno.EIO, "the restore failed too")

    return replace_once_then_fail, parked


def _record_the_claim():
    """Every `os.replace` left real, with the name the claim minted recorded.

    The arms whose restore SUCCEEDS carry no park path and leave nothing under
    the park root, so this is the only honest way for a test to name the path
    those arms must not mention.
    """
    parked: list[str] = []
    real_replace = os.replace

    def replace(src, dst, **kwargs):
        if not parked:
            parked.append(str(dst))
        return real_replace(src, dst, **kwargs)

    return replace, parked


# --- the eight arms that raise, each driven through the real function -------


def _arm_symlink(repo: Path, base: Path, park_root: Path, checkout: Path):
    outside = repo.parent / "elsewhere-for-the-link"
    outside.mkdir(exist_ok=True)
    link = base / "looks-like-a-checkout"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DirectoryReleaseFailed) as caught:
        release_directory(link, inside=base, park_root=park_root)
    return caught.value, ""


def _arm_outside_base(repo: Path, base: Path, park_root: Path, checkout: Path):
    sibling = repo.parent / ".worktrees-old" / "x"
    sibling.mkdir(parents=True, exist_ok=True)
    with pytest.raises(DirectoryReleaseFailed) as caught:
        release_directory(sibling, inside=base, park_root=park_root)
    return caught.value, ""


def _arm_park_root_mkdir(repo: Path, base: Path, park_root: Path, checkout: Path):
    with mock.patch.object(Path, "mkdir", side_effect=OSError(errno.EACCES, "denied")):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)
    return caught.value, ""


def _arm_claiming_replace(repo: Path, base: Path, park_root: Path, checkout: Path):
    with mock.patch("os.replace", side_effect=OSError(errno.EXDEV, "cross-device link")):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)
    return caught.value, ""


def _arm_prune_restore_ok(repo: Path, base: Path, park_root: Path, checkout: Path):
    replace, parked = _record_the_claim()
    with mock.patch.object(
        directory_release, "run_git", side_effect=_prune_exits_non_zero(directory_release.run_git)
    ):
        with mock.patch("os.replace", side_effect=replace):
            with pytest.raises(DirectoryReleaseFailed) as caught:
                release_directory(checkout, inside=base, park_root=park_root)
    return caught.value, parked[0]


def _arm_prune_restore_fails(repo: Path, base: Path, park_root: Path, checkout: Path):
    replace, parked = _fail_the_second_replace()
    with mock.patch.object(
        directory_release, "run_git", side_effect=_prune_exits_non_zero(directory_release.run_git)
    ):
        with mock.patch("os.replace", side_effect=replace):
            with pytest.raises(DirectoryReleaseFailed) as caught:
                release_directory(checkout, inside=base, park_root=park_root)
    return caught.value, parked[0]


def _arm_delete_restore_ok(repo: Path, base: Path, park_root: Path, checkout: Path):
    replace, parked = _record_the_claim()
    with mock.patch("os.replace", side_effect=replace):
        with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
            with pytest.raises(DirectoryReleaseFailed) as caught:
                release_directory(checkout, inside=base, park_root=park_root)
    return caught.value, parked[0]


def _arm_delete_restore_fails(repo: Path, base: Path, park_root: Path, checkout: Path):
    replace, parked = _fail_the_second_replace()
    with mock.patch("os.replace", side_effect=replace):
        with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
            with pytest.raises(DirectoryReleaseFailed) as caught:
                release_directory(checkout, inside=base, park_root=park_root)
    return caught.value, parked[0]


#: Every arm of `release_directory` that raises, and the disposition each must
#: report. Kept as one table because the IFF invariant (AC1.5) is a statement
#: about the WHOLE set: a per-arm test cannot say "and no other arm leaks a
#: park path", which is the mutant this is here to kill.
RAISING_ARMS = {
    "symlink refusal": (_arm_symlink, "INTACT"),
    "outside the base": (_arm_outside_base, "INTACT"),
    "park-root mkdir failed": (_arm_park_root_mkdir, "INTACT"),
    "the claiming rename failed": (_arm_claiming_replace, "INTACT"),
    "prune raised, restore worked": (_arm_prune_restore_ok, "INTACT"),
    "prune raised, restore failed too": (_arm_prune_restore_fails, "STILL_PARKED"),
    "delete raised, restore worked": (_arm_delete_restore_ok, "RESTORED"),
    "delete raised, restore failed too": (_arm_delete_restore_fails, "STILL_PARKED"),
}

#: The four that never moved the bytes — before or at the claiming rename.
BEFORE_THE_RENAME = [
    "symlink refusal",
    "outside the base",
    "park-root mkdir failed",
    "the claiming rename failed",
]


# --- AC1.1 ------------------------------------------------------------------


def test_release_disposition_has_exactly_three_members():
    """AC1.1 — three end states, and a fourth member is a fourth sentence.

    Pinned by name AND by value, because the value reaches no API here but is
    the string a later ticket's payload will carry, and renaming it quietly
    would move that vocabulary without anyone choosing to.
    """
    assert len(ReleaseDisposition) == 3
    assert {m.name: m.value for m in ReleaseDisposition} == {
        "INTACT": "intact",
        "RESTORED": "restored",
        "STILL_PARKED": "still-parked",
    }


# --- AC1.2 ------------------------------------------------------------------


def test_the_disposition_is_required_and_has_no_default():
    """AC1.2 — no default, so no construction site can forget to decide.

    The compatibility posture is internal: a default would let a caller keep
    the old shape and report an end state nobody chose, which is the inference
    this whole change deletes. Every construction site must say which of the
    three it means, and a missed one is a `TypeError` at construction rather
    than a wrong answer in a payload.
    """
    with pytest.raises(TypeError):
        DirectoryReleaseFailed("the release failed")
    with pytest.raises(TypeError):
        DirectoryOutsideBase("outside the base")

    failure = DirectoryReleaseFailed(
        "the release failed", disposition=ReleaseDisposition.INTACT, uncleaned_path="/w/x"
    )
    assert failure.disposition is ReleaseDisposition.INTACT
    assert failure.refusal is None, "the seam's vocabulary is still the seam's to stamp"


def test_the_disposition_is_keyword_only():
    """AC1.2 — positionally it is not reachable, so no call site can pass it
    second by accident and have the message become the disposition."""
    with pytest.raises(TypeError):
        DirectoryReleaseFailed("the release failed", ReleaseDisposition.INTACT)


def test_the_class_docstring_states_the_iff_invariant():
    """AC1.2 — the invariant is a docstring plus AC1.5's test, not an assert.

    Asserted on the docstring and not on a validator because there IS no
    validator: the exception must construct on any combination, so that a
    future arm is caught by the parametrized test over real arms rather than
    by a runtime check that would turn a reporting bug into a second crash on
    an already-failing path.
    """
    doc = (DirectoryReleaseFailed.__doc__ or "").lower()
    assert "if and only if" in doc or "iff" in doc
    assert "still_parked" in doc or "still-parked" in doc


def test_the_module_does_not_import_the_seams_vocabulary():
    """AC1.2 — `worktree_service` imports this module, so the reverse is a cycle.

    The disposition is this module's own vocabulary precisely so that it can be
    required here without reaching for the caller's enum.
    """
    source = Path(directory_release.__file__).read_text()
    assert "CleanupRefusal" not in source.replace(
        "`~loregarden.services.worktree_service.CleanupRefusal`", ""
    ).replace("CleanupRefusal` cannot be", "")
    assert not hasattr(directory_release, "CleanupRefusal")


# --- AC1.3 ------------------------------------------------------------------


def test_restore_returns_the_disposition_it_was_given_when_it_put_the_tree_back(
    base: Path, park_root: Path
):
    """AC1.3 — `on_success` is the caller's half of the answer.

    Which end state a recovered failure left behind depends on WHICH syscall
    raised, and `_restore` cannot know that — so the caller names it and
    `_restore` returns it, or overrides it with the one fact only it has.
    """
    park_root.mkdir(parents=True, exist_ok=True)
    for on_success in (ReleaseDisposition.INTACT, ReleaseDisposition.RESTORED):
        parked = park_root / f"parked-{on_success.name.lower()}"
        parked.mkdir()
        target = base / f"back-{on_success.name.lower()}"

        assert directory_release._restore(parked, target, on_success=on_success) is on_success
        assert target.is_dir(), "the tree really was put back"


def test_restore_returns_still_parked_when_it_could_not_put_the_tree_back(
    base: Path, park_root: Path, caplog
):
    """AC1.3 — the one fact only `_restore` has, and the record it already writes.

    Asserted on the RETURN value, and on the record's count and the path inside
    it, never on its wording: the mutant that collapses the dispositions leaves
    the wording untouched.
    """
    park_root.mkdir(parents=True, exist_ok=True)
    parked = park_root / "unmovable"
    parked.mkdir()
    target = base / "never-arrives"

    with caplog.at_level(logging.ERROR):
        with mock.patch("os.replace", side_effect=OSError(errno.EIO, "I/O error")):
            answer = directory_release._restore(
                parked, target, on_success=ReleaseDisposition.RESTORED
            )

    assert answer is ReleaseDisposition.STILL_PARKED
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert str(parked) in errors[0].getMessage()


def test_a_release_that_worked_needs_no_restore_and_logs_no_error(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog
):
    """AC1.3 — the ERROR record is still only ever the failed-restore arm.

    The success path is unchanged by the split, and the split must not have
    turned the prune into a reported failure for a tree it pruned fine.
    """
    with caplog.at_level(logging.ERROR):
        release_directory(checkout, inside=base, park_root=park_root)

    assert not checkout.exists()
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


# --- AC1.4 ------------------------------------------------------------------


@pytest.mark.parametrize("arm", BEFORE_THE_RENAME)
def test_every_refusal_before_the_rename_reports_intact(
    arm: str, repo: Path, base: Path, park_root: Path, checkout: Path
):
    """AC1.4 — nothing moved, so nothing is restored and nothing is parked.

    Driven through the real `release_directory` for each arm rather than by
    constructing the exception, because what is under test is which value the
    PRODUCTION site stamps. Four separate arms rather than one example: each is
    a distinct raise with its own keyword list, and a sweep that updates three
    of them is exactly the miss this parametrization catches.
    """
    drive, expected = RAISING_ARMS[arm]
    assert expected == "INTACT", "fixture: this list is the never-moved-the-bytes set"

    failure, parked = drive(repo, base, park_root, checkout)

    assert parked == "", "fixture: these arms never reach the claiming rename"
    assert failure.disposition is ReleaseDisposition.INTACT
    assert failure.parked_path == ""


def test_the_seams_outside_base_refusal_reports_intact_too(session, workspace, tmp_path):
    """AC1.4 — the fifth site, which a grep of `directory_release.py` misses.

    `worktree_service` raises `DirectoryOutsideBase` itself, on the RECORDED
    spelling, before anything is read off the disk. Nothing was attempted, so
    the bytes and git's registration are both intact — and the seam's own
    `refusal` member is untouched by this change.
    """
    ticket = make_ticket_on_id_branch(session, workspace, external_id="LG-OUT")
    run = make_run(session, workspace, ticket, "run-outside")
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        worktree_path=str(tmp_path / "somewhere-else" / "tree"),
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    service = WorktreeService(session, repo_path=workspace.repo_path)

    with pytest.raises(DirectoryOutsideBase) as caught:
        service.remove_checkout(row)

    assert caught.value.disposition is ReleaseDisposition.INTACT
    assert caught.value.parked_path == ""
    assert caught.value.refusal is CleanupRefusal.OUTSIDE_BASE


# --- AC1.5 ------------------------------------------------------------------


@pytest.mark.parametrize("arm", list(RAISING_ARMS))
def test_parked_path_is_non_empty_iff_the_disposition_is_still_parked(
    arm: str, repo: Path, base: Path, park_root: Path, checkout: Path
):
    """AC1.5 — the invariant, over every arm that raises, both directions.

    The defect was a `parked_path` stamped unconditionally: the only arm for
    which it is TRUE is the one where the `os.replace` back failed. Stated as
    an equivalence rather than as "it is empty here", so a mutant that leaves
    it set on one arm fails, and so does a mutant that clears it on the one
    arm where it is the only record of where the bytes went.
    """
    drive, expected = RAISING_ARMS[arm]

    failure, _parked = drive(repo, base, park_root, checkout)

    assert failure.disposition is ReleaseDisposition[expected]
    assert bool(failure.parked_path) == (failure.disposition is ReleaseDisposition.STILL_PARKED), (
        f"{arm}: disposition {failure.disposition} with parked_path {failure.parked_path!r}"
    )


@pytest.mark.parametrize("arm", list(RAISING_ARMS))
def test_the_park_path_appears_in_the_message_only_when_it_is_still_there(
    arm: str, repo: Path, base: Path, park_root: Path, checkout: Path
):
    """AC5 — "nowhere in the answer" includes `str(failure)`.

    Today's single message interpolates the park root for every failed delete,
    including the ones it recovered from — so an operator reads a path that no
    longer exists, for a checkout sitting where its row says it is. The
    recovered arms must not name it at all.
    """
    drive, _expected = RAISING_ARMS[arm]

    failure, parked = drive(repo, base, park_root, checkout)

    # The PARK PATH — `park_root/<uuid4().hex>` — and not the park root, which
    # the mkdir arm names for a reason that is still true: it is reporting the
    # directory it could not create.
    named = bool(parked) and parked in str(failure)
    assert named == (failure.disposition is ReleaseDisposition.STILL_PARKED), (
        f"{arm}: {str(failure)!r}"
    )


# --- AC1.6 ------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["delete raised, restore worked", "prune raised, restore worked"])
def test_the_park_path_is_never_stat_ed_after_the_restore_returns(
    arm: str, repo: Path, base: Path, park_root: Path, checkout: Path
):
    """AC1.6 — the disposition is a returned value, never a reading of the disk.

    `parked.exists()` is the simplest-looking way to decide this, and it is a
    check-then-act on a path another process may be moving — the defect class
    this module exists to delete. It is also INVISIBLE to a behavioural test:
    after a restore that worked the park path really is gone, so a stat-based
    implementation answers correctly here and wrongly the day the stat loses
    the race.

    So the window is instrumented instead. The moment the RESTORING
    `os.replace` is entered, the module is deciding the disposition; from there
    on nothing may read the park path off the filesystem. `_prune_metadata`
    probing `<parked>/.git` happens before that moment and is the module's own
    business, not a decision about the end state.
    """
    drive, _expected = RAISING_ARMS[arm]
    probes: list[tuple[bool, str]] = []
    deciding = {"yes": False}
    true_replace = os.replace
    real = {
        "stat": os.stat,
        "lstat": os.lstat,
        "exists": os.path.exists,
        "path_exists": Path.exists,
    }

    def watched_replace(src, dst, **kwargs):
        # The claim moves the checkout TO the park root; the restore moves it
        # FROM there. The second direction is the decision window.
        if str(src).startswith(str(park_root)):
            deciding["yes"] = True
        return true_replace(src, dst, **kwargs)

    def probe(name):
        def wrapper(subject, *rest, **kwargs):
            probes.append((deciding["yes"], str(subject)))
            return real[name](subject, *rest, **kwargs)

        return wrapper

    with (
        mock.patch("os.replace", side_effect=watched_replace),
        mock.patch("os.stat", side_effect=probe("stat")),
        mock.patch("os.lstat", side_effect=probe("lstat")),
        mock.patch("os.path.exists", side_effect=probe("exists")),
        mock.patch.object(Path, "exists", probe("path_exists")),
    ):
        failure, parked = drive(repo, base, park_root, checkout)

    assert deciding["yes"], "fixture: this arm must actually reach the restoring rename"
    assert failure.disposition is not ReleaseDisposition.STILL_PARKED
    late = [path for seen, path in probes if seen and path.startswith(parked)]
    assert late == [], f"the park path was probed to decide the disposition: {late}"


# ---------------------------------------------------------------------------
# AC5 — pinned on a real filesystem, and driven through the seam so the row
# and the payload are covered too
# ---------------------------------------------------------------------------


def _owned_checkout(session, workspace, repo: Path, external_id: str) -> tuple[Worktree, Path]:
    """A real checkout with a real receipt: every premise of removal holding.

    Built the honest way — a real `git worktree`, a real commit, and the
    branch really merged into `main` — because the subject below is the
    RELEASE, and a fixture that faked the receipt would be gated before it got
    there.
    """
    ticket = make_ticket_on_id_branch(session, workspace, external_id=external_id)
    run = make_run(session, workspace, ticket, f"run-{external_id}")
    path = resolve_execution_root(session, run, ticket, workspace)
    (path / "work.txt").write_text("committed work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    grant_receipt(session, ticket, Path(workspace.repo_path))
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    return row, path


def _release_through_the_seam(session, path: Path):
    """Drive the real endpoint path, keeping the exception and the Retirement.

    Nothing is stubbed: both patches are pass-throughs over the real functions,
    there only so the test can read the two values the public answer does not
    hand back. The answer itself — `WorktreeRemoval`, and the row it wrote — is
    the real one.
    """
    kept: dict[str, object] = {}
    real_release = worktree_service.release_directory
    real_retire = checkout_release.retire_worktree

    def capture_release(*args, **kwargs):
        try:
            return real_release(*args, **kwargs)
        except DirectoryReleaseFailed as failure:
            kept["failure"] = failure
            raise

    def capture_retire(session_, service, row):
        kept["outcome"] = real_retire(session_, service, row)
        return kept["outcome"]

    with (
        mock.patch.object(worktree_service, "release_directory", side_effect=capture_release),
        mock.patch.object(checkout_release, "retire_worktree", side_effect=capture_retire),
    ):
        removal = release_checkout(session, str(path))
    return removal, kept


# ---------------------------------------------------------------------------
# lg-external-harness-1414 — operator sentences keyed on ReleaseDisposition
# ---------------------------------------------------------------------------

#: U+2014. AC7/AC13/STILL_PARKED copy is settled; do not paraphrase or reopen.
_EM_DASH = "\u2014"
_CLEANUP_FAILED = RemovalFailure.CLEANUP_FAILED.value
_UNREADABLE_CLAUSE = "the filesystem would not say whether it is there"
#: A member ReleaseDisposition does not have. Stands in for the fourth sentence
#: somebody adds, without editing the production enum to find out (AC3).
_LATER_DISPOSITION = enum.Enum("LaterReleaseDisposition", {"ORPHANED": "orphaned"}).ORPHANED


def _owned_name(row: Worktree) -> str:
    """`{path}` in the operator sentence: worktree_path, or the row id."""
    return row.worktree_path or row.id


def _ac7_restored(path: str) -> str:
    """AC7 — RESTORED, verbatim. No `{clause}` slot."""
    return (
        f"The directory is still at {path} and is still owned, but part of it may be gone "
        f"and git no longer lists it as a worktree {_EM_DASH} delete it again, or remove "
        f"the directory by hand."
    )


def _ac13_intact_remove_raised(path: str) -> str:
    """AC13 — INTACT + REMOVE_RAISED, verbatim."""
    return f"Nothing was deleted: {path} is still owned {_EM_DASH} {_CLEANUP_FAILED}."


def _still_parked_sentence(path: str, parked_path: str) -> str:
    """Today's STILL_PARKED sentence; parked_path interpolates as a name."""
    return (
        f"Nothing was deleted: {path} is still owned, and its checkout is parked at "
        f"{parked_path} {_EM_DASH} {_CLEANUP_FAILED}."
    )


def _prefixed_payload(detail: str) -> str:
    """checkout_release._as_removal :113 — refusal.value, then the gate sentence."""
    return f"{_CLEANUP_FAILED} {_EM_DASH} {detail}"


def _assert_remove_raised_row(session, row, outcome, removal, *, parked: str, detail: str):
    """AC2 + AC3 oracles on Retirement, the worktrees row, and the payload.

    Verbatim detail belongs on Retirement.detail and unresolved_reason.
    payload['detail'] is prefix + remainder, never equality with AC7.
    """
    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.cause is CleanupRefusal.REMOVE_RAISED
    assert outcome.parked_path == parked
    assert outcome.detail == detail
    assert "disposition" not in {f.name for f in dataclasses.fields(type(outcome))}
    session.refresh(row)
    assert row.parked_at == parked
    assert row.unresolved_reason == detail
    payload = removal.as_payload(row.branch)
    assert payload["parked_path"] == parked
    assert payload["detail"] == _prefixed_payload(detail)
    return payload


def _assert_not_the_restored_sentence(detail: str, park_root: Path) -> None:
    """AC13/AC14 — INTACT (and None) must not claim a restore that did not happen."""
    assert "part of it may be gone" not in detail
    assert "git no longer lists" not in detail
    assert "parked" not in detail
    assert str(park_root) not in detail
    assert "Open " not in detail
    assert "land the branch" not in detail


def test_a_failed_rmtree_whose_restore_succeeded_reports_restored_and_no_parked_path(
    session, workspace, repo: Path, park_root: Path
):
    """AC5.1 — (i) the common case, which IS the defect.

    `_prune_metadata` runs for real and succeeds; `shutil.rmtree` is made to
    raise; the `os.replace` back is left alone, so it succeeds. The bytes are
    back where the row says they are — possibly partly deleted, since the
    delete got part way — git's registration has been pruned, and there is no
    park path, because there is nothing parked.

    Today this arm answers with the park path the rename minted, which by the
    time anybody reads it does not exist. Driven through the seam as well, so
    the three places that carry the string — the `Retirement`, the `worktrees`
    row and the removal payload — are all covered in the one exercise.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-RESTORED")

    with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
        removal, kept = _release_through_the_seam(session, path)

    failure = kept["failure"]
    assert failure.disposition is ReleaseDisposition.RESTORED
    assert failure.parked_path == ""
    assert str(park_root) not in str(failure)
    assert failure.uncleaned_path == str(path.resolve())

    # The bytes, at the path the row names.
    assert path.is_dir()
    assert (path / "work.txt").read_text() == "committed work\n"

    # AC7 — the operator sentence, keyed on RESTORED, not on empty parked_path.
    outcome = kept["outcome"]
    detail = _ac7_restored(_owned_name(row))
    payload = _assert_remove_raised_row(session, row, outcome, removal, parked="", detail=detail)
    assert str(park_root) not in json.dumps(payload)
    assert not detail.startswith("Nothing was deleted")
    assert _CLEANUP_FAILED not in detail
    assert "parked" not in detail
    assert str(park_root) not in detail
    assert "Open " not in detail
    assert "land the branch" not in detail
    assert payload["detail"].count(_CLEANUP_FAILED) == 1


def test_a_failed_rmtree_whose_restore_also_failed_reports_still_parked_with_the_path(
    session, workspace, repo: Path, caplog
):
    """AC5.2 — (ii) the worst case, and it must stay loud.

    The `os.replace` back raises too, so the bytes are under the park root and
    that string is the only thing that knows where. It is carried in all three
    places, and the single ERROR record still fires exactly once with the path
    in it — asserted on the COUNT and on the path, never on the wording, so
    that the mutant which collapses the dispositions cannot be caught here by
    accident and reported as caught.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-PARKED")
    replace, parked = _fail_the_second_replace()

    with caplog.at_level(logging.ERROR):
        with mock.patch("os.replace", side_effect=replace):
            with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
                removal, kept = _release_through_the_seam(session, path)

    failure = kept["failure"]
    assert failure.disposition is ReleaseDisposition.STILL_PARKED
    assert failure.parked_path == parked[0]
    assert Path(parked[0]).is_dir(), "the bytes really are where the answer says"
    assert parked[0] in str(failure)

    # AC3 — parked_path interpolates into today's sentence as a name, never as
    # a discriminator. Do not assert on the ERROR record's wording: that
    # mutant-collapsing surface survives every disposition-collapsing change.
    outcome = kept["outcome"]
    detail = _still_parked_sentence(_owned_name(row), parked[0])
    payload = _assert_remove_raised_row(
        session, row, outcome, removal, parked=parked[0], detail=detail
    )
    assert parked[0] in payload["detail"]
    assert _CLEANUP_FAILED in detail

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert parked[0] in errors[0].getMessage()


def test_a_failed_prune_whose_restore_succeeded_reports_intact_and_never_deletes(
    session, workspace, repo: Path, park_root: Path
):
    """AC5.3 — (iii) the arm the shared `try` hid.

    `git worktree prune` exits non-zero, so `_prune_metadata` raises — and
    `shutil.rmtree` therefore NEVER RUNS. Nothing was deleted and nothing was
    pruned: the bytes are intact and git still lists the tree. That is
    `INTACT`, not `RESTORED`, and the two are only distinguishable because the
    prune and the delete have separate `try` blocks.

    This is the test that kills the mutant putting them back in one block:
    under that mutant this arm reports `RESTORED`.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-INTACT")
    deletes = mock.MagicMock(wraps=shutil.rmtree)

    with mock.patch.object(
        directory_release, "run_git", side_effect=_prune_exits_non_zero(directory_release.run_git)
    ):
        with mock.patch("shutil.rmtree", deletes):
            removal, kept = _release_through_the_seam(session, path)

    failure = kept["failure"]
    assert failure.disposition is ReleaseDisposition.INTACT
    assert deletes.call_count == 0, "the prune raised, so the delete must never have run"

    # Nothing anywhere names a park path. AC13 — same emptiness as RESTORED,
    # different sentence: nothing was deleted, git still lists it.
    assert failure.parked_path == ""
    assert str(park_root) not in str(failure)
    outcome = kept["outcome"]
    detail = _ac13_intact_remove_raised(_owned_name(row))
    payload = _assert_remove_raised_row(session, row, outcome, removal, parked="", detail=detail)
    assert str(park_root) not in json.dumps(payload)
    _assert_not_the_restored_sentence(detail, park_root)
    assert payload["detail"].count(_CLEANUP_FAILED) == 2

    # The bytes, intact at the path the row names.
    assert (path / "work.txt").read_text() == "committed work\n"

    # And git's registration, intact: the prune never applied, so the tree is
    # still listed and still usable. This is the half that `RESTORED` would be
    # false about, and it is the reason the two members are not one.
    listed = git(repo, "worktree", "list").stdout
    assert str(path.resolve()) in listed
    assert subprocess.run(["git", "status"], cwd=path, capture_output=True).returncode == 0


def test_a_park_root_mkdir_failure_reports_intact_with_the_remove_raised_sentence(
    session, workspace, repo: Path, park_root: Path
):
    """AC13 — park-root mkdir: INTACT, parked_path="", REMOVE_RAISED.

    Reuses `_arm_park_root_mkdir`'s Path.mkdir patch around the seam, not a
    copy of that function and not `_failing_service`. Same emptiness as
    RESTORED; the sentence must be AC13, not AC7.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-MKDIR")

    with mock.patch.object(Path, "mkdir", side_effect=OSError(errno.EACCES, "denied")):
        removal, kept = _release_through_the_seam(session, path)

    failure = kept["failure"]
    assert failure.disposition is ReleaseDisposition.INTACT
    assert failure.parked_path == ""
    detail = _ac13_intact_remove_raised(_owned_name(row))
    payload = _assert_remove_raised_row(
        session, row, kept["outcome"], removal, parked="", detail=detail
    )
    _assert_not_the_restored_sentence(detail, park_root)
    assert payload["detail"].count(_CLEANUP_FAILED) == 2
    assert path.is_dir()


def test_a_claiming_replace_failure_reports_intact_with_the_remove_raised_sentence(
    session, workspace, repo: Path, park_root: Path
):
    """AC13 — claiming os.replace: INTACT, parked_path="", REMOVE_RAISED.

    Reuses `_arm_claiming_replace`'s os.replace patch around the seam. A
    mutant that keys AC7 on `parked_path == ""` fails here, on the prune
    arm, and on mkdir — three empty INTACT arms, not one example.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-CLAIM")

    with mock.patch("os.replace", side_effect=OSError(errno.EXDEV, "cross-device link")):
        removal, kept = _release_through_the_seam(session, path)

    failure = kept["failure"]
    assert failure.disposition is ReleaseDisposition.INTACT
    assert failure.parked_path == ""
    detail = _ac13_intact_remove_raised(_owned_name(row))
    payload = _assert_remove_raised_row(
        session, row, kept["outcome"], removal, parked="", detail=detail
    )
    _assert_not_the_restored_sentence(detail, park_root)
    assert payload["detail"].count(_CLEANUP_FAILED) == 2
    assert path.is_dir()


@pytest.mark.parametrize(
    "disposition",
    [ReleaseDisposition.RESTORED, ReleaseDisposition.INTACT],
    ids=["RESTORED", "INTACT"],
)
def test_an_empty_parked_path_on_restored_or_intact_clears_a_stale_parked_at(
    disposition: ReleaseDisposition, session, workspace, repo: Path, park_root: Path
):
    """AC2 — empty parked_path CLEARS a previously-set worktrees.parked_at.

    Production already assigns `worktree.parked_at = outcome.parked_path` at
    `_record_refusal:519`. This seeds a stale path, drives an INTACT or
    RESTORED raise through the seam, and asserts the column is `""`. Both
    dispositions: a clearer that only fires on one of them is the miss.
    Independent variable is ReleaseDisposition, not bool(parked_path).
    """
    row, path = _owned_checkout(session, workspace, repo, f"LG-STALE-{disposition.name}")
    stale = str(park_root / "stale-parked-that-is-gone")
    row.parked_at = stale
    session.add(row)
    session.commit()

    if disposition is ReleaseDisposition.RESTORED:
        with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
            removal, kept = _release_through_the_seam(session, path)
    else:
        with mock.patch.object(
            directory_release,
            "run_git",
            side_effect=_prune_exits_non_zero(directory_release.run_git),
        ):
            removal, kept = _release_through_the_seam(session, path)

    failure = kept["failure"]
    assert failure.disposition is disposition
    assert failure.parked_path == ""
    session.refresh(row)
    assert row.parked_at == ""
    assert kept["outcome"].parked_path == ""
    assert removal.as_payload(row.branch)["parked_path"] == ""


def test_an_unreadable_refusal_with_empty_parked_path_is_not_the_restored_sentence(
    session, workspace, repo: Path, park_root: Path
):
    """AC3 — disposition is None (premises, `_unreadable`); emptiness is not RESTORED.

    `_observed_state` raising OSError is the real None path: `_unreadable`
    calls `_refused(..., parked_path="")` with no disposition. The INTACT
    template keeps the clause it was given. A mutant that infers RESTORED
    from `parked_path == ""` rewrites this as AC7.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-UNREADABLE")

    with mock.patch.object(
        worktree_lifecycle, "_observed_state", side_effect=OSError(errno.EACCES, "denied")
    ):
        removal, kept = _release_through_the_seam(session, path)

    assert "failure" not in kept
    outcome = kept["outcome"]
    named = _owned_name(row)
    detail = f"Nothing was deleted: {named} is still owned {_EM_DASH} {_UNREADABLE_CLAUSE}."
    assert outcome.refusal is KeepReason.UNREADABLE
    assert outcome.parked_path == ""
    assert outcome.detail == detail
    session.refresh(row)
    assert row.parked_at == ""
    assert row.unresolved_reason == detail
    _assert_not_the_restored_sentence(detail, park_root)
    assert removal.as_payload(row.branch)["parked_path"] == ""


def test_an_unmapped_release_disposition_fails_loudly_instead_of_inheriting_intact(
    session, workspace, repo: Path
):
    """AC3 — a later ReleaseDisposition member has no sentence.

    No silent INTACT fallback. The raise is injected at `release_directory`
    with a member the production enum does not have, then driven through
    `retire_worktree` so `_remove` must pass `failed.disposition` into the
    mapping. Today's two-state `if parked_path:` swallows it as INTACT.
    """
    _row, path = _owned_checkout(session, workspace, repo, "LG-LATER")
    failed = DirectoryReleaseFailed(
        "a later disposition has no sentence",
        disposition=_LATER_DISPOSITION,
        uncleaned_path=str(path),
        parked_path="",
    )
    failed.refusal = CleanupRefusal.REMOVE_RAISED

    def raise_later(*_args, **_kwargs):
        raise failed

    with mock.patch.object(worktree_service, "release_directory", side_effect=raise_later):
        with pytest.raises((KeyError, LookupError, ValueError)):
            _release_through_the_seam(session, path)


def _inject_release_failed(
    session,
    repo: Path,
    path: Path,
    *,
    disposition: object,
    parked_path: str = "",
    exc_type: type[DirectoryReleaseFailed] = DirectoryReleaseFailed,
):
    """Raise a constructed DirectoryReleaseFailed from `release_directory`.

    The independent variable is the exception's `disposition` (and the
    parked_path it carries), never `bool(parked_path)` inferred by the fixture.
    `remove_checkout` still stamps refusal from the exception type.
    """
    failed = exc_type(
        "injected release failure",
        disposition=disposition,
        uncleaned_path=str(path),
        parked_path=parked_path,
    )

    def raise_it(*_args, **_kwargs):
        raise failed

    with mock.patch.object(worktree_service, "release_directory", side_effect=raise_it):
        return _release_through_the_seam(session, path)


@pytest.mark.parametrize(
    ("disposition", "parked"),
    [
        (ReleaseDisposition.RESTORED, "/injected/parked-that-is-not-a-discriminator"),
        (ReleaseDisposition.INTACT, "/injected/parked-that-is-not-a-discriminator"),
        (ReleaseDisposition.STILL_PARKED, ""),
    ],
    ids=["RESTORED-with-a-parked-name", "INTACT-with-a-parked-name", "STILL_PARKED-with-empty"],
)
def test_the_operator_sentence_follows_disposition_not_whether_parked_path_is_set(
    disposition: ReleaseDisposition, parked: str, session, workspace, repo: Path, park_root: Path
):
    """AC3 — parked_path is a name, never the discriminator.

    # CHECKPOINT: DirectoryReleaseFailed constructs on any combination. A leftover
    `if parked_path:` after a RESTORED special-case passes every IFF-satisfying
    arm the designer pinned. These three contradict the IFF on purpose: non-empty
    parked_path must not force the parked sentence, and empty must not forbid it.
    Wording only — parked_at still follows `failed.parked_path` at :519.
    """
    row, path = _owned_checkout(session, workspace, repo, f"LG-DISC-{disposition.name}")
    named = _owned_name(row)

    removal, kept = _inject_release_failed(
        session, repo, path, disposition=disposition, parked_path=parked
    )

    failure = kept["failure"]
    assert failure.disposition is disposition
    outcome = kept["outcome"]
    if disposition is ReleaseDisposition.RESTORED:
        detail = _ac7_restored(named)
        assert _CLEANUP_FAILED not in detail
        assert "parked" not in detail
        assert parked not in detail
        assert str(park_root) not in detail
    elif disposition is ReleaseDisposition.INTACT:
        detail = _ac13_intact_remove_raised(named)
        _assert_not_the_restored_sentence(detail, park_root)
        assert parked not in detail
    else:
        detail = _still_parked_sentence(named, parked)
        assert "and its checkout is parked at" in detail
        assert _CLEANUP_FAILED in detail
        assert not detail.startswith("The directory is still at")
    assert outcome.detail == detail
    assert outcome.cause is CleanupRefusal.REMOVE_RAISED
    session.refresh(row)
    assert row.unresolved_reason == detail
    payload = removal.as_payload(row.branch)
    assert payload["detail"] == _prefixed_payload(detail)


def test_an_unmapped_release_disposition_fails_loudly_even_when_parked_path_is_set(
    session, workspace, repo: Path
):
    """AC3 — leftover `if parked_path:` swallows an unknown member as the parked sentence.

    The empty-path unmapped test is not enough: today's two-state switch only
    inherits INTACT when parked_path is empty. A mapping that still branches on
    `if parked_path:` first would raise on the empty arm and silently format
    the parked template here.
    """
    _row, path = _owned_checkout(session, workspace, repo, "LG-LATER-PARKED")

    with pytest.raises((KeyError, LookupError, ValueError)):
        _inject_release_failed(
            session,
            repo,
            path,
            disposition=_LATER_DISPOSITION,
            parked_path="/injected/parked-for-an-unknown-member",
        )


def test_an_outside_base_intact_refusal_keeps_its_own_clause_not_the_restored_sentence(
    session, workspace, repo: Path, park_root: Path
):
    """A3 — OUTSIDE_BASE keeps today's clause-appending INTACT template.

    AC13 verbatim is the three REMOVE_RAISED INTACT arms only. A mutant that
    hardcodes AC13 (or AC7) for every empty parked_path would pass those three
    and rewrite this one. Driven through the seam without `_failing_service`.
    """
    row, path = _owned_checkout(session, workspace, repo, "LG-OUTSIDE")
    named = _owned_name(row)
    detail = (
        f"Nothing was deleted: {named} is still owned {_EM_DASH} {REMOVAL_REFUSED_OUTSIDE_BASE}."
    )

    removal, kept = _inject_release_failed(
        session,
        repo,
        path,
        disposition=ReleaseDisposition.INTACT,
        exc_type=DirectoryOutsideBase,
    )

    failure = kept["failure"]
    assert failure.disposition is ReleaseDisposition.INTACT
    assert failure.parked_path == ""
    outcome = kept["outcome"]
    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.cause is CleanupRefusal.OUTSIDE_BASE
    assert outcome.detail == detail
    session.refresh(row)
    assert row.unresolved_reason == detail
    assert row.parked_at == ""
    _assert_not_the_restored_sentence(detail, park_root)
    assert _CLEANUP_FAILED not in detail
    payload = removal.as_payload(row.branch)
    assert payload["detail"] == _prefixed_payload(detail)
    assert payload["detail"].count(_CLEANUP_FAILED) == 1
    assert REMOVAL_REFUSED_OUTSIDE_BASE in payload["detail"]
