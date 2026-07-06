from collections.abc import Generator
from pathlib import Path

from loregarden.config import settings
from loregarden.services.path_resolve import (
    is_under_icloud,
    resolve_icloud_root,
    resolve_sqlite_path,
    sqlite_url_for_path,
)
from sqlalchemy import event, text
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
    _apply_sqlite_migrations(engine)


def _apply_sqlite_migrations(eng) -> None:
    if not str(eng.url).startswith("sqlite"):
        return
    with eng.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(workspaces)")).fetchall()
        if not rows:
            return
        columns = {row[1] for row in rows}
        if "workflow_override_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE workspaces ADD COLUMN workflow_override_json TEXT NOT NULL DEFAULT '{}'"
                )
            )

        ticket_rows = conn.execute(text("PRAGMA table_info(tickets)")).fetchall()
        if ticket_rows:
            ticket_cols = {row[1] for row in ticket_rows}
            if "work_item_type" not in ticket_cols:
                conn.execute(
                    text(
                        "ALTER TABLE tickets ADD COLUMN work_item_type TEXT NOT NULL DEFAULT 'task'"
                    )
                )
            if "parent_ticket_id" not in ticket_cols:
                conn.execute(text("ALTER TABLE tickets ADD COLUMN parent_ticket_id TEXT"))
            if "cycle_id" not in ticket_cols:
                conn.execute(text("ALTER TABLE tickets ADD COLUMN cycle_id TEXT"))
            if "state_locked" not in ticket_cols:
                conn.execute(
                    text("ALTER TABLE tickets ADD COLUMN state_locked INTEGER NOT NULL DEFAULT 0")
                )
            if "triage_runtime_json" not in ticket_cols:
                conn.execute(
                    text(
                        "ALTER TABLE tickets ADD COLUMN triage_runtime_json TEXT NOT NULL DEFAULT '{}'"
                    )
                )
            if "workflow_disabled" not in ticket_cols:
                conn.execute(
                    text(
                        "ALTER TABLE tickets ADD COLUMN workflow_disabled INTEGER NOT NULL DEFAULT 0"
                    )
                )
            if "permission_allowlist_json" not in ticket_cols:
                conn.execute(
                    text(
                        "ALTER TABLE tickets ADD COLUMN permission_allowlist_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )

        ws_rows = conn.execute(text("PRAGMA table_info(workspaces)")).fetchall()
        if ws_rows:
            ws_cols = {row[1] for row in ws_rows}
            if "orchestration_profile_slug" not in ws_cols:
                conn.execute(
                    text(
                        "ALTER TABLE workspaces ADD COLUMN orchestration_profile_slug TEXT NOT NULL DEFAULT ''"
                    )
                )
            runtime_cols = {
                "cli_adapter": "ALTER TABLE workspaces ADD COLUMN cli_adapter TEXT NOT NULL DEFAULT ''",
                "claude_model": "ALTER TABLE workspaces ADD COLUMN claude_model TEXT NOT NULL DEFAULT ''",
                "cursor_model": "ALTER TABLE workspaces ADD COLUMN cursor_model TEXT NOT NULL DEFAULT ''",
                "lmstudio_base_url": "ALTER TABLE workspaces ADD COLUMN lmstudio_base_url TEXT NOT NULL DEFAULT ''",
                "lmstudio_model": "ALTER TABLE workspaces ADD COLUMN lmstudio_model TEXT NOT NULL DEFAULT ''",
            }
            ws_cols = {
                row[1] for row in conn.execute(text("PRAGMA table_info(workspaces)")).fetchall()
            }
            for col, stmt in runtime_cols.items():
                if col not in ws_cols:
                    conn.execute(text(stmt))
            ws_cols = {
                row[1] for row in conn.execute(text("PRAGMA table_info(workspaces)")).fetchall()
            }
            if "permission_allowlist_json" not in ws_cols:
                conn.execute(
                    text(
                        "ALTER TABLE workspaces ADD COLUMN permission_allowlist_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )

        approval_rows = conn.execute(text("PRAGMA table_info(approvals)")).fetchall()
        if approval_rows:
            approval_cols = {row[1] for row in approval_rows}
            migrations = {
                "run_id": "ALTER TABLE approvals ADD COLUMN run_id TEXT",
                "kind": "ALTER TABLE approvals ADD COLUMN kind TEXT NOT NULL DEFAULT 'workflow_gate'",
                "permission_request_id": "ALTER TABLE approvals ADD COLUMN permission_request_id TEXT NOT NULL DEFAULT ''",
                "tool_name": "ALTER TABLE approvals ADD COLUMN tool_name TEXT NOT NULL DEFAULT ''",
                "tool_input_json": "ALTER TABLE approvals ADD COLUMN tool_input_json TEXT NOT NULL DEFAULT '{}'",
                "cli_adapter": "ALTER TABLE approvals ADD COLUMN cli_adapter TEXT NOT NULL DEFAULT ''",
                "cli_session_id": "ALTER TABLE approvals ADD COLUMN cli_session_id TEXT NOT NULL DEFAULT ''",
                "response_json": "ALTER TABLE approvals ADD COLUMN response_json TEXT NOT NULL DEFAULT '{}'",
            }
            for col, stmt in migrations.items():
                if col not in approval_cols:
                    conn.execute(text(stmt))

        agent_rows = conn.execute(text("PRAGMA table_info(agent_runs)")).fetchall()
        if agent_rows:
            agent_cols = {row[1] for row in agent_rows}
            if "orchestration_run_id" not in agent_cols:
                conn.execute(text("ALTER TABLE agent_runs ADD COLUMN orchestration_run_id TEXT"))

        orch_rows = conn.execute(text("PRAGMA table_info(orchestration_runs)")).fetchall()
        if orch_rows:
            orch_cols = {row[1] for row in orch_rows}
            if "auto_approve" not in orch_cols:
                conn.execute(
                    text(
                        "ALTER TABLE orchestration_runs ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0"
                    )
                )
            if "stop_at_stage_key" not in orch_cols:
                conn.execute(
                    text(
                        "ALTER TABLE orchestration_runs ADD COLUMN stop_at_stage_key TEXT NOT NULL DEFAULT ''"
                    )
                )

        triage_rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='triage_messages'")
        ).fetchall()
        if not triage_rows:
            conn.execute(
                text(
                    """
                    CREATE TABLE triage_messages (
                        id TEXT PRIMARY KEY,
                        ticket_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(ticket_id) REFERENCES tickets(id)
                    )
                    """
                )
            )
            conn.execute(
                text("CREATE INDEX ix_triage_messages_ticket_id ON triage_messages (ticket_id)")
            )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
