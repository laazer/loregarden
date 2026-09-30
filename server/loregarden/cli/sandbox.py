"""`loregarden sandbox …` — data for a sandbox server to show real-shaped UI.

    loregarden sandbox snapshot --into DIR   # copy production (needs the live DB)
    loregarden sandbox seed --into DIR       # build the production-shaped scenario

Both print `export` lines for the sandbox server's environment, so a shell can
`eval` them (`scripts/sandbox.sh` does). The summary is a `#` comment, which
`eval` ignores and a person reading the output does not.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
from pathlib import Path

from loregarden.cli.errors import UsageError
from loregarden.config import resolved_database_path, resolved_memory_sqlite_path
from loregarden.db.session import engine, init_db
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.path_resolve import sqlite_url_for_path
from loregarden.services.sandbox_snapshot import take_snapshot
from loregarden.services.seed import seed_database
from loregarden.testing.prod_shape import build_prod_shape
from sqlmodel import Session


def _exports(summary: str, database: Path, memory_sqlite: Path | None) -> str:
    env = {
        "LOREGARDEN_SANDBOX": "1",
        "LOREGARDEN_DATABASE_URL": sqlite_url_for_path(database),
        # Blank, not unset: the sandbox must never fall back to main's graph or
        # vault, and an empty value is what `config` reads as "none".
        "LOREGARDEN_MEMORY_SQLITE_URL": sqlite_url_for_path(memory_sqlite) if memory_sqlite else "",
        "LOREGARDEN_OBSIDIAN_VAULT_DIR": "",
    }
    lines = [f"# {summary}"]
    lines += [f"export {key}={shlex.quote(value)}" for key, value in env.items()]
    return "\n".join(lines)


def _snapshot(args: argparse.Namespace) -> str:
    snap = take_snapshot(Path(args.into))
    memory = (
        f"memory graphs: {', '.join(snap.memory_workspaces) or 'base only'}"
        if snap.memory_sqlite
        else "no memory graph configured"
    )
    return _exports(f"snapshot in {args.into}; {memory}", snap.database, snap.memory_sqlite)


def _inside(path: Path | None, root: Path) -> bool:
    return path is not None and path.resolve().is_relative_to(root.resolve())


def _seed(args: argparse.Namespace) -> str:
    """Build the scenario into the database and memory this process is bound to.

    The engine binds `LOREGARDEN_DATABASE_URL` at import, so the caller points
    it (and the memory URL) inside `--into` first — and this refuses to run
    otherwise, because seeding recreates the database it is pointed at.
    """
    into = Path(args.into)
    database = resolved_database_path()
    memory = resolved_memory_sqlite_path()
    if not _inside(database, into) or not _inside(memory, into):
        raise UsageError(
            f"refusing to seed: the database ({database}) and memory graph ({memory}) must both "
            f"live under {into}. Set LOREGARDEN_DATABASE_URL and LOREGARDEN_MEMORY_SQLITE_URL "
            "there first — scripts/sandbox.sh --seeded does."
        )

    engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    if memory.parent.exists():
        shutil.rmtree(memory.parent)
    database.parent.mkdir(parents=True, exist_ok=True)
    memory.parent.mkdir(parents=True, exist_ok=True)

    init_db()
    with Session(engine) as session:
        seed_database(session)
        summary = build_prod_shape(
            session, graph_path=AgentMemoryService.from_settings().graph_path
        )
    return _exports(
        f"seeded production shape in {into}: {summary.tickets} tickets, {summary.findings} "
        f"findings, memory {summary.memory_records}",
        database,
        memory,
    )


def register(sub: argparse._SubParsersAction) -> None:
    """Add `sandbox snapshot` and `sandbox seed` to the root CLI's `sandbox` group."""
    snapshot = sub.add_parser(
        "snapshot",
        help="Copy the database and memory graphs, and print the sandbox server's env.",
    )
    snapshot.add_argument("--into", required=True, help="Directory to write the copies to.")
    snapshot.set_defaults(run=_snapshot)

    seed = sub.add_parser(
        "seed",
        help="Build the production-shaped scenario (no live data needed), and print the env.",
    )
    seed.add_argument("--into", required=True, help="Directory the database and memory live in.")
    seed.set_defaults(run=_seed)
