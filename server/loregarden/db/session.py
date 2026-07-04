from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from loregarden.config import settings


def _sqlite_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", ""))
        if not db_path.is_absolute():
            db_path = settings.repo_root / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path}"
    return url


engine = create_engine(
    _sqlite_url(settings.database_url),
    connect_args={"check_same_thread": False},
)


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
                conn.execute(
                    text("ALTER TABLE tickets ADD COLUMN parent_ticket_id TEXT")
                )
            if "cycle_id" not in ticket_cols:
                conn.execute(text("ALTER TABLE tickets ADD COLUMN cycle_id TEXT"))
            if "state_locked" not in ticket_cols:
                conn.execute(
                    text(
                        "ALTER TABLE tickets ADD COLUMN state_locked INTEGER NOT NULL DEFAULT 0"
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

        agent_rows = conn.execute(text("PRAGMA table_info(agent_runs)")).fetchall()
        if agent_rows:
            agent_cols = {row[1] for row in agent_rows}
            if "orchestration_run_id" not in agent_cols:
                conn.execute(text("ALTER TABLE agent_runs ADD COLUMN orchestration_run_id TEXT"))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
