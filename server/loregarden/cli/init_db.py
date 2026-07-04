"""Create a fresh SQLite database and seed bootstrap data."""

from __future__ import annotations

import argparse
from pathlib import Path

from loregarden.config import settings


def sqlite_db_path() -> Path:
    from loregarden.config import Settings

    cfg = Settings()
    url = cfg.database_url
    if not url.startswith("sqlite:///"):
        raise ValueError(f"init-db only supports SQLite URLs, got: {url}")
    db_path = Path(url.removeprefix("sqlite:///"))
    if not db_path.is_absolute():
        db_path = cfg.repo_root / db_path
    return db_path


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize a clean Loregarden SQLite database.")
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Create schema only; do not seed bootstrap workspaces/tickets.",
    )
    args = parser.parse_args(argv)

    db_path = sqlite_db_path()
    removed = remove_sqlite_files(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from sqlmodel import Session

    from loregarden.db.session import engine, init_db
    from loregarden.services.seed import seed_database

    init_db()
    if not args.empty:
        with Session(engine) as session:
            seed_database(session)

    if removed:
        print(f"removed {len(removed)} existing file(s)")
    print(f"initialized {db_path.relative_to(settings.repo_root)}")
    if args.empty:
        print("schema only (--empty)")
    else:
        print("seeded bootstrap data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
