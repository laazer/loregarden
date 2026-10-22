"""Has a branch's work landed on base? The squash-merge predicate, and a scan that remembers it.

Commit counts compare by SHA, so a branch squashed into one commit on base still
looks "ahead" though its changes landed. :func:`branch_work_has_landed` asks the
content question instead: is every file the branch touched since the merge-base
byte-identical between its tip and base?

That is two git processes per branch, and on macOS a git process costs ~27ms
to start whatever it does. At 214 ahead branches (live repo, 2026-10-21) the
branch triage snapshot spent 4.2s on it, almost all of it process spawns:
batching does not help, because the merge-base walk is itself one process per
branch (2.4s on its own) and `diff-tree --stdin` refuses `--merge-base`.

So :func:`landed_branches` keeps each answer, keyed by the commits it was
computed from, and recomputes only what can have changed:

- same base and tip: the answer is a pure function of the two commits; reuse it.
- base fast-forwarded, tip unchanged: reuse it when (a) none of the new base
  commits is reachable from any scanned tip, so every merge-base is unchanged
  and so is the branch's file list, and (b) no path the new commits changed
  lies under any of the branch's files, so the second diff reads the same
  bytes. Anything else (a rewound base, a tip that moved, a git failure)
  recomputes with the exact predicate.

The deleting caller (`chat_branch_sweep`) calls :func:`branch_work_has_landed`
directly and never reads the cache.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from loregarden.services.git_subprocess import run_git

LANDED_SCAN_MAX_WORKERS = 8
#: A pathspec containing any of these is a glob to git; the reuse test only
#: knows literal prefix matching, so such a branch is always recomputed.
_PATHSPEC_GLOB_CHARS = frozenset("*?[\\")


@dataclass(frozen=True)
class _Verdict:
    landed: bool
    #: The files `base...tip` touched; the pathspecs of the second diff.
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class _ScanMemo:
    base_sha: str
    by_tip: dict[str, _Verdict]


_T = TypeVar("_T")
_R = TypeVar("_R")

_memos: dict[str, _ScanMemo] = {}
_memos_lock = threading.Lock()


def _git(repo_root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return run_git(
        ["-C", str(repo_root), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _check(repo_root: Path, base: str, branch: str) -> _Verdict | None:
    """The predicate. None when git could not answer, which callers read as not landed.

    `base...branch` diffs from the merge-base, so no separate merge-base lookup is needed.
    """
    files_proc = _git(repo_root, "diff", "--name-only", f"{base}...{branch}")
    if files_proc.returncode != 0:
        return None
    changed_files = tuple(line for line in (files_proc.stdout or "").splitlines() if line.strip())
    if not changed_files:
        return _Verdict(landed=True, changed_files=())

    diff_proc = _git(repo_root, "diff", "--quiet", base, branch, "--", *changed_files)
    if diff_proc.returncode not in (0, 1):
        return None
    return _Verdict(landed=diff_proc.returncode == 0, changed_files=changed_files)


def branch_work_has_landed(repo_root: Path, base: str, branch: str) -> bool:
    """Whether every file `branch` touched since its merge-base is identical in `base`.

    The bar the cleanup dialog pre-selects on, and the only one at which
    removing a ref destroys nothing: the branch's content is already in `base`.
    `chat_branch_sweep` deletes on it unattended, so it is always computed
    fresh, never from :func:`landed_branches`'s memo.
    """
    verdict = _check(repo_root, base, branch)
    return verdict is not None and verdict.landed


def _resolve_commits(repo_root: Path, revisions: list[str]) -> dict[str, str] | None:
    """Each revision's commit sha, resolved the way `git diff` resolves it, in one process.

    A revision git cannot resolve to a commit is left out. None when the batch
    itself failed.
    """
    proc = _git(
        repo_root,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype)",
        stdin="".join(f"{rev}^{{commit}}\n" for rev in revisions),
    )
    lines = (proc.stdout or "").splitlines()
    if proc.returncode != 0 or len(lines) != len(revisions):
        return None
    resolved: dict[str, str] = {}
    for rev, line in zip(revisions, lines, strict=True):
        sha, _, kind = line.partition(" ")
        if kind == "commit":
            resolved[rev] = sha
    return resolved


@dataclass(frozen=True)
class _BaseAdvance:
    """What a fast-forward of base from `old` to `new` can have changed."""

    #: Paths that differ between the two base trees.
    changed_paths: frozenset[str]
    #: Tips that reach a commit new to base, so their merge-base may have moved.
    rebased_tips: frozenset[str]


def _tips_reaching(
    repo_root: Path, tips: list[str], old_base: str, new_commits: set[str]
) -> frozenset[str] | None:
    """The tips with any of `new_commits` among their ancestors, from one rev-list.

    A merge-base of (base, tip) can only move when a commit new to base is an
    ancestor of tip. Such a commit is not an ancestor of `old_base`, so the walk
    only needs `tips ^old_base`: listed parents-first, a commit reaches a new
    commit when it is one or a parent inside that set does. A parent outside it
    is an ancestor of `old_base`, and nothing new is an ancestor of that.
    """
    if not new_commits or not tips:
        return frozenset()
    proc = _git(
        repo_root,
        "rev-list",
        "--parents",
        "--topo-order",
        "--reverse",
        "--stdin",
        stdin="".join(f"{line}\n" for line in [*tips, f"^{old_base}"]),
    )
    if proc.returncode != 0:
        return None
    reaches: dict[str, bool] = {}
    for line in (proc.stdout or "").splitlines():
        commit, *parents = line.split()
        reaches[commit] = commit in new_commits or any(reaches.get(p, False) for p in parents)
    return frozenset(tip for tip in tips if reaches.get(tip, False))


def _base_advance(
    repo_root: Path, old_base: str, new_base: str, tips: list[str]
) -> _BaseAdvance | None:
    """None when base did not simply fast-forward (rewound or rewritten), or git failed."""
    range_proc = _git(repo_root, "rev-list", "--left-right", f"{old_base}...{new_base}")
    if range_proc.returncode != 0:
        return None
    sides = (range_proc.stdout or "").split()
    if any(side.startswith("<") for side in sides):
        return None
    rebased_tips = _tips_reaching(repo_root, tips, old_base, {side[1:] for side in sides})
    if rebased_tips is None:
        return None
    delta_proc = _git(
        repo_root, "diff-tree", "-r", "--no-renames", "--name-only", "-z", old_base, new_base
    )
    if delta_proc.returncode != 0:
        return None
    return _BaseAdvance(
        changed_paths=frozenset(path for path in (delta_proc.stdout or "").split("\0") if path),
        rebased_tips=rebased_tips,
    )


def _covered_prefixes(paths: frozenset[str]) -> frozenset[str]:
    """Every path and each of its leading directories, casefolded.

    A literal pathspec `p` matches `d` when `d == p` or `d` is under `p/`, i.e.
    when `p` is in this set. Casefolding only widens the match, which only
    costs a recompute.
    """
    covered: set[str] = set()
    for path in paths:
        parts = path.casefold().split("/")
        covered.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return frozenset(covered)


def _still_holds(verdict: _Verdict, covered: frozenset[str]) -> bool:
    for path in verdict.changed_files:
        if path.startswith(":") or not _PATHSPEC_GLOB_CHARS.isdisjoint(path):
            return False
        if path.casefold() in covered:
            return False
    return True


def _reusable(
    repo_root: Path, memo: _ScanMemo | None, base_sha: str, tips: set[str]
) -> dict[str, _Verdict]:
    if memo is None:
        return {}
    carried = {tip: verdict for tip, verdict in memo.by_tip.items() if tip in tips}
    if memo.base_sha == base_sha or not carried:
        return carried
    advance = _base_advance(repo_root, memo.base_sha, base_sha, sorted(carried))
    if advance is None:
        return {}
    covered = _covered_prefixes(advance.changed_paths)
    return {
        tip: verdict
        for tip, verdict in carried.items()
        if tip not in advance.rebased_tips and _still_holds(verdict, covered)
    }


def landed_branches(repo_root: Path, base: str, branches: list[str]) -> set[str]:
    """The subset of `branches` for which :func:`branch_work_has_landed` holds, memoized."""
    if not branches:
        return set()
    resolved = _resolve_commits(repo_root, [base, *branches]) or {}
    base_sha = resolved.get(base)
    tip_of = {name: resolved[name] for name in branches if name in resolved} if base_sha else {}
    # A name git could not resolve to a commit (or any name, when base did not
    # resolve) is asked as-is and not memoized; the predicate answers it as it always did.
    unresolved = [name for name in branches if name not in tip_of]
    landed = {
        name
        for name, verdict in _map_parallel(
            lambda name: (name, _check(repo_root, base, name)), unresolved
        )
        if verdict is not None and verdict.landed
    }
    if base_sha is None:
        return landed

    key = str(repo_root.resolve())
    with _memos_lock:
        memo = _memos.get(key)
    by_tip = _reusable(repo_root, memo, base_sha, set(tip_of.values()))
    fresh = _map_parallel(
        lambda tip: (tip, _check(repo_root, base_sha, tip)),
        sorted(set(tip_of.values()) - by_tip.keys()),
    )
    by_tip.update((tip, verdict) for tip, verdict in fresh if verdict is not None)
    with _memos_lock:
        _memos[key] = _ScanMemo(base_sha=base_sha, by_tip=by_tip)

    return landed | {name for name, tip in tip_of.items() if tip in by_tip and by_tip[tip].landed}


def _map_parallel(fn: Callable[[_T], _R], items: list[_T]) -> list[_R]:
    if len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(LANDED_SCAN_MAX_WORKERS, len(items))) as pool:
        return list(pool.map(fn, items))


def clear_memo() -> None:
    """Forget every memoized verdict (tests)."""
    with _memos_lock:
        _memos.clear()
