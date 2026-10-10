"""Copy production's database and memory graphs for a sandbox server to read.

UI built against a fixture or an empty dev database looks fine at three rows and
fails at production volume: the Monitor tab with 138 findings, a memory map of
33 unlinked records, 74 milestones behind one sentence. Each shipped because
nobody had looked at it on real data — and looking took a hand-built launch
config. This makes the copy one call (`loregarden sandbox snapshot`, behind
`task sandbox`).

Everything goes through SQLite's backup API (see `local_instances.snapshot_database`
for why a file copy of a WAL database is not safe), read-only on the source. The
sandbox server that reads these copies runs with `LOREGARDEN_SANDBOX=1`, so it
never adopts main's runs or worktrees, and its memory writes land in the copies.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from loregarden.config import resolved_memory_sqlite_path
from loregarden.services.local_instances import snapshot_database
from pydantic import BaseModel

_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


class SandboxSnapshot(BaseModel):
    database: Path
    #: The base memory graph copy, or None when this install has no memory graph.
    memory_sqlite: Path | None
    #: Per-workspace graphs copied beside it (`<slug>/memory.db`).
    memory_workspaces: list[str]


def _clear(db_path: Path) -> None:
    """Remove a previous copy, sidecars included, so the backup starts clean."""
    for suffix in ("", *_SQLITE_SIDECARS):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


def _backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _clear(target)
    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src,
        sqlite3.connect(target) as dst,
    ):
        src.backup(dst)


def take_snapshot(into: Path) -> SandboxSnapshot:
    """Snapshot the configured database and every memory graph into `into`.

    Memory keeps its layout — `memory.db` at the root, `<slug>/memory.db` per
    workspace — because the server derives the per-workspace path from the base
    one (`resolved_memory_sqlite_path`).
    """
    into.mkdir(parents=True, exist_ok=True)
    database = into / "loregarden.db"
    _clear(database)
    snapshot_database(database)

    base = resolved_memory_sqlite_path()
    if base is None:
        return SandboxSnapshot(database=database, memory_sqlite=None, memory_workspaces=[])

    memory_root = into / "memory"
    memory_base = memory_root / base.name
    if base.is_file():
        _backup(base, memory_base)
    workspaces = []
    if base.parent.is_dir():
        for child in sorted(base.parent.iterdir()):
            graph = child / base.name
            if child.is_dir() and graph.is_file():
                _backup(graph, memory_root / child.name / base.name)
                workspaces.append(child.name)
    return SandboxSnapshot(
        database=database, memory_sqlite=memory_base, memory_workspaces=workspaces
    )
