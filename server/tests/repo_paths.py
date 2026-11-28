"""The checkout's root, found the same way from the tree and from a copy of it.

Tests used to compute it by depth, `Path(__file__).resolve().parents[2]`. That
holds only while the test sits exactly two levels below the root. mutmut runs
the suite from a copy one level deeper (`server/mutants/tests/`), where the
same expression names `server/` — and every test reading a repo file
(`lefthook.yml`, `.lefthook/scripts/`, `.claude/hooks/`) failed before a single
mutant ran.

So the root is found by what it holds, not where it is:

1. ``LOREGARDEN_REPO_ROOT``, when set (server-tests.sh and the dev scripts set
   it to the checkout they run). It must name a loregarden checkout — one
   holding ``lefthook.yml`` — or this raises rather than read another tree.
2. Otherwise the nearest directory above this file holding ``.git`` (a
   directory in a clone, a file in a worktree) or ``lefthook.yml``. Neither
   ``server/`` nor mutmut's ``server/mutants/`` holds either, so the walk
   passes through them to the checkout.

``REPO_ROOT`` is resolved once, at import — collection time, before any test
can point the environment variable at a ``tmp_path`` of its own.

``OWN_CHECKOUT_ROOT`` is the same walk with the override ignored: the checkout
*this file* lives in. Use it for a file that belongs to the test rather than to
whatever checkout the process was told about — a fixture this branch committed
is in this branch's tree, and reading it through the override makes a branch's
own fixtures look missing (a criteria check inherits ``LOREGARDEN_REPO_ROOT``
naming the primary checkout while ``cwd`` is the ticket's worktree). Keep
``REPO_ROOT`` for files that belong to the checkout under test
(``lefthook.yml``, ``.lefthook/scripts/``), where following the override is the
point.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT_ENV = "LOREGARDEN_REPO_ROOT"
ROOT_MARKERS = (".git", "lefthook.yml")


class RepoRootNotFound(RuntimeError):
    """Neither the override nor the walk named a checkout."""


def find_repo_root(start: Path, environ: Mapping[str, str] = os.environ) -> Path:
    """The checkout root for a file or directory at ``start``."""
    override = environ.get(REPO_ROOT_ENV)
    if override:
        root = Path(override).resolve()
        if not (root / "lefthook.yml").is_file():
            raise RepoRootNotFound(
                f"{REPO_ROOT_ENV}={override} holds no lefthook.yml, so it is not a "
                "loregarden checkout; unset it or point it at one"
            )
        return root
    start = start.resolve()
    for candidate in (start, *start.parents):
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    raise RepoRootNotFound(f"no directory above {start} holds any of {', '.join(ROOT_MARKERS)}")


REPO_ROOT = find_repo_root(Path(__file__).parent)

#: The checkout holding the tests themselves, never redirected by the override.
OWN_CHECKOUT_ROOT = find_repo_root(Path(__file__).parent, environ={})
