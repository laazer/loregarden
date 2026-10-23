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

import errno
import logging
import os
import stat
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.directory_release import (
    DirectoryOutsideBase,
    DirectoryReleaseFailed,
    DirectoryState,
    directory_state,
    release_directory,
)
from tests.worktree_helpers import git, make_repo


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


def test_an_rmtree_that_fails_raises_carrying_the_original_error_and_both_paths(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    boom = OSError(errno.EIO, "I/O error")

    with mock.patch("shutil.rmtree", side_effect=boom):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    failure = caught.value
    assert failure.__cause__ is boom, "the original failure is the cause, not a summary of it"
    assert failure.uncleaned_path
    assert failure.parked_path
    assert failure.parked_path != failure.uncleaned_path
    assert failure.parked_path in str(failure)


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
