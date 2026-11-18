"""The only module in the symbol map that spawns a process.

Two git reads and one extension lookup. Both git calls go through
`run_git` with an EXPLICIT `cwd=repo_root`: `run_git` defaults `cwd=None`,
meaning the calling process's working directory, and this control plane runs
git from hooks where that directory belongs to another repository. `GIT_DIR`
would override it too, which is why `run_git` scrubs it.

`git ls-files` is read with `-z`. Measured on this machine: the default
output C-quotes any path holding a byte above 0x7f, so a tracked `pkg_Ω.py`
prints as `"pkg_\\316\\251.py"` — a path that exists on no filesystem. A build
that splits the default output on newlines records a parse failure for a file
that parses, filed under a name no caller will ever ask for.

HEAD is derived here and nowhere else in the package: eleven-plus sites in
`loregarden/` spell `rev-parse HEAD` their own way, and inside this package
there is one.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.services.git_subprocess import run_git
from loregarden.services.symbol_map.values import detect

__all__ = ["UnbornHeadError", "detect", "head_sha", "tracked_files"]


class UnbornHeadError(RuntimeError):
    """The checkout has no commit yet, so there is no sha to stamp a map with.

    Refusing is the honest answer. Storing `""` is not: an empty sha compares
    equal to the next empty read, so a map would report itself fresh for a
    tree it has never seen.
    """


def tracked_files(repo_root: Path) -> list[str]:
    """Every path git tracks in *repo_root*, repo-relative, in git's order.

    What git tracks, not what the directory holds: walking the filesystem
    would index `node_modules`, `.venv` and every build artifact, and would
    report symbols for files no commit contains.
    """
    result = run_git(
        ["ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def head_sha(repo_root: Path) -> str:
    """The sha at HEAD in *repo_root*, or `UnbornHeadError` if there is none."""
    result = run_git(
        ["rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise UnbornHeadError(
            f"{repo_root} has no commit at HEAD, so a symbol map built from it "
            f"could not be stamped with the sha it describes: "
            f"{result.stderr.strip() or 'unborn HEAD'}"
        )
    return sha
