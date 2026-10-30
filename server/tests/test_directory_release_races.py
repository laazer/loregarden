"""The rename IS the claim: what happens when two callers want the same path.

lg-external-harness-1026, AC9/AC10. `tests/test_directory_release.py` proves
the rename happens without a preceding stat, and that a failed delete raises
carrying both paths. What it does not do is put a *second actor* in the window
the old code left open, and that is the whole argument for the design: AC9
says the rename's own failure IS the check, which is a claim about
concurrency, not about syscall economy.

Each test here is aimed at a mutation that survives the sibling module:

* **Catch `OSError` around `os.replace` and return.** Every existing test
  either succeeds at the rename or is checking the rmtree arm. A release whose
  rename loses the race, or whose path vanished a microsecond earlier, must
  raise — a quiet return is "it is gone, I did my job", which is exactly the
  `False` that `cleanup_worktree` used to answer.
* **Fall back to copy-then-delete when the rename cannot work.** That puts the
  check-then-act window straight back and doubles the bytes on disk. The EXDEV
  test next door covers one way in; a park root that cannot be created is the
  other, and nothing covered it.
* **`resolve()` only one side of the containment check.** A symlink under the
  base pointing out of it is the classic way to make a contained-looking path
  destroy something else.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.directory_release import (
    DirectoryOutsideBase,
    DirectoryReleaseFailed,
    release_directory,
)
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="base")
def base_fixture(repo: Path) -> Path:
    base = repo.parent / ".worktrees"
    base.mkdir(exist_ok=True)
    return base


@pytest.fixture(name="park_root")
def park_root_fixture(repo: Path) -> Path:
    return repo.parent / ".worktrees-releasing"


def _checkout(repo: Path, base: Path, name: str) -> Path:
    path = base / name
    git(repo, "worktree", "add", "-q", "-b", f"feature/{name}", str(path))
    (path / "work.txt").write_text(f"committed work in {name}\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    return path


@pytest.fixture(name="checkout")
def checkout_fixture(repo: Path, base: Path) -> Path:
    return _checkout(repo, base, "tree-one")


# ---------------------------------------------------------------------------
# AC9 / AC10 — the window, with a second actor in it
# ---------------------------------------------------------------------------


def test_when_two_releases_race_for_one_path_only_one_can_report_success(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """Both callers believe the directory is theirs; exactly one is right.

    The interleaving is forced rather than hoped for: the first release's
    `os.replace` is intercepted, a *complete* second release of the same path
    runs to completion inside that moment, and only then does the original
    rename go ahead — onto a path that is no longer there.

    This is the scenario the old `exists()`-then-remove could not survive and
    could not detect. The rename is the claim, so the loser must find out by
    failing, not by having asked earlier and been told yes.
    """
    outcomes: list[str] = []
    real_replace = os.replace
    reentered = False

    def race_on_first_rename(src, dst, **kwargs):
        nonlocal reentered
        if not reentered and Path(src) == checkout.resolve():
            reentered = True
            # The other sweeper, all the way through, inside the window.
            release_directory(checkout, inside=base, park_root=park_root)
            outcomes.append("second")
        return real_replace(src, dst, **kwargs)

    with mock.patch("os.replace", side_effect=race_on_first_rename):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    assert reentered, "the race never happened; the test proved nothing"
    assert outcomes == ["second"], "exactly one release may report success"
    assert isinstance(caught.value.__cause__, OSError), (
        "the loser learns from the rename's own failure, not from a prior check"
    )
    assert not checkout.exists()


def test_a_path_that_vanishes_before_the_rename_is_a_failure_and_never_a_quiet_success(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """ENOENT at the rename is the losing half of the race above, isolated.

    The tempting handler is `except FileNotFoundError: return` — "somebody
    already removed it, fine". It is not fine: the caller is `remove_checkout`,
    whose whole job is to say what happened to the bytes, and a quiet return
    makes a row settle on an act this process did not perform and cannot
    describe. `directory_state` is where "it is not there" gets answered, and
    it is answered *before* a release is attempted, not swallowed inside one.
    """
    with mock.patch("os.replace", side_effect=FileNotFoundError(errno.ENOENT, "No such file")):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    assert caught.value.__cause__.errno == errno.ENOENT
    assert caught.value.uncleaned_path == str(checkout.resolve())


def test_a_park_root_that_cannot_be_made_keeps_the_checkout_and_says_so(
    repo: Path, base: Path, checkout: Path
):
    """No park root, no atomic rename — and therefore no release.

    The failure mode this forbids is a degradation: "the park root is not
    usable, so copy the tree aside and delete the original instead". That is
    check-then-act with extra bytes, and it would pass every other test in this
    suite because the directory does end up gone.

    A plain file where the park root belongs is the cheap way to make the
    directory unmakeable without needing a permission the test runner may not
    be able to drop.
    """
    blocked = repo.parent / ".worktrees-releasing"
    blocked.write_text("not a directory\n")
    before = sorted(p.name for p in checkout.iterdir())

    with pytest.raises(DirectoryReleaseFailed):
        release_directory(checkout, inside=base, park_root=blocked)

    assert checkout.is_dir(), "a release that cannot park must not have deleted anything"
    assert sorted(p.name for p in checkout.iterdir()) == before
    assert (checkout / "work.txt").read_text() == "committed work in tree-one\n"
    assert blocked.is_file(), "nothing may be written over the operator's file either"


def test_a_symlink_under_the_base_cannot_be_used_to_release_what_is_outside_it(
    repo: Path, base: Path, park_root: Path, tmp_path: Path
):
    """Containment is judged on the resolved path, on both sides.

    A link sitting inside the base is "inside the base" by every string test
    and by `Path.is_relative_to` on the unresolved path. AC9 says resolve both
    and then compare, and this is the only test that can tell the difference —
    which matters because the thing on the other end of such a link is, by
    construction, something nobody authorised this gate to touch.
    """
    elsewhere = tmp_path / "somebody-elses-work"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("not yours\n")
    link = base / "looks-contained"
    link.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(DirectoryOutsideBase):
        release_directory(link, inside=base, park_root=park_root)

    assert (elsewhere / "precious.txt").read_text() == "not yours\n"
    assert link.is_symlink(), "the link itself is not the gate's to remove either"


def test_a_failed_release_leaves_exactly_one_copy_of_the_tree(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """Restored means *moved back*, not *copied back*.

    A restore written as a copy leaves the parked duplicate behind: a tree no
    row points at, which no sweep revisits, holding a second checkout of the
    same branch. That is the orphan this ticket exists to stop creating, minted
    by the recovery path instead of the failure path.
    """
    with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
        with pytest.raises(DirectoryReleaseFailed):
            release_directory(checkout, inside=base, park_root=park_root)

    assert checkout.is_dir()
    parked = list(park_root.iterdir()) if park_root.exists() else []
    assert parked == [], f"the restore left a duplicate behind: {parked}"


def test_two_releases_in_flight_at_once_never_share_a_parked_name(
    repo: Path, base: Path, park_root: Path
):
    """`os.replace` onto an existing directory is a silent overwrite.

    The sibling module checks that two *sequential* releases of one path get
    different names. Sequential is the easy case — a counter would pass it. The
    case that matters is two releases alive at the same time, where a name
    derived from anything the two share (the path, the pid, the second) would
    collide and one tree would be renamed over the other, destroying it with no
    error anywhere.
    """
    first = _checkout(repo, base, "tree-a")
    second = _checkout(repo, base, "tree-b")
    parked: list[str] = []
    real_replace = os.replace
    real_rmtree = shutil.rmtree
    held: list[Path] = []

    def park_and_hold(src, dst, **kwargs):
        parked.append(str(dst))
        result = real_replace(src, dst, **kwargs)
        held.append(Path(dst))
        return result

    def rmtree_after_the_other_has_parked(path, *args, **kwargs):
        # The first release is suspended between its rename and its delete
        # while the second release parks. Both parked copies exist at once.
        if len(parked) == 1:
            release_directory(second, inside=base, park_root=park_root)
        return real_rmtree(path, *args, **kwargs)

    with mock.patch("os.replace", side_effect=park_and_hold):
        with mock.patch(
            "loregarden.services.directory_release.shutil.rmtree",
            side_effect=rmtree_after_the_other_has_parked,
        ):
            release_directory(first, inside=base, park_root=park_root)

    assert len(parked) == 2, "both releases must have parked"
    assert len(set(parked)) == 2, f"two live releases shared one parked name: {parked}"
    assert not first.exists() and not second.exists()
    assert held and not any(p.exists() for p in held), "every parked copy is deleted in the end"


def test_a_symlink_under_the_base_pointing_at_another_checkout_releases_neither(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """The harmful direction of the same link, which nothing else covered.

    `test_a_symlink_under_the_base_cannot_be_used_to_release_what_is_outside_it`
    aims a link *out* of the base and `remove_checkout`'s lexical arm aims one
    *in*; both pass with the final component followed. This one points inside
    to inside, where following it means containment is judged on another row's
    real checkout and then `os.replace` plus `rmtree` act on it: the link
    survives, the real tree with its uncommitted work is gone, and the caller
    is told the release succeeded. A checkout is a directory git created, so a
    link here is not the thing the gate was asked to release.
    """
    (checkout / "uncommitted.txt").write_text("not committed anywhere\n")
    link = base / "looks-like-its-own-checkout"
    link.symlink_to(checkout, target_is_directory=True)

    with pytest.raises(DirectoryOutsideBase):
        release_directory(link, inside=base, park_root=park_root)

    assert checkout.is_dir(), "the real checkout at the other end must survive"
    assert (checkout / "uncommitted.txt").read_text() == "not committed anywhere\n"
    assert (checkout / "work.txt").read_text() == "committed work in tree-one\n"
    assert link.is_symlink(), "nothing may be unlinked on a refusal either"
