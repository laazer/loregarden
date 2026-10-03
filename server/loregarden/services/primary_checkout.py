"""Refuse to work in a primary checkout that holds someone else's uncommitted work.

A ticket run or chat turn falls back to the workspace's shared checkout when
worktrees are off or one cannot be cut, and several operator actions switch that
checkout's branch or rewrite files in it. When the checkout holds an operator's
work-in-progress, all of these used to go ahead: agent writes mixed with WIP,
changed-path attribution and handoffs then described a tree nobody owned, and a
branch switch could carry the WIP onto a ticket branch (lg-workflow-integrity-864,
a redo of 738 on the current tree snapshot).

Two refinements over 738's rule, decided with the operator on 2026-10-03:

- A ticket's own recorded paths are not WIP. With worktrees off, a ticket's
  earlier stage leaves its uncommitted files in the shared checkout; refusing on
  those would park every later stage on its own predecessor's work. Callers pass
  them as `ignoring`.
- Hook installation refuses only when the file it writes is itself dirty
  (`watching`). Workspaces usually sit on main with unrelated WIP that the
  install never touches.

A tree git cannot read is refused (`UNREADABLE`), never treated as clean.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loregarden.models.domain import DirtyCheckoutCause, PrimaryCheckoutUse
from loregarden.services.worktree_snapshot import TreeSnapshot, read_tree

#: How many dirty paths a refusal names before summarising the rest.
_NAMED_PATHS = 8


class DirtyPrimaryCheckoutError(ValueError):
    """The primary checkout holds work this use would mix with or overwrite.

    A ValueError so the API routes that already map one to a 400 keep doing so;
    the executor and the chat runner catch it first, to park or cancel instead
    of failing.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        use: PrimaryCheckoutUse,
        cause: DirtyCheckoutCause,
        dirty_paths: Iterable[str] = (),
    ) -> None:
        self.repo_root = repo_root
        self.use = use
        self.cause = cause
        self.dirty_paths = tuple(sorted(dirty_paths))
        super().__init__(self._describe())

    def _describe(self) -> str:
        if self.cause is DirtyCheckoutCause.UNREADABLE:
            return (
                f"Refusing {self.use.value} in {self.repo_root}: git could not read the "
                "working tree, so it cannot be shown to be free of someone else's work."
            )
        named = ", ".join(self.dirty_paths[:_NAMED_PATHS])
        more = len(self.dirty_paths) - _NAMED_PATHS
        rest = f" (+{more} more)" if more > 0 else ""
        return (
            f"Refusing {self.use.value} in {self.repo_root}: it has uncommitted changes "
            f"this work did not make ({named}{rest}). Commit, stash or discard them, or "
            "let a worktree be cut, before trying again."
        )

    def artifact_content(self, *, stage_key: str) -> dict:
        """An ERROR artifact body in the shape the Errors tab renders."""
        return {
            "message": str(self),
            "run_code": "",
            "agent_id": "",
            "stage_key": stage_key,
            "command": "",
            "dirty_paths": list(self.dirty_paths),
            "cause": self.cause.value,
            "use": self.use.value,
            "repo_root": str(self.repo_root),
        }


def require_clean_checkout(
    repo_root: Path,
    *,
    use: PrimaryCheckoutUse,
    ignoring: Iterable[str] = (),
    watching: Iterable[str] | None = None,
) -> None:
    """Raise unless `repo_root` holds no uncommitted change that matters to `use`.

    `ignoring` are paths the caller owns (a ticket's recorded paths). `watching`,
    when given, narrows the check to those paths only. One `git status`.

    A directory with no repository holds no uncommitted git work to protect, and
    is let through, as `commit_paths` lets it through; a real repository git
    cannot read is refused.
    """
    if not (repo_root / ".git").exists():
        return
    require_clean_tree(read_tree(repo_root), use=use, ignoring=ignoring, watching=watching)


def require_clean_tree(
    tree: TreeSnapshot,
    *,
    use: PrimaryCheckoutUse,
    ignoring: Iterable[str] = (),
    watching: Iterable[str] | None = None,
) -> None:
    """`require_clean_checkout` against a reading the caller already has."""
    repo_root = tree.repo_root
    dirty = tree.dirty_paths
    if dirty is None:
        raise DirtyPrimaryCheckoutError(
            repo_root=repo_root, use=use, cause=DirtyCheckoutCause.UNREADABLE
        )
    foreign = set(dirty) - set(ignoring)
    if watching is not None:
        foreign &= set(watching)
    if foreign:
        raise DirtyPrimaryCheckoutError(
            repo_root=repo_root, use=use, cause=DirtyCheckoutCause.DIRTY, dirty_paths=foreign
        )
