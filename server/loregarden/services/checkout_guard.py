"""Would removing this checkout lose anything? One answer for every caller.

Branch Triage removes worktrees permanently (`git worktree remove`); Machine →
Disk moves them to the Trash. Both ask this module first, so the two cannot
hold different bars for the same directory. A removal is refused when:

- the path is the repository's main working tree, or git cannot say which
  tree is main (fails closed, `git_trees.is_main_working_tree`);
- `worktree_lifecycle.checkout_loss` finds uncommitted or untracked changes,
  or a commit only HEAD reaches;
- git ignores a file there that nothing regenerates: an `.env`, a database,
  a key, a nested repository. Ignored files are invisible to `git status`,
  so the first two checks never see them, and they are exactly the files a
  person keeps in a worktree and has nowhere else.

The answer is a sentence for the person, or None when nothing would be lost.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from loregarden.models.domain import REGENERABLE_KINDS
from loregarden.services.disk_usage.scanner import kind_for_name
from loregarden.services.git_subprocess import run_git
from loregarden.services.git_trees import is_main_working_tree, main_working_tree
from loregarden.services.worktree_lifecycle import KeepReason, checkout_loss

logger = logging.getLogger(__name__)

#: Ignored files no package manager or build puts back.
PRECIOUS_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".kdbx")
PRECIOUS_NAMES = frozenset(
    {
        "id_rsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
    }
)
_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
#: A search past this many entries refuses rather than guesses.
PRECIOUS_WALK_CAP = 200_000
_MAX_NAMED = 5


def named(paths: list[str]) -> str:
    """The first few paths, and how many more."""
    shown = ", ".join(paths[:_MAX_NAMED])
    more = len(paths) - _MAX_NAMED
    return f"{shown} and {more} more" if more > 0 else shown


def is_precious(name: str) -> bool:
    if name in _ENV_TEMPLATES:
        return False
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in PRECIOUS_NAMES
        or name.endswith(PRECIOUS_SUFFIXES)
    )


def _visit(entry: os.DirEntry[str], stack: list[Path], hits: list[str]) -> None:
    """One entry of `find_precious`'s walk: descend, record a hit, or skip it."""
    if entry.is_symlink():
        return
    if not entry.is_dir(follow_symlinks=False):
        if is_precious(entry.name):
            hits.append(entry.path)
        return
    if entry.name == ".git":
        hits.append(entry.path)
    elif kind_for_name(entry.name) not in REGENERABLE_KINDS:
        stack.append(Path(entry.path))


def find_precious(root: Path) -> tuple[list[str], bool]:
    """Irreplaceable-looking files under `root`, and whether the search finished.

    Skips regenerable folders by name (a package's test certificate in
    `node_modules` is not the person's key). Never follows a symlink. Names a
    nested `.git` too: a repository inside is work nothing here can vouch for.
    An unreadable folder is named as a hit: what cannot be read cannot be cleared.
    """
    hits: list[str] = []
    if root.is_symlink() or kind_for_name(root.name) in REGENERABLE_KINDS:
        return hits, True
    if root.is_file():
        return ([str(root)] if is_precious(root.name) else []), True
    seen = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                listed = list(entries)
        except OSError as exc:
            hits.append(f"{current} (unreadable: {exc.strerror or exc})")
            continue
        for entry in listed:
            seen += 1
            if seen > PRECIOUS_WALK_CAP:
                return hits, False
            _visit(entry, stack, hits)
    return hits, True


def _ignored_entries(path: Path) -> list[str] | None:
    """What git ignores in the checkout at `path`, one entry per ignored file or folder."""
    proc = run_git(
        ["status", "--porcelain", "--ignored=matching", "--untracked-files=all", "-z"],
        cwd=str(path),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        logger.warning("git status --ignored failed in %s: %s", path, (proc.stderr or "").strip())
        return None
    return [entry[3:].rstrip("/") for entry in proc.stdout.split("\0") if entry.startswith("!! ")]


def checkout_removal_refusal(path: Path) -> str | None:
    """Why removing the checkout at `path` would lose something, or None if nothing would.

    A directory that is already gone loses nothing; git only needs to forget it.
    """
    if not path.exists():
        return None
    if is_main_working_tree(path, main_tree=main_working_tree(path)):
        return "It is the repository's main checkout, or git could not say which checkout is main."
    loss = checkout_loss(path)
    if loss is KeepReason.UNCOMMITTED:
        return "It has uncommitted or untracked changes that exist nowhere else."
    if loss is not None:
        return f"HEAD holds {loss.value}. Put a branch or tag on it before removing the checkout."
    ignored = _ignored_entries(path)
    if ignored is None:
        return "git could not list what this worktree ignores."
    precious: list[str] = []
    for entry in ignored:
        hits, finished = find_precious(path / entry)
        if not finished:
            return f"Too many ignored files under {entry} to check them all."
        precious.extend(hits)
    if precious:
        return f"It holds ignored files nothing regenerates: {named(precious)}. Move or delete them first."
    return None
