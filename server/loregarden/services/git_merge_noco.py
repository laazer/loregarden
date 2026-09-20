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


def merge_without_checkout(
    repo_root: Path, *, target: str, source: str, subject: str
) -> MergeOutcome:
    """Merge ``source`` into ``target`` (both local branch names) and move ``target``."""
    source_sha = rev(repo_root, f"refs/heads/{source}")
    target_sha = rev(repo_root, f"refs/heads/{target}")
    if not source_sha:
        return MergeOutcome(ok=False, detail=f"branch {source!r} does not exist")
    if not target_sha:
        return MergeOutcome(ok=False, detail=f"branch {target!r} does not exist")
    if is_ancestor(repo_root, source_sha, target_sha):
        return MergeOutcome(ok=True, sha=source_sha, already_contained=True)

    tree, conflicts, words = _merge_tree(repo_root, target, source)
    if conflicts:
        return MergeOutcome(ok=False, detail=words, conflicted_files=conflicts)
    if not tree:
        return MergeOutcome(ok=False, detail=words)

    committed = _git(
        repo_root, "commit-tree", tree, "-p", target_sha, "-p", source_sha, "-m", subject
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
