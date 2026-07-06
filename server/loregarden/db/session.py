from collections.abc import Generator
from pathlib import Path

from loregarden.config import settings
from loregarden.db.migrations import apply_migrations
from loregarden.services.path_resolve import (
    is_under_icloud,
    resolve_icloud_root,
    resolve_sqlite_path,
    sqlite_url_for_path,
)
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


def _sqlite_url(url: str) -> str:
    db_path = resolve_sqlite_path(url, settings.repo_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_url_for_path(db_path)


def _db_path_from_engine_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///"))


engine = create_engine(
    _sqlite_url(settings.database_url),
    connect_args={"check_same_thread": False, "timeout": 30.0},
)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    db_path = _db_path_from_engine_url(str(engine.url))
    icloud_root = resolve_icloud_root(settings.icloud_root)
    if db_path and is_under_icloud(db_path, icloud_root):
        # iCloud Drive + WAL sidecars cause sync conflicts; prefer DELETE journal there.
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute("PRAGMA synchronous=FULL")
    else:
        cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
