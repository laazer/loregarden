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

        studio_session_rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='ticket_studio_sessions'")
        ).fetchall()
        if not studio_session_rows:
            conn.execute(
                text(
                    """
                    CREATE TABLE ticket_studio_sessions (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        brief TEXT NOT NULL DEFAULT '',
                        parent_ticket_id TEXT,
                        status TEXT NOT NULL DEFAULT 'draft',
                        draft_json TEXT NOT NULL DEFAULT '[]',
                        summary TEXT NOT NULL DEFAULT '',
                        clarifying_questions_json TEXT NOT NULL DEFAULT '[]',
                        runtime_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
                        FOREIGN KEY(parent_ticket_id) REFERENCES tickets(id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_ticket_studio_sessions_workspace_id ON ticket_studio_sessions (workspace_id)"
                )
            )
            conn.execute(
                text("CREATE INDEX ix_ticket_studio_sessions_status ON ticket_studio_sessions (status)")
            )

        studio_message_rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='ticket_studio_messages'")
        ).fetchall()
        if not studio_message_rows:
            conn.execute(
                text(
                    """
                    CREATE TABLE ticket_studio_messages (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(session_id) REFERENCES ticket_studio_sessions(id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_ticket_studio_messages_session_id ON ticket_studio_messages (session_id)"
                )
            )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
