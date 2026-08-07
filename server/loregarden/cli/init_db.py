"""Create a fresh SQLite database and seed bootstrap data."""

from __future__ import annotations

import argparse
from pathlib import Path

from loregarden.config import settings
from loregarden.services.path_resolve import resolve_sqlite_path


def sqlite_db_path() -> Path:
    from loregarden.config import Settings

    cfg = Settings()
    return resolve_sqlite_path(cfg.database_url, cfg.repo_root)


def remove_sqlite_files(db_path: Path) -> list[Path]:
    removed: list[Path] = []
    for candidate in (
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-journal"),
    ):
        if candidate.is_file():
            candidate.unlink()
            removed.append(candidate)
    return removed


def _initialize(*, empty: bool) -> str:
    """Recreate the database from scratch and report what happened."""
    db_path = sqlite_db_path()
    removed = remove_sqlite_files(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from loregarden.db.session import engine, init_db
    from loregarden.services.seed import seed_database
    from sqlmodel import Session

    init_db()
    if not empty:
        with Session(engine) as session:
            seed_database(session)

    try:
        display = db_path.relative_to(settings.repo_root)
    except ValueError:
        display = db_path
    lines = [f"removed {len(removed)} existing file(s)"] if removed else []
    lines.append(f"initialized {display}")
    lines.append("schema only (--empty)" if empty else "seeded bootstrap data")
    return "\n".join(lines)


def _run(args: argparse.Namespace) -> str:
    return _initialize(empty=args.empty)


def register(sub: argparse._SubParsersAction) -> None:
    """Add `db init` to the root CLI's `db` group."""
    parser = sub.add_parser(
        "init",
        help="Recreate the SQLite database from scratch — DELETES the existing file.",
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Create schema only; do not seed bootstrap workspaces/tickets.",
    )
    parser.set_defaults(run=_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize a clean Loregarden SQLite database.")
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Create schema only; do not seed bootstrap workspaces/tickets.",
    )
    args = parser.parse_args(argv)
    print(_initialize(empty=args.empty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
