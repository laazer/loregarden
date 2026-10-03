"""Commit what a transition gate's mechanical fixers changed — or say why not.

When a gate fails and its autofix commands clear it, the fixer's edits are
committed onto the ticket's branch. That commit used to be scoped to the paths
the ticket's runs had recorded and to swallow any git failure as "nothing to
commit" (lg-workflow-integrity-850). So:

- a commit the workspace's own pre-commit hook refused left the fixes
  uncommitted, logged nothing, and the gate still passed;
- a fixer edit to a file no run had recorded was never staged and never
  reported.

The fixers are measured instead of trusted: `take_fixer_footprint` records the
tree and a digest of every dirty file before they run, and the paths they
touched are what differs afterwards. Their new edits belong to the ticket — the
fixers ran on its behalf. An edit to a file that was already dirty and that no
run recorded is someone else's work-in-progress with the fix mixed in; it is
left uncommitted and named, never swept into the ticket's commit.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from loregarden.services.git_commit_push_service import commit_paths_in
from loregarden.services.worktree_snapshot import TreeSnapshot, read_tree

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixerFootprint:
    """The tree just before the fixers ran.

    `digests` holds a content digest for each path dirty at that moment, None
    for one that is not a readable regular file (deleted, a directory, a
    submodule). A path that is clean has no entry: any change to it shows up as
    a new dirty path.
    """

    tree: TreeSnapshot
    digests: Mapping[str, str | None]


@dataclass(frozen=True)
class AutofixCommit:
    committed: bool
    #: git's own words when `add` or `commit` failed; "" otherwise.
    error: str = ""
    #: Paths the fixers changed that this commit did not take: on an error,
    #: everything they touched; otherwise, their edits to files that were
    #: already dirty and that no run of the ticket recorded.
    left_uncommitted: tuple[str, ...] = ()
    #: The tree could not be read before the fixers ran, so their edits could
    #: not be told apart from what was already there; only recorded paths were
    #: offered to the commit.
    attribution_unknown: bool = False


def take_fixer_footprint(repo_root: Path) -> FixerFootprint:
    """Read the tree and digest its dirty files. Call immediately before the fixers."""
    tree = read_tree(repo_root)
    return FixerFootprint(
        tree=tree,
        digests={path: _digest(repo_root / path) for path in tree.dirty_paths or ()},
    )


def commit_fixer_changes(
    repo_root: Path,
    before: FixerFootprint,
    *,
    recorded: Iterable[str],
    message: str,
) -> AutofixCommit:
    """Commit the ticket's recorded paths plus what the fixers newly dirtied."""
    if not (repo_root / ".git").exists():
        # A workspace with no repository has no branch to commit to; the fix
        # stays in its tree, as everything else there does.
        return AutofixCommit(committed=False)
    recorded_paths = set(recorded)
    after = read_tree(repo_root)
    if after.dirty_paths is None:
        return AutofixCommit(
            committed=False,
            error=f"could not read the working tree at {repo_root} after the fixers ran",
        )
    if before.tree.dirty_paths is None:
        return _commit(
            repo_root,
            message,
            wanted=recorded_paths,
            touched=set(),
            foreign=set(),
            attribution_unknown=True,
        )

    touched = _paths_touched(repo_root, before, after.dirty_paths)
    foreign = {
        path for path in touched if path in before.tree.dirty_paths and path not in recorded_paths
    }
    return _commit(
        repo_root,
        message,
        wanted=(recorded_paths | touched) - foreign,
        touched=touched,
        foreign=foreign,
        attribution_unknown=False,
    )


def _commit(
    repo_root: Path,
    message: str,
    *,
    wanted: set[str],
    touched: set[str],
    foreign: set[str],
    attribution_unknown: bool,
) -> AutofixCommit:
    try:
        committed = commit_paths_in(repo_root, message, wanted)
    except ValueError as exc:
        # commit_paths_in raises with git's message when add or commit fails —
        # a refusing pre-commit hook, an unwritable index, a lock. Returned so
        # the caller reports it; never read as "nothing to commit".
        return AutofixCommit(
            committed=False,
            error=str(exc),
            left_uncommitted=tuple(sorted(touched)),
            attribution_unknown=attribution_unknown,
        )
    return AutofixCommit(
        committed=committed,
        left_uncommitted=tuple(sorted(foreign)),
        attribution_unknown=attribution_unknown,
    )


def _paths_touched(
    repo_root: Path, before: FixerFootprint, dirty_after: frozenset[str]
) -> set[str]:
    """Newly dirty paths, plus already-dirty ones whose content changed."""
    newly_dirty = set(dirty_after) - set(before.digests)
    rewritten = {
        path
        for path in before.digests.keys() & dirty_after
        if _digest(repo_root / path) != before.digests[path]
    }
    return newly_dirty | rewritten


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        # Unreadable both times compares equal, so a fixer edit to this file
        # would be missed; said here so that is never silent.
        logger.warning("could not read %s to tell whether a fixer changed it: %s", path, exc)
        return None
