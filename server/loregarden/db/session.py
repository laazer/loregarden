from collections.abc import Generator
from pathlib import Path

from loregarden.config import settings
from loregarden.db.enum_integrity import report_unreadable_enum_values
from loregarden.db.migrations import apply_migrations

# Imported for its side effect: registering the WorkflowInstance mapper events
# that refuse a pin to a template version with no terminal stage. Kept here so
# every process that opens a session — app, tests, CLI — has the guard armed.
from loregarden.db.workflow_pin_guard import WorkflowPinWithoutTerminalStageError  # noqa: F401
from loregarden.services.path_resolve import (
    is_under_icloud,
    resolve_icloud_root,
    resolve_sqlite_path,
    sqlite_url_for_path,
)
from sqlalchemy import event
from sqlalchemy.engine import Engine
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


@event.listens_for(Engine, "connect")
def _enforce_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Honour the foreign keys the schema declares.

    SQLite defaults this off, per connection — so every reference in the schema
    was documentation rather than a guarantee, and a delete path that missed a
    child table left rows pointing at nothing instead of failing.

    Registered on ``Engine`` rather than on this module's engine so that every
    SQLite connection in the process gets it, including the per-test engines the
    suite builds and any engine opened by a script. A pragma that only some
    connections set is worse than none: it makes enforcement depend on which
    code path opened the connection.

    ``apply_migrations`` turns it back off for the length of a migration run,
    where rebuilding a table means dropping one every dependant references.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
    # After migrations, so a value the migrations were meant to convert is not
    # reported as corruption. Logs and continues: refusing to boot over one bad
    # row would take the control plane down harder than the bad row does.
    report_unreadable_enum_values(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
