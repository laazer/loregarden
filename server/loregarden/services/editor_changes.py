"""The files the open branch or worktree has changed, for the Editor's centre pane.

Compared against the merge-base with the repository's base branch, and taken
from the working tree rather than from commits, so a ticket worktree's
uncommitted edits count. That is what "this ticket's diff" means to someone
looking at the worktree, and the Editor needs no idea of tickets to show it.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain import Workspace
from loregarden.models.domain.enums import EditorChangeStatus
from loregarden.services.artifact_service import git_base_ref
from loregarden.services.file_editor import resolve_editor_root
from loregarden.services.git_subprocess import run_git
from pydantic import BaseModel

#: Files listed before the rest are only counted. A branch that rewrote more
#: than this is better read as a PR than in a sidebar.
MAX_CHANGED_FILES = 200

_STATUS_CODES = {
    "A": EditorChangeStatus.ADDED,
    "M": EditorChangeStatus.MODIFIED,
    "D": EditorChangeStatus.DELETED,
    "T": EditorChangeStatus.MODIFIED,
}


class EditorChange(BaseModel):
    path: str
    status: EditorChangeStatus
    #: None for a binary file, or an untracked one git has no counts for.
    additions: int | None
    deletions: int | None


class EditorChanges(BaseModel):
    branch: str
    base: str
    merge_base: str
    files: list[EditorChange]
    #: Every changed file, including any past ``MAX_CHANGED_FILES``.
    total: int


class EditorChangesError(ValueError):
    """Git could not say what changed: no base branch, or a command failed."""


def _git(root: Path, *args: str) -> str:
    result = run_git(list(args), cwd=root, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise EditorChangesError(f"git {' '.join(args)} failed: {reason}")
    return result.stdout


def _nul_fields(text: str) -> list[str]:
    return [field for field in text.split("\0") if field]


def _numstat(root: Path, merge_base: str) -> dict[str, tuple[int | None, int | None]]:
    counts: dict[str, tuple[int | None, int | None]] = {}
    numstat = _git(root, "diff", "--numstat", "--no-renames", "--relative", "-z", merge_base)
    for record in _nul_fields(numstat):
        added, deleted, path = record.split("\t", 2)
        if added == "-":
            counts[path] = (None, None)
        else:
            counts[path] = (int(added), int(deleted))
    return counts


def _statuses(root: Path, merge_base: str) -> list[tuple[str, EditorChangeStatus]]:
    fields = _nul_fields(
        _git(root, "diff", "--name-status", "--no-renames", "--relative", "-z", merge_base)
    )
    # `-z` writes each record as status NUL path NUL.
    return [
        (path, _STATUS_CODES.get(code[:1], EditorChangeStatus.MODIFIED))
        for code, path in zip(fields[0::2], fields[1::2], strict=True)
    ]


def list_editor_changes(workspace: Workspace, *, context_root: str | None = None) -> EditorChanges:
    root = resolve_editor_root(workspace, context_root)
    base = git_base_ref(root)
    if base is None:
        raise EditorChangesError(
            "No base branch to compare with: this repository has no main or master"
        )
    merge_base = _git(root, "merge-base", "HEAD", base).strip()
    branch = _git(root, "branch", "--show-current").strip() or "(detached)"

    counts = _numstat(root, merge_base)
    changes: list[EditorChange] = []
    for path, status in _statuses(root, merge_base):
        additions, deletions = counts.get(path, (None, None))
        changes.append(
            EditorChange(path=path, status=status, additions=additions, deletions=deletions)
        )
    tracked = {change.path for change in changes}
    changes.extend(
        EditorChange(path=path, status=EditorChangeStatus.UNTRACKED, additions=None, deletions=None)
        for path in _nul_fields(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
        if path not in tracked
    )
    changes.sort(key=lambda change: change.path)
    return EditorChanges(
        branch=branch,
        base=base,
        merge_base=merge_base[:12],
        files=changes[:MAX_CHANGED_FILES],
        total=len(changes),
    )
