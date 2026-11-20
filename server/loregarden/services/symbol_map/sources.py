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

`changed_paths` is the delta oracle the incremental refresh runs on
(lg-code-knowledge-284). It diffs a stored sha against the WORKING TREE, not
against another commit: a commit range cannot see uncommitted work, and
uncommitted work is every mid-stage edit this control plane makes. It answers
with a closed three-valued result and never with a bare list, because an empty
changed-set returned by a FAILED diff re-stamps the sha onto a map describing
another tree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain.symbol_map_enums import ChangedPathsOutcome
from loregarden.services.git_subprocess import run_git
from loregarden.services.symbol_map.values import detect
from loregarden.services.worktree_snapshot import TreeSnapshot

logger = logging.getLogger(__name__)

__all__ = [
    "ChangedPaths",
    "ChangedPathsOutcome",
    "UnbornHeadError",
    "changed_paths",
    "detect",
    "head_sha",
    "is_ancestor",
    "tracked_files",
]

#: Stderr fragments git uses when it cannot resolve the base at all. The
#: distinction is load-bearing and is not guessable from the exit code: a bad
#: object and a missing repository both exit 128, and only the first is
#: recoverable by rebuilding. Probed against real git on this machine.
_BASE_UNUSABLE_MARKERS = (
    "bad revision",
    "bad object",
    "unknown revision",
    "ambiguous argument",
    "not a valid object name",
    "invalid object name",
)
#: git's usage exit. A blob sha resolves as an object and is still not
#: something a tree can be diffed against; git exits 129 rather than 128.
#: BASE_UNUSABLE rather than GIT_FAILED because BASE_UNUSABLE recovers (the
#: rebuild re-stamps a usable sha) and GIT_FAILED, which leaves the stored sha
#: alone by design, would fail identically forever.
_GIT_USAGE_EXIT = 129


@dataclass(frozen=True)
class ChangedPaths:
    """Which repo-relative paths differ from a base sha, and which are gone.

    `changed` is only ever empty under `ChangedPathsOutcome.OK`. The two sets
    are disjoint: a path in both would be deleted and re-inserted, and which
    of those won would depend on the order of two loops nobody chose.
    """

    outcome: ChangedPathsOutcome
    changed: frozenset[str]
    deleted: frozenset[str]


def _failed(outcome: ChangedPathsOutcome) -> ChangedPaths:
    return ChangedPaths(outcome=outcome, changed=frozenset(), deleted=frozenset())


def _classify(returncode: int, stderr: str) -> ChangedPathsOutcome:
    lowered = (stderr or "").lower()
    if returncode == _GIT_USAGE_EXIT or any(marker in lowered for marker in _BASE_UNUSABLE_MARKERS):
        return ChangedPathsOutcome.BASE_UNUSABLE
    return ChangedPathsOutcome.GIT_FAILED


def _parse_name_status(stdout: str) -> tuple[set[str], set[str]]:
    """`--name-status -z` is a flat NUL-delimited stream of status, path, ...

    `--no-renames` keeps every record two fields wide, so a rename arrives as
    a `D` and an `A` rather than as the three-field `R100` record a
    three-letter parser reads as one path named `old\tnew`.
    """
    records = [record for record in stdout.split("\0") if record]
    changed: set[str] = set()
    deleted: set[str] = set()
    for index in range(0, len(records) - 1, 2):
        status, path = records[index], records[index + 1]
        if status[:1] == "D":
            deleted.add(path)
        else:
            changed.add(path)
    return changed, deleted


def _untracked(repo_root: Path, tree: TreeSnapshot | None) -> frozenset[str] | None:
    """Untracked paths, from the caller's snapshot or from git directly.

    `TreeSnapshot.dirty_paths` is None when git could not answer — that is
    "unreadable", not "clean", and treating it as an empty set silently drops
    every brand-new module from the delta. None here means the same thing one
    level up: ask git, and if git fails too, the whole answer is GIT_FAILED.

    Entries ending in `/` are directories git refused to descend into — a
    nested checkout is reported as one. Indexing inside one would file another
    repository's sources under this checkout's identity.
    """
    if tree is not None and tree.dirty_paths is not None:
        return frozenset(path for path in tree.dirty_paths if not path.endswith("/"))
    result = run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "could not list untracked files in %s (exit %s): %s",
            repo_root,
            result.returncode,
            (result.stderr or "").strip()[:200],
        )
        return None
    return frozenset(path for path in result.stdout.split("\0") if path and not path.endswith("/"))


def changed_paths(repo_root: Path, base_sha: str, tree: TreeSnapshot | None = None) -> ChangedPaths:
    """Which paths differ between *base_sha* and the working tree of *repo_root*.

    One `git diff --name-status --no-renames -z <base_sha> --`, against the
    working tree rather than against another commit, plus the untracked paths
    the caller's snapshot already holds. Never raises: an OSError from git
    itself is a failure to look, which is `GIT_FAILED` like any other.
    """
    if not base_sha.strip():
        # A partition whose sha was never stamped reads as "". That is not
        # "diff against nothing"; it is a base nothing can be compared to.
        return _failed(ChangedPathsOutcome.BASE_UNUSABLE)
    try:
        result = run_git(
            ["diff", "--name-status", "--no-renames", "-z", base_sha, "--"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            outcome = _classify(result.returncode, result.stderr)
            logger.warning(
                "symbol-map delta against %s in %s failed (exit %s, %s): %s",
                base_sha,
                repo_root,
                result.returncode,
                outcome.value,
                (result.stderr or "").strip()[:200],
            )
            return _failed(outcome)
        changed, deleted = _parse_name_status(result.stdout)
        untracked = _untracked(repo_root, tree)
    except OSError as exc:
        logger.warning("could not read the symbol-map delta in %s: %s", repo_root, exc)
        return _failed(ChangedPathsOutcome.GIT_FAILED)
    if untracked is None:
        return _failed(ChangedPathsOutcome.GIT_FAILED)
    # A path the index records as deleted can still have bytes on disk
    # (`git rm --cached`), so the untracked scan and the diff describe it from
    # two sides. The deletion wins: the two sets must not overlap.
    return ChangedPaths(
        outcome=ChangedPathsOutcome.OK,
        changed=frozenset((changed | untracked) - deleted),
        deleted=frozenset(deleted),
    )


def is_ancestor(repo_root: Path, maybe_ancestor: str, descendant: str) -> bool:
    """Whether *maybe_ancestor* is reachable from *descendant* in *repo_root*.

    False whenever git cannot answer, including when the object is missing:
    the caller is choosing a seed, and declining to seed costs a full build
    while seeding from an unrelated commit costs a wrong map.
    """
    if not maybe_ancestor.strip() or not descendant.strip():
        return False
    try:
        result = run_git(
            ["merge-base", "--is-ancestor", maybe_ancestor, descendant],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        logger.warning("could not test ancestry in %s: %s", repo_root, exc)
        return False
    return result.returncode == 0


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
