"""Build each throwaway git repo once per process, and copy it per test.

`make_repo`, the ``git_repo`` fixture and the seeded workspace repo each ran five
or six git subprocesses per test — about 1-1.5s apiece under a loaded xdist
run — to produce the same one-commit repository hundreds of times.

Only for a self-contained repository. One that records an absolute path — a
remote, a worktree, an alternates file — would point back at the template once
copied, so those keep building their own.

A copy runs no git at all. The index records each file's inode and ctime,
which a copy changes, so each copy used to run ``git update-index --refresh``
(about 90ms on a loaded machine, once per ``client`` test). Instead each
template is settled once (`settle_template`): ``core.checkStat=minimal``
compares only mtime and size, which ``copytree`` preserves, and its tracked
files are backdated so no index entry is racily clean. A copy's next
``git status`` then neither re-hashes nor rewrites ``.git/index``, so a test
asserting that a check leaves the repository untouched sees no write the old
repo never had.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

_template_root: Path | None = None


def set_template_root(root: Path) -> None:
    """Where this process keeps its templates. Set once, by a session fixture."""
    global _template_root
    _template_root = root


def from_template(
    destination: Path,
    key: str,
    build: Callable[[Path], None],
    *,
    repos: Sequence[str] = (".",),
) -> Path:
    """Put a copy of the ``key`` repo at ``destination``, building the template on first use.

    ``repos`` names the working trees inside the template to settle, relative to
    it: the template itself by default, or the clones in a tree that holds
    several (a bare repository has no index and needs none).

    Outside pytest — nothing set a template root — this simply builds in place.
    """
    if _template_root is None:
        build(destination)
        return destination
    template = _template_root / key
    if not template.exists():
        staging = template.with_name(f"{key}.building")
        if staging.exists():
            shutil.rmtree(staging)
        build(staging)
        for relpath in repos:
            settle_template(staging / relpath)
        staging.rename(template)
    copy_repo(template, destination)
    return destination


#: Lock files a git process is holding in the template, never part of the copy.
#: `git commit` ends by running `git maintenance run --auto --detach`, whose
#: child takes `objects/maintenance.lock` after the commit has returned. On a
#: loaded runner it is still alive when the next test copies the template, and
#: copytree lists the lock, then fails to open it once the child has removed it.
_TRANSIENT_GIT_FILES = shutil.ignore_patterns("*.lock")


#: How far `settle_template` backdates tracked files: past the index's own
#: mtime by more than git's whole-second racy-clean window.
_BACKDATE_SECONDS = 10


def settle_template(repo: Path, env: Mapping[str, str] | None = None) -> None:
    """Make ``repo``'s index valid for every byte-for-byte copy of it; see the module docstring."""

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
        )

    git("config", "core.checkStat", "minimal")
    past = time.time() - _BACKDATE_SECONDS
    for relpath in git("ls-files", "-z").stdout.split("\0"):
        path = repo / relpath
        # A submodule entry is a directory, a symlink is stamped rather than
        # followed, and a file the build deleted has nothing to stamp.
        if relpath and os.path.lexists(path) and not path.is_dir():
            os.utime(path, (past, past), follow_symlinks=False)
    git("update-index", "-q", "--really-refresh")


def copy_repo(source: Path, destination: Path) -> None:
    """Copy a settled template (`settle_template`); its index is valid as copied."""
    shutil.copytree(source, destination, symlinks=True, ignore=_TRANSIENT_GIT_FILES)
