"""The files loregarden itself puts in a ticket's tree, which are not the ticket's work.

Two kinds: the dependency link (`toolchain_links`) and the handoff export the
workspace gates read (`handoff_store.export_for_gate`). Every reader of "what is
dirty here" asks this module, so a third kind has one place to be added.

lmkr-execution-leaves-12 finished every stage and was refused over its export:
only loregarden's own repository ignores `.loregarden/`, a repair run had
recorded the file as changed, and landing counts an untracked file a run
recorded. lmkr-execution-leaves-11's had already been committed and landed that
way. The export now ignores itself, but that does nothing for one a branch
already holds. So a change to an export is discounted whatever git calls it:
the next gate run rebuilds the directory from the database, and nothing
there is anyone's to keep. Two changes are still reported, because the export
never makes them and a ticket tidying the repository would: a removal someone
staged, and a ticket's whole directory gone.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath

from loregarden.services.git_subprocess import run_git
from loregarden.services.handoff_store import HANDOFF_SCRATCH_SUBDIR, scratch_root
from loregarden.services.toolchain_links import own_toolchain_links

logger = logging.getLogger(__name__)

#: `XY ` before the path in each `git status --porcelain` record.
_STATUS_WIDTH = 3


def own_files(tree: Path) -> frozenset[str]:
    """The paths git reports changed or untracked in ``tree`` that are loregarden's own."""
    return own_toolchain_links(tree) | _own_handoff_exports(tree)


def excluding_own_files(tree: Path) -> list[str]:
    """Arguments ending a `git status`, `add -A` or `ls-files` so it passes over those files.

    Empty when there are none, which leaves the command exactly as it was.
    """
    own = own_files(tree)
    if not own:
        return []
    return ["--", ":/", *(_excluding(path) for path in sorted(own))]


def _excluding(path: str) -> str:
    """A pathspec leaving out exactly ``path``, spelled as a pattern with no fixed beginning.

    `git add` exits 1 when a pathspec names something inside an ignored directory,
    even one that excludes it, and a tracked export is inside one. It looks only
    at the part before the first wildcard, so the first character is made one.
    """
    rest = re.sub(r"([*?\[\\])", r"\\\1", path[1:])
    return f":(top,exclude,glob)[{path[0]}]{rest}"


def _own_handoff_exports(tree: Path) -> frozenset[str]:
    """The exported handoff files git reports in ``tree``.

    Costs no git call in a tree without an export. A git that cannot answer
    discounts nothing, as with the links.
    """
    root = scratch_root(tree)
    if not root.is_dir():
        return frozenset()
    done = run_git(
        [
            "status",
            "--porcelain",
            "-z",
            "--no-renames",
            "--untracked-files=all",
            "--",
            f":(top){HANDOFF_SCRATCH_SUBDIR}",
        ],
        cwd=str(tree),
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        logger.warning(
            "git status failed in %s; its handoff exports count as changed files: %s",
            tree,
            (done.stderr or "").strip()[:400],
        )
        return frozenset()
    kept = (r for r in done.stdout.split("\0") if r and not r.startswith("D"))
    return frozenset(path for record in kept if _is_export(root, path := record[_STATUS_WIDTH:]))


def _is_export(root: Path, path: str) -> bool:
    """Whether ``path`` is in a ticket's export directory, and that directory is still there."""
    inside = PurePosixPath(path).relative_to(HANDOFF_SCRATCH_SUBDIR).parts
    return bool(inside) and (root / inside[0]).is_dir()
