#!/usr/bin/env python3
"""Print every live workspace the installers serve, as ``<slug>\\t<root>``.

Backs ``--all`` on install-workspace-hooks.sh and install-workspace-docs.sh.
Reads the database read-only and directly rather than through the CLI: every
CLI entry point migrates what it opens, and an installer has no business
migrating anything.

Skipped: archived workspaces, and loregarden's own repository — its lefthook.yml
runs the gates directly and its AGENTS.md is what the docs block summarises.

Stdlib only, like the installers it serves.
"""

from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
from pathlib import Path


def workspace_roots(db: Path, loregarden_root: Path) -> list[tuple[str, Path]]:
    # mode=ro: a missing file is an error, not a fresh empty database.
    with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT slug, repo_path FROM workspaces WHERE archived_at IS NULL ORDER BY slug"
        ).fetchall()
    own = loregarden_root.resolve()
    found = []
    for slug, repo_path in rows:
        # Relative repo paths are from loregarden's own root, as in
        # services/workspace_paths.resolve_repo_path.
        path = Path(repo_path.strip() or ".")
        root = (path if path.is_absolute() else own / path).resolve()
        if root == own or own in root.parents:
            continue  # loregarden itself, or one of its worktrees
        found.append((slug, root))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loregarden-root", required=True, type=Path)
    args = parser.parse_args()
    db = args.loregarden_root / "data" / "loregarden.db"
    try:
        roots = workspace_roots(db, args.loregarden_root)
    except sqlite3.Error as exc:
        print(f"cannot list workspaces from {db}: {exc}", file=sys.stderr)
        return 1
    for slug, root in roots:
        print(f"{slug}\t{root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
