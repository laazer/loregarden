"""The dependency links loregarden puts in a worktree, and telling them from a ticket's work.

A ticket tree gets a symlink to the repository's own `client/node_modules`. A
repository that ignores `client/node_modules/` ignores a directory and not a
symlink, so git lists the link as an untracked file. Every reader of "what is
dirty here" then took loregarden's link for the ticket's work: landing refused
lmkr-execution-leaves-11 over it, the stage-end commit would have committed an
absolute path into this machine, and a done ticket's tree was never removed.

Only loregarden's own link is discounted: untracked, and pointing at that same
path in the repository's main working tree. A real directory, a link anywhere
else, and a link someone committed or staged are still whatever git says they
are. That is why this is not a line in `info/exclude`, which cannot tell them
apart and which every worktree of the repository shares.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from loregarden.services.git_subprocess import run_git

logger = logging.getLogger(__name__)

#: Directories a gate needs but git never puts in a worktree, because they are
#: gitignored build output. Linked from the parent checkout rather than
#: installed: `npm ci` per ticket costs minutes, and a worktree of the same repo
#: wants the same dependencies by definition.
_LINKED_TOOLCHAIN_DIRS = ("client/node_modules",)


def link_ignored_toolchains(repo_path: Path, worktree_path: Path) -> None:
    """Point a fresh worktree at the parent checkout's installed dependencies.

    Without this the client gate runs `npx oxlint` in a tree with no
    node_modules, npx tries to fetch it over the network on every gate, and the
    300s budget expires — which the orchestrator handed to the stage's own agent
    as a code failure, re-running a stage that had already passed. Every ticket
    hit it, every transition.

    Best-effort: a missing source, a link that cannot be made, or a directory
    the ticket already has are all fine and leave the gate to report for itself.
    """
    for relative in _LINKED_TOOLCHAIN_DIRS:
        source = repo_path / relative
        target = worktree_path / relative
        if not source.is_dir() or target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
        except OSError:
            logger.warning(
                "Could not link %s into worktree %s; gates needing it will report unavailable",
                relative,
                worktree_path,
                exc_info=True,
            )


def own_toolchain_links(tree: Path) -> frozenset[str]:
    """The untracked paths in ``tree`` that are loregarden's own dependency links.

    Costs no git call in a tree without a link. A git that cannot answer
    discounts nothing, so the link is reported as before: "could not tell" must
    not read as "not the ticket's".
    """
    linked = [path for path in _LINKED_TOOLCHAIN_DIRS if os.path.islink(tree / path)]
    if not linked:
        return frozenset()
    untracked = _git_fields(
        tree, ["ls-files", "--others", "--exclude-standard", "-z", "--", *linked]
    )
    if not untracked:
        return frozenset()
    # git lists the main working tree first, from any of its worktrees.
    listed = _git_fields(tree, ["worktree", "list", "--porcelain", "-z"])
    if not listed:
        return frozenset()
    main = Path(listed[0].removeprefix("worktree "))
    return frozenset(path for path in untracked if _points_at(tree / path, main / path))


def _points_at(link: Path, source: Path) -> bool:
    """Whether ``link`` names ``source`` itself, whose own target, if any, is not followed."""
    target = link.parent / os.readlink(link)
    return target.name == source.name and target.parent.resolve() == source.parent.resolve()


def _git_fields(tree: Path, args: list[str]) -> list[str]:
    done = run_git(args, cwd=str(tree), check=False, capture_output=True, text=True)
    if done.returncode != 0:
        logger.warning(
            "git %s failed in %s; its dependency links count as untracked files: %s",
            args[0],
            tree,
            (done.stderr or "").strip()[:400],
        )
        return []
    return [field for field in done.stdout.split("\0") if field]
