"""Build each throwaway git repo once per process, and copy it per test.

`make_repo`, the ``git_repo`` fixture and the seeded workspace repo each ran five
or six git subprocesses per test — about 1-1.5s apiece under a loaded xdist
run — to produce the same one-commit repository hundreds of times.

Only for a self-contained repository. One that records an absolute path — a
remote, a worktree, an alternates file — would point back at the template once
copied, so those keep building their own.

A copy is followed by ``git update-index --refresh``. The index records each
file's inode and ctime, which a copy changes; without the refresh the next
``git status`` would rewrite ``.git/index``, and a test asserting that a check
leaves the repository untouched would see a write the old repo never had.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

_template_root: Path | None = None


def set_template_root(root: Path) -> None:
    """Where this process keeps its templates. Set once, by a session fixture."""
    global _template_root
    _template_root = root


def from_template(destination: Path, key: str, build: Callable[[Path], None]) -> Path:
    """Put a copy of the ``key`` repo at ``destination``, building the template on first use.

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
        staging.rename(template)
    copy_repo(template, destination)
    return destination


def copy_repo(source: Path, destination: Path) -> None:
    """Copy a repository and refresh its index's stat data for the new files."""
    shutil.copytree(source, destination, symlinks=True)
    subprocess.run(
        ["git", "update-index", "-q", "--refresh"],
        cwd=destination,
        check=True,
        capture_output=True,
    )
