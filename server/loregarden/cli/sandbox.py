"""`loregarden sandbox snapshot` — copy production data for a sandbox server.

Prints `export` lines for the sandbox server's environment, so a shell can
`eval` them (`scripts/sandbox.sh` does). The summary is a `#` comment, which
`eval` ignores and a person reading the output does not.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from loregarden.services.path_resolve import sqlite_url_for_path
from loregarden.services.sandbox_snapshot import take_snapshot


def _run(args: argparse.Namespace) -> str:
    snap = take_snapshot(Path(args.into))
    env = {
        "LOREGARDEN_SANDBOX": "1",
        "LOREGARDEN_DATABASE_URL": sqlite_url_for_path(snap.database),
        # Blank, not unset: the sandbox must never fall back to main's graph or
        # vault, and an empty value is what `config` reads as "none".
        "LOREGARDEN_MEMORY_SQLITE_URL": (
            sqlite_url_for_path(snap.memory_sqlite) if snap.memory_sqlite else ""
        ),
        "LOREGARDEN_OBSIDIAN_VAULT_DIR": "",
    }
    memory = (
        f"memory graphs: {', '.join(snap.memory_workspaces) or 'base only'}"
        if snap.memory_sqlite
        else "no memory graph configured"
    )
    lines = [f"# snapshot in {args.into}; {memory}"]
    lines += [f"export {key}={shlex.quote(value)}" for key, value in env.items()]
    return "\n".join(lines)


def register(sub: argparse._SubParsersAction) -> None:
    """Add `sandbox snapshot` to the root CLI's `sandbox` group."""
    parser = sub.add_parser(
        "snapshot",
        help="Copy the database and memory graphs, and print the sandbox server's env.",
    )
    parser.add_argument("--into", required=True, help="Directory to write the copies to.")
    parser.set_defaults(run=_run)
