"""Releasing a directory: the reflexive base, the prune, and a spent ERROR.

lg-external-harness-1026, AC9/AC10. `test_directory_release.py` pins the design
— rename first, no pre-rename stat, an unguessable park name, a raise carrying
the original error and both paths — and `test_directory_release_races.py` covers
the races. Every case here survives both and breaks the invariant anyway.

1. **The base is relative to itself.** `Path(base).is_relative_to(base)` is
   `True`, so a containment check written exactly as AC9 words it accepts the
   base directory as a thing to release — and one call then renames every
   checkout on the machine to an unguessable name and deletes it. The
   sibling-prefix test cannot see this: `.worktrees` is not a sibling of
   `.worktrees`. Nor can it see the arguments swapped round, which accepts the
   base's parent and takes the repository with it.
2. **The prune runs between the rename and the delete**, and both of its
   failure modes are invisible to a suite that only ever runs a prune that
   works: a discarded non-zero exit (the `run_git(check=False)` result this repo
   forbids discarding), and a raise leaking out of a call whose caller is a
   sweep.
3. **"Additionally logs exactly one ERROR" has an *additionally*.** Exactly-one
   is pinned for the worst case; zero is pinned for nothing. A module that logs
   ERROR for every failed `rmtree`, including the ones it recovered from, reads
   identically — and spends on a recoverable condition the one record that means
   "the bytes are somewhere else now".
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services import directory_release
from loregarden.services.directory_release import (
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


@pytest.fixture(name="checkout")
def checkout_fixture(repo: Path, base: Path) -> Path:
    path = base / "tree-one"
    git(repo, "worktree", "add", "-q", "-b", "feature/one", str(path))
    (path / "work.txt").write_text("committed work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    return path


# ---------------------------------------------------------------------------
# AC9 — containment is a strict descendant, not `is_relative_to`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["as-is", "trailing-dot", "through-a-parent"])
def test_the_base_itself_is_not_a_thing_that_can_be_released(
    repo: Path, base: Path, park_root: Path, checkout: Path, spelling: str
):
    """The one-line reading of AC9 that deletes every checkout on the box.

    `is_relative_to` is reflexive. A release whose target *is* the base passes
    the containment check as written, renames the base out from under every
    live agent in one syscall, and deletes it — and because the rename is
    atomic and the name is unguessable, there is nothing to recover by hand.

    Three spellings, because `resolve()` flattens all of them onto the base and
    a check that compares the strings it was handed would catch only the first.
    """
    target = {
        "as-is": base,
        "trailing-dot": base / ".",
        "through-a-parent": base / "tree-one" / "..",
    }[spelling]

    with pytest.raises(DirectoryReleaseFailed):
        release_directory(target, inside=base, park_root=park_root)

    assert base.is_dir(), "the base was released"
    assert checkout.is_dir(), "a release of the base took a live checkout with it"
    assert (checkout / "work.txt").read_text() == "committed work\n"


def test_an_ancestor_of_the_base_is_refused_as_firmly_as_a_sibling(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """`<repo_parent>` holds the repository, the base and the park root.

    A containment check with its arguments the wrong way round —
    `base.is_relative_to(path)` instead of `path.is_relative_to(base)` — passes
    every test in the suite that only ever hands it a descendant, and accepts
    the whole workspace parent here.
    """
    with pytest.raises(DirectoryReleaseFailed):
        release_directory(base.parent, inside=base, park_root=park_root)

    assert base.parent.is_dir()
    assert repo.is_dir(), "the repository itself was released"


# ---------------------------------------------------------------------------
# AC9 — the prune runs between the rename and the delete, and it can fail
# ---------------------------------------------------------------------------


def test_a_prune_git_refuses_is_not_discarded(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """A `run_git(..., check=False)` result nobody reads is this repo's named
    anti-pattern, and here it hides the exact condition the prune exists to
    prevent: git still holding metadata for a path that is now empty, so the
    next `worktree add` there is refused as "already registered".

    Either answer is defensible — raise, or carry it — and what is not
    defensible is a release that reports success having silently left git's
    metadata stale. So the assertion is on the pair: if the call returns
    normally, the path must really be reusable.
    """
    failed = mock.Mock(returncode=1, stdout="", stderr="fatal: could not prune")
    real_run_git = directory_release.run_git

    def prune_fails(args, **kwargs):
        if list(args[:2]) == ["worktree", "prune"]:
            return failed
        return real_run_git(args, **kwargs)

    with mock.patch.object(directory_release, "run_git", side_effect=prune_fails):
        try:
            release_directory(checkout, inside=base, park_root=park_root)
        except DirectoryReleaseFailed:
            return  # carried as a failure: the caller keeps ownership

    readded = git(repo, "worktree", "add", "-q", "-b", "feature/again", str(checkout))
    assert readded.returncode == 0, (
        "the release reported success while git still held the path's metadata"
    )


def test_a_prune_that_raises_does_not_escape_as_itself(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """`retire_worktree` catches `DirectoryReleaseFailed` and nothing else
    (AC14), and a sweep is the caller. A bare `OSError` out of the prune ends
    the pass, and every worktree after this one goes unvisited — the
    crash-in-a-loop failure AC11 forbids, arriving from the one step in
    `release_directory` that is neither the rename nor the delete.
    """
    real_run_git = directory_release.run_git

    def prune_raises(args, **kwargs):
        if list(args[:2]) == ["worktree", "prune"]:
            raise OSError(errno.ENOMEM, "cannot fork")
        return real_run_git(args, **kwargs)

    with mock.patch.object(directory_release, "run_git", side_effect=prune_raises):
        with pytest.raises(DirectoryReleaseFailed):
            release_directory(checkout, inside=base, park_root=park_root)


# ---------------------------------------------------------------------------
# AC10 — "additionally logs exactly one ERROR" has an *additionally*
# ---------------------------------------------------------------------------


def test_a_release_that_worked_logs_no_error_at_all(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog
):
    with caplog.at_level(logging.ERROR):
        release_directory(checkout, inside=base, park_root=park_root)

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_a_failure_that_put_the_tree_back_does_not_log_the_park_path_at_error(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog
):
    """The ERROR record means one thing: the checkout is not where its row says
    it is, and only this string knows where it went. A module that logs it for
    every failed `rmtree` — including the ones it recovered from — spends that
    record on a condition with nothing to recover, and the record stops being
    read before the day it matters.

    The restored case still raises, and the raise carries both paths. That is
    where a recovered failure is reported.
    """
    parked: list[str] = []
    real_replace = os.replace

    def record(src, dst, **kwargs):
        if not parked:
            parked.append(str(dst))
        return real_replace(src, dst, **kwargs)

    with caplog.at_level(logging.ERROR):
        with mock.patch("os.replace", side_effect=record):
            with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
                with pytest.raises(DirectoryReleaseFailed) as caught:
                    release_directory(checkout, inside=base, park_root=park_root)

    assert checkout.is_dir(), "fixture: this is the case where the restore worked"
    assert caught.value.parked_path == parked[0]
    spurious = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and parked[0] in r.getMessage()
    ]
    assert spurious == [], (
        f"the park path was logged at ERROR after a successful restore: {spurious}"
    )
