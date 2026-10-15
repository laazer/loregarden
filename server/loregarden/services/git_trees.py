"""Which checkout a path belongs to: this repository's main working tree.

One question, asked of git and answered from any linked worktree. It exists
because two decisions inside `git_branch.ensure_ticket_branch` were reading the
tree they were handed as the repository's main working tree — true while every
dispatch ran in the workspace's shared checkout, and false since 779 stopped
skipping that call for worktree runs (lg-milestone-that-916). Derived in two
places the two decisions can disagree, and the disagreement is invisible: the
guard protects one tree while the message names another.

`db/shared_database_guard` asks the same question and imports *up* into here,
so this module stays on `worktree_snapshot -> git_subprocess -> stdlib` and
pulls in no `loregarden.db` module.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.services.worktree_snapshot import git_reading

logger = logging.getLogger(__name__)


def main_working_tree(tree: Path) -> Path | None:
    """The main working tree of the repository `tree` belongs to, or None.

    None means git could not be asked or could not answer — the contract
    `worktree_snapshot.git_reading` already honours, borrowed rather than
    re-implemented, including the case where git never ran at all. It is never
    "there is no main working tree", and callers must not read it as safe.

    `--path-format=absolute` precedes the question so that it applies to it
    (git >= 2.31); a relative answer is still resolved against `tree`, as
    `shared_database_guard` has always done, because resolving it against this
    *process's* working directory would name an unrelated tree.
    """
    proc = git_reading(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        repo_root=tree,
        describing="rev-parse --git-common-dir",
    )
    if proc is None:
        return None
    answer = proc.stdout.strip()
    if not answer:
        # git exited 0 and said nothing, which `git_reading` cannot classify.
        # `Path("")` resolves to the process working directory, whose parent is
        # some unrelated tree — and a wrong path fails OPEN, comparing unequal
        # to the real main working tree, which is the one answer that
        # authorizes `worktree remove --force`.
        logger.warning("git named no --git-common-dir in %s; topology unknown", tree)
        return None
    common = Path(answer)
    if not common.is_absolute():
        common = tree / common
    return common.resolve().parent


def is_main_working_tree(path: Path | str, *, main_tree: Path | None) -> bool:
    """Is `path` the main working tree `main_tree` names?

    **Fails closed.** Answering False is what authorizes
    `git worktree remove --force`, so an unknown topology (`main_tree is None`)
    and a path that cannot be resolved both answer True. The OSError posture is
    `git_branch._is_primary_checkout`'s, kept verbatim and kept audible: a
    guard that fires silently is indistinguishable from one that never fired.

    Both sides are resolved. On macOS `/tmp` is a symlink to `/private/tmp`, and
    comparing one resolved spelling against one unresolved spelling answers
    False there — silently authorizing the removal the caller is guarding.
    """
    if main_tree is None:
        return True
    try:
        return Path(path).resolve() == main_tree.resolve()
    except OSError:
        logger.warning(
            "Could not resolve %r against %s; treating it as the main working tree",
            path,
            main_tree,
            exc_info=True,
        )
        return True


__all__ = ["is_main_working_tree", "main_working_tree"]
