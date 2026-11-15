"""The tips a branch was moved away from, kept so the landings they hold stay findable.

`integration_branch_moves` writes them before any move that is not a fast
forward; `base_delivery` reads them. Read-only here, and a leaf, so the reader
needs neither the writer nor anything that imports it.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.services.git_subprocess import run_git

#: Where a tip the branch is reset away from is kept: the base held it whole,
#: so every landing it contains is delivered, and `base_delivery` reads them
#: from here once the branch no longer contains them (lg-milestone-that-2624).
SETTLED_PREFIX = "refs/loregarden/settled/"


def settled_tips(repo_root: Path, branch: str) -> list[str]:
    """The tips ``branch`` was reset away from because the base held them, newest first."""
    listed = run_git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(objectname)",
            f"{SETTLED_PREFIX}{branch}/",
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        raise RuntimeError(f"git for-each-ref failed: {(listed.stderr or listed.stdout).strip()}")
    return listed.stdout.split()
