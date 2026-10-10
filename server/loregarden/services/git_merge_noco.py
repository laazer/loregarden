"""Merge one branch into another with no working tree involved.

``merge-tree --write-tree`` computes the result, ``commit-tree`` makes the
merge commit, and a compare-and-swap ``update-ref`` moves the target. Nothing
is checked out, so it works while the primary checkout and any number of
worktrees hold other branches, and two callers racing on one target cannot
overwrite each other — the second sees the ref move and reports it.

Used to land a ticket on its integration branch (768) and to bring an
integration branch up to the base branch before a ticket is cut from it
(770). Requires git ≥ 2.38 for ``--write-tree``; the host runs 2.51.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.services.git_subprocess import run_git


@dataclass(frozen=True)
class MergeOutcome:
    ok: bool
    #: The merge commit, or the source tip when nothing needed merging.
    sha: str = ""
    #: True when the source was already contained and nothing was written.
    already_contained: bool = False
    detail: str = ""
    conflicted_files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def conflicted(self) -> bool:
        return bool(self.conflicted_files)


@dataclass(frozen=True)
class BranchTips:
    """Where some local branches pointed, read in one `for-each-ref` at one moment.

    Consumed by the step that moves one of them (`merge_tips`), never kept past
    it: the compare-and-swap that step ends with is what makes a reading taken
    a moment earlier safe to act on — a ref that moved since is refused, not
    overwritten (lg-build-verification-847).
    """

    shas: Mapping[str, str]
    #: git's own words when it could not list the refs at all. Every branch then
    #: reads as missing, which is what `rev` answered on the same failure.
    error: str = ""

    def of(self, branch: str) -> str:
        """The commit `branch` names, or "" when it does not exist."""
        return self.shas.get(branch, "")


def read_branch_tips(repo_root: Path, *branches: str) -> BranchTips:
    """The tips of `branches`, in one git call instead of one per branch.

    `for-each-ref` matches a pattern by whole path components, so
    `refs/heads/integration` would also list `refs/heads/integration/x`; only
    exact names are kept. A branch that does not exist is simply absent.
    """
    wanted = {f"refs/heads/{branch}": branch for branch in branches}
    result = _git(repo_root, "for-each-ref", "--format=%(refname) %(objectname)", *wanted)
    if result.returncode != 0:
        return BranchTips(
            shas={}, error=(result.stderr or result.stdout or "git for-each-ref failed").strip()
        )
    shas: dict[str, str] = {}
    for line in result.stdout.splitlines():
        refname, _, sha = line.rpartition(" ")
        if refname in wanted:
            shas[wanted[refname]] = sha
    return BranchTips(shas=shas)


def _git(repo_root: Path, *args: str):
    return run_git(list(args), cwd=str(repo_root), check=False, capture_output=True, text=True)


def rev(repo_root: Path, ref: str) -> str:
    """The commit ``ref`` names, or "" when it names nothing."""
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else ""


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    return _git(repo_root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def signs_commits(repo_root: Path) -> bool:
    """Whether this repository signs its commits (`commit.gpgsign`).

    `git commit` reads that setting; `git commit-tree` does not — it signs
    only with an explicit `-S`. The first eight landings this module made
    were unsigned in a repository whose branch rules require signatures, and
    the integration branch could not be merged (lg-milestone-that-782).
    """
    result = _git(repo_root, "config", "--get", "--type=bool", "commit.gpgsign")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _merge_tree(repo_root: Path, target: str, source: str) -> tuple[str, tuple[str, ...], str]:
    """The merged tree, the conflicted paths (empty when clean), and git's own words.

    Exit 0: the tree on the first line. Exit 1: the tree, then the conflicted
    paths up to a blank line. Higher: git could not merge at all — reported as
    a failure with no tree, never as "no conflicts".
    """
    result = _git(repo_root, "merge-tree", "--write-tree", "--name-only", target, source)
    lines = result.stdout.splitlines()
    if result.returncode == 0:
        return lines[0].strip() if lines else "", (), ""
    if result.returncode == 1 and lines:
        files: list[str] = []
        for line in lines[1:]:
            if not line.strip():
                break
            files.append(line.strip())
        return lines[0].strip(), tuple(files), result.stdout.strip()
    return "", (), (result.stderr or result.stdout or "git merge-tree failed").strip()


def merge_preview(repo_root: Path, target_sha: str, source_sha: str) -> MergeOutcome:
    """Whether ``source_sha`` would merge into ``target_sha``, writing nothing.

    The read-only half of `merge_tips`: same ancestry shortcut, same
    `merge-tree`, no commit and no ref update. ``ok`` with ``already_contained``
    means nothing to merge; ``ok`` alone means a clean merge is waiting.
    """
    if source_sha == target_sha or is_ancestor(repo_root, source_sha, target_sha):
        return MergeOutcome(ok=True, sha=source_sha, already_contained=True)
    tree, conflicts, words = _merge_tree(repo_root, target_sha, source_sha)
    if conflicts:
        return MergeOutcome(ok=False, detail=words, conflicted_files=conflicts)
    if not tree:
        return MergeOutcome(ok=False, detail=words)
    return MergeOutcome(ok=True)


def merge_without_checkout(
    repo_root: Path, *, target: str, source: str, subject: str
) -> MergeOutcome:
    """Merge ``source`` into ``target`` (both local branch names) and move ``target``."""
    return merge_tips(
        repo_root,
        read_branch_tips(repo_root, target, source),
        target=target,
        source=source,
        subject=subject,
    )


def merge_tips(
    repo_root: Path, tips: BranchTips, *, target: str, source: str, subject: str
) -> MergeOutcome:
    """`merge_without_checkout` from tips the caller has just read.

    For a caller that already read both branches to decide whether to merge at
    all, so the merge does not ask git for them again. The final `update-ref`
    compares against ``tips``' target, so a target that moved after the reading
    is reported rather than overwritten.
    """
    source_sha = tips.of(source)
    target_sha = tips.of(target)
    if not source_sha:
        return MergeOutcome(ok=False, detail=tips.error or f"branch {source!r} does not exist")
    if not target_sha:
        return MergeOutcome(ok=False, detail=tips.error or f"branch {target!r} does not exist")
    # A commit is its own ancestor; equal tips need no `merge-base` to say so.
    if source_sha == target_sha or is_ancestor(repo_root, source_sha, target_sha):
        return MergeOutcome(ok=True, sha=source_sha, already_contained=True)

    tree, conflicts, words = _merge_tree(repo_root, target, source)
    if conflicts:
        return MergeOutcome(ok=False, detail=words, conflicted_files=conflicts)
    if not tree:
        return MergeOutcome(ok=False, detail=words)

    sign = ["-S"] if signs_commits(repo_root) else []
    committed = _git(
        repo_root, "commit-tree", tree, *sign, "-p", target_sha, "-p", source_sha, "-m", subject
    )
    if committed.returncode != 0:
        detail = (committed.stderr or committed.stdout or "git commit-tree failed").strip()
        return MergeOutcome(ok=False, detail=detail)
    merge_sha = committed.stdout.strip()

    moved = _git(repo_root, "update-ref", f"refs/heads/{target}", merge_sha, target_sha)
    if moved.returncode != 0:
        # The old value did not match: something else moved the target since
        # we read it. Nothing was written; the caller re-reads and retries.
        detail = (moved.stderr or moved.stdout or "git update-ref refused").strip()
        return MergeOutcome(ok=False, detail=detail)
    return MergeOutcome(ok=True, sha=merge_sha)
