import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
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
from sqlalchemy import Connection, event
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


def driver_transaction_open(connection: Connection) -> bool:
    """Whether the *DBAPI* connection — not SQLAlchemy — has a transaction open.

    The attribute is pysqlite's, and so is the problem: it is the driver
    SQLAlchemy runs in a mode where neither a SELECT nor a SAVEPOINT emits a
    `BEGIN`. This module binds SQLite for the whole process, so the assumption
    holds here by construction — and a driver without the attribute would fail
    loudly on this line rather than quietly commit the caller's Session, which
    is the right way round for a probe that `service_write` depends on.
    """
    driver: sqlite3.Connection = connection.connection.driver_connection
    return driver.in_transaction


@contextmanager
def service_write(session: Session) -> Iterator[Session]:
    """Yield the Session a service's own write should go through.

    For a service handed someone else's Session that wants its write to unwind
    independently. Two caller shapes reach such a service, and they need
    opposite things — so this decides, rather than each service deciding again.

    *A caller that has only read* — the state `get_session()` below hands every
    request — holds no transaction of its own. Writing through its Session
    means opening one, and a service may not end a transaction on a Session it
    was handed (608/610), so the lock would outlive the call. SQLite locks the
    whole *database file*, not a row, so anything slow the service does next —
    a network fetch, most of all — runs with the control plane unwritable for
    its duration, which is a lever a remote server should not have (638). We
    therefore take our own connection: the lock is acquired and released inside
    this block, the write is durable when it exits, and the caller's Session is
    left exactly as it was found.

    *A caller that is mid-write* already holds that lock because it chose to. A
    second connection would deadlock against it, so we nest inside the caller's
    transaction and let the caller own both ends — 616's contract, unchanged.
    The write becomes durable when that caller commits, and its rollback
    discards ours with its own.

    `session.in_transaction()` cannot tell the two apart: it is normally True
    by this point, autobegun by whatever the service read first, while pysqlite
    has emitted no `BEGIN`. The driver connection can, and that is what
    `driver_transaction_open` asks.

    **The yielded Session is not always the one passed in.** Resolve rows
    *through the yielded Session* — re-query rather than reusing an instance
    attached to the outer one, whose identity map the writer does not share.
    """
    if driver_transaction_open(session.connection()):
        with session.begin_nested():
            yield session
        return
    with Session(session.get_bind()) as writer, writer.begin():
        yield writer
