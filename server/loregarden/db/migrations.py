"""Lightweight, version-tracked SQLite migrations.

Replaces the previous ad-hoc chain of ``PRAGMA table_info`` + ``ALTER TABLE``
calls with an ordered registry recorded in a ``schema_migrations`` table. Each
migration still guards its own changes (so it is safe to run against databases
at any prior point in history, including brand-new ones created by
``SQLModel.metadata.create_all``), but now the applied set is tracked, ordered,
and auditable — and future non-idempotent migrations can rely on run-once
semantics.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

Migration = Callable[[Connection], None]


def _columns(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row is not None


def _add_columns_if_missing(conn: Connection, table: str, columns: dict[str, str]) -> None:
    """Add each ``name -> ALTER statement`` whose column is absent from ``table``."""
    if not _table_exists(conn, table):
        return
    existing = _columns(conn, table)
    for name, statement in columns.items():
        if name not in existing:
            conn.execute(text(statement))


def _m_workspace_workflow_override(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "workspaces",
        {
            "workflow_override_json": (
                "ALTER TABLE workspaces ADD COLUMN workflow_override_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        },
    )


def _m_ticket_columns(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "tickets",
        {
            "work_item_type": (
                "ALTER TABLE tickets ADD COLUMN work_item_type TEXT NOT NULL DEFAULT 'task'"
            ),
            "parent_ticket_id": "ALTER TABLE tickets ADD COLUMN parent_ticket_id TEXT",
            "cycle_id": "ALTER TABLE tickets ADD COLUMN cycle_id TEXT",
            "state_locked": (
                "ALTER TABLE tickets ADD COLUMN state_locked INTEGER NOT NULL DEFAULT 0"
            ),
            "triage_runtime_json": (
                "ALTER TABLE tickets ADD COLUMN triage_runtime_json TEXT NOT NULL DEFAULT '{}'"
            ),
            "workflow_disabled": (
                "ALTER TABLE tickets ADD COLUMN workflow_disabled INTEGER NOT NULL DEFAULT 0"
            ),
            "permission_allowlist_json": (
                "ALTER TABLE tickets ADD COLUMN permission_allowlist_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def _m_workspace_runtime_columns(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "workspaces",
        {
            "orchestration_profile_slug": (
                "ALTER TABLE workspaces ADD COLUMN orchestration_profile_slug "
                "TEXT NOT NULL DEFAULT ''"
            ),
            "cli_adapter": "ALTER TABLE workspaces ADD COLUMN cli_adapter TEXT NOT NULL DEFAULT ''",
            "claude_model": (
                "ALTER TABLE workspaces ADD COLUMN claude_model TEXT NOT NULL DEFAULT ''"
            ),
            "cursor_model": (
                "ALTER TABLE workspaces ADD COLUMN cursor_model TEXT NOT NULL DEFAULT ''"
            ),
            "lmstudio_base_url": (
                "ALTER TABLE workspaces ADD COLUMN lmstudio_base_url TEXT NOT NULL DEFAULT ''"
            ),
            "lmstudio_model": (
                "ALTER TABLE workspaces ADD COLUMN lmstudio_model TEXT NOT NULL DEFAULT ''"
            ),
            "permission_allowlist_json": (
                "ALTER TABLE workspaces ADD COLUMN permission_allowlist_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def _m_approval_columns(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "approvals",
        {
            "run_id": "ALTER TABLE approvals ADD COLUMN run_id TEXT",
            "kind": ("ALTER TABLE approvals ADD COLUMN kind TEXT NOT NULL DEFAULT 'workflow_gate'"),
            "permission_request_id": (
                "ALTER TABLE approvals ADD COLUMN permission_request_id TEXT NOT NULL DEFAULT ''"
            ),
            "tool_name": "ALTER TABLE approvals ADD COLUMN tool_name TEXT NOT NULL DEFAULT ''",
            "tool_input_json": (
                "ALTER TABLE approvals ADD COLUMN tool_input_json TEXT NOT NULL DEFAULT '{}'"
            ),
            "cli_adapter": "ALTER TABLE approvals ADD COLUMN cli_adapter TEXT NOT NULL DEFAULT ''",
            "cli_session_id": (
                "ALTER TABLE approvals ADD COLUMN cli_session_id TEXT NOT NULL DEFAULT ''"
            ),
            "response_json": (
                "ALTER TABLE approvals ADD COLUMN response_json TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )


def _m_agent_run_orchestration_id(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "agent_runs",
        {"orchestration_run_id": "ALTER TABLE agent_runs ADD COLUMN orchestration_run_id TEXT"},
    )


def _m_agent_run_auto_approve(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "auto_approve": "ALTER TABLE agent_runs ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0"
        },
    )


def _m_orchestration_run_columns(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "orchestration_runs",
        {
            "auto_approve": (
                "ALTER TABLE orchestration_runs ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0"
            ),
            "stop_at_stage_key": (
                "ALTER TABLE orchestration_runs ADD COLUMN stop_at_stage_key "
                "TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_triage_messages_table(conn: Connection) -> None:
    if _table_exists(conn, "triage_messages"):
        return
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
    conn.execute(text("CREATE INDEX ix_triage_messages_ticket_id ON triage_messages (ticket_id)"))


def _m_ticket_studio_tables(conn: Connection) -> None:
    if not _table_exists(conn, "ticket_studio_sessions"):
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
                    clarifying_answers_json TEXT NOT NULL DEFAULT '[]',
                    runtime_json TEXT NOT NULL DEFAULT '{}',
                    is_preview INTEGER NOT NULL DEFAULT 0,
                    imported_tickets_json TEXT NOT NULL DEFAULT '[]',
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
                "CREATE INDEX ix_ticket_studio_sessions_workspace_id "
                "ON ticket_studio_sessions (workspace_id)"
            )
        )
        conn.execute(
            text("CREATE INDEX ix_ticket_studio_sessions_status ON ticket_studio_sessions (status)")
        )
    else:
        _add_columns_if_missing(
            conn,
            "ticket_studio_sessions",
            {
                "clarifying_answers_json": (
                    "ALTER TABLE ticket_studio_sessions "
                    "ADD COLUMN clarifying_answers_json TEXT NOT NULL DEFAULT '[]'"
                ),
            },
        )

    if not _table_exists(conn, "ticket_studio_messages"):
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
                "CREATE INDEX ix_ticket_studio_messages_session_id "
                "ON ticket_studio_messages (session_id)"
            )
        )


def _m_ticket_diff_comments(conn: Connection) -> None:
    if _table_exists(conn, "ticket_diff_comments"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE ticket_diff_comments (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                line_kind TEXT NOT NULL DEFAULT 'c',
                content TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX ix_ticket_diff_comments_ticket_id ON ticket_diff_comments (ticket_id)")
    )
    conn.execute(
        text(
            "CREATE INDEX ix_ticket_diff_comments_anchor "
            "ON ticket_diff_comments (ticket_id, file_path, line_index)"
        )
    )


def _m_branch_diff_comments(conn: Connection) -> None:
    if _table_exists(conn, "branch_diff_comments"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE branch_diff_comments (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_index INTEGER NOT NULL,
                line_kind TEXT NOT NULL DEFAULT 'c',
                content TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX ix_branch_diff_comments_workspace_branch "
            "ON branch_diff_comments (workspace_id, branch)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX ix_branch_diff_comments_anchor "
            "ON branch_diff_comments (workspace_id, branch, file_path, line_index)"
        )
    )


def _m_branch_triage_messages(conn: Connection) -> None:
    if _table_exists(conn, "branch_triage_messages"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE branch_triage_messages (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX ix_branch_triage_messages_workspace_branch "
            "ON branch_triage_messages (workspace_id, branch)"
        )
    )


def _m_ticket_studio_preview_state(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "ticket_studio_sessions",
        {
            "is_preview": (
                "ALTER TABLE ticket_studio_sessions "
                "ADD COLUMN is_preview INTEGER NOT NULL DEFAULT 0"
            ),
            "imported_tickets_json": (
                "ALTER TABLE ticket_studio_sessions "
                "ADD COLUMN imported_tickets_json TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def _m_queued_run_failure_columns(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "queued_runs",
        {
            "failure_reason": (
                "ALTER TABLE queued_runs ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''"
            ),
            "last_failed_at": "ALTER TABLE queued_runs ADD COLUMN last_failed_at TEXT",
        },
    )


def _m_agent_model_columns(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "tickets",
        {
            "orchestration_runtime_json": (
                "ALTER TABLE tickets ADD COLUMN orchestration_runtime_json "
                "TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )
    _add_columns_if_missing(
        conn,
        "studio_agents",
        {
            "default_model": (
                "ALTER TABLE studio_agents ADD COLUMN default_model TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_triage_message_run_id(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "triage_messages",
        {
            "run_id": "ALTER TABLE triage_messages ADD COLUMN run_id TEXT",
        },
    )


def _m_agent_run_timeout_override(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "timeout_override_seconds": (
                "ALTER TABLE agent_runs ADD COLUMN timeout_override_seconds INTEGER"
            ),
        },
    )


def _m_approval_checklist(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "approvals",
        {
            "checklist_json": "ALTER TABLE approvals ADD COLUMN checklist_json TEXT NOT NULL DEFAULT '[]'",
        },
    )


def _m_clear_classify_next_agent_backfill(conn: Connection) -> None:
    """Drop next_agent values that reconcile_workflow_state backfilled onto classify stages.

    reconcile_workflow_state used to copy a stage's static agent_id into
    ticket.next_agent for every stage type. On a classify stage that agent_id is
    only the route table's fallback, but resolve_classify_route reads next_agent
    back as a deliberate routing hint and returns before scoring any route --
    pinning the ticket to the fallback agent and making the other routes
    unreachable. The backfill is fixed in workflow_state.py, but tickets that
    already have the value persisted stay pinned, so clear it here and let the
    classifier score them again.

    Only clears where next_agent still equals the classify stage's static
    agent_id (the backfill's signature). A hint pointing anywhere else was set
    deliberately -- by a reject/rework route -- and is left alone.
    """
    for table in ("tickets", "workflow_instances", "workflow_templates"):
        if not _table_exists(conn, table):
            return
    ticket_columns = _columns(conn, "tickets")
    if not {"next_agent", "workflow_stage_key"} <= ticket_columns:
        return

    rows = conn.execute(
        text(
            """
            SELECT t.id, t.workflow_stage_key, t.next_agent, tpl.stages_json
            FROM tickets t
            JOIN workflow_instances inst ON inst.ticket_id = t.id
            JOIN workflow_templates tpl ON tpl.id = inst.template_id
            WHERE COALESCE(t.next_agent, '') != ''
              AND COALESCE(t.workflow_stage_key, '') != ''
            """
        )
    ).fetchall()

    stale: set[str] = set()
    for ticket_id, stage_key, next_agent, stages_json in rows:
        try:
            stages = json.loads(stages_json or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(stages, list):
            continue
        stage = next(
            (s for s in stages if isinstance(s, dict) and s.get("key") == stage_key),
            None,
        )
        if not stage or stage.get("stage_type") != "classify":
            continue
        if next_agent == (stage.get("agent_id") or ""):
            stale.add(ticket_id)

    for ticket_id in stale:
        conn.execute(
            text("UPDATE tickets SET next_agent = '' WHERE id = :id"),
            {"id": ticket_id},
        )


# Ordered registry. Append new migrations here with the next id; never reorder or
# rewrite an id that may already be recorded in a deployed database.
def _m_compatibility_posture(conn: Connection) -> None:
    """Two levels of storage give three levels of control: a ticket's own value, any
    ancestor's (milestones are tickets), else the workspace default. Blank = inherit.
    """
    _add_columns_if_missing(
        conn,
        "workspaces",
        {
            "compatibility_posture": (
                "ALTER TABLE workspaces ADD COLUMN compatibility_posture "
                "TEXT NOT NULL DEFAULT 'internal'"
            )
        },
    )
    _add_columns_if_missing(
        conn,
        "tickets",
        {
            "compatibility_posture": (
                "ALTER TABLE tickets ADD COLUMN compatibility_posture TEXT NOT NULL DEFAULT ''"
            )
        },
    )


MIGRATIONS: list[tuple[str, Migration]] = [
    ("0001_workspace_workflow_override", _m_workspace_workflow_override),
    ("0002_ticket_columns", _m_ticket_columns),
    ("0003_workspace_runtime_columns", _m_workspace_runtime_columns),
    ("0004_approval_columns", _m_approval_columns),
    ("0005_agent_run_orchestration_id", _m_agent_run_orchestration_id),
    ("0006_orchestration_run_columns", _m_orchestration_run_columns),
    ("0007_triage_messages_table", _m_triage_messages_table),
    ("0008_ticket_studio_tables", _m_ticket_studio_tables),
    ("0009_ticket_diff_comments", _m_ticket_diff_comments),
    ("0010_branch_diff_comments", _m_branch_diff_comments),
    ("0011_branch_triage_messages", _m_branch_triage_messages),
    ("0012_agent_run_auto_approve", _m_agent_run_auto_approve),
    ("0013_ticket_studio_preview_state", _m_ticket_studio_preview_state),
    ("0014_queued_run_failure_columns", _m_queued_run_failure_columns),
    ("0015_agent_model_columns", _m_agent_model_columns),
    ("0016_triage_message_run_id", _m_triage_message_run_id),
    ("0017_agent_run_timeout_override", _m_agent_run_timeout_override),
    ("0018_approval_checklist", _m_approval_checklist),
    ("0019_clear_classify_next_agent_backfill", _m_clear_classify_next_agent_backfill),
    ("0020_compatibility_posture", _m_compatibility_posture),
]


def _ensure_migrations_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )


def _applied_ids(conn: Connection) -> set[str]:
    rows = conn.execute(text("SELECT id FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def apply_migrations(engine: Engine) -> list[str]:
    """Apply pending migrations in order. Returns the ids that ran this call."""
    if not str(engine.url).startswith("sqlite"):
        return []
    applied: list[str] = []
    with engine.begin() as conn:
        _ensure_migrations_table(conn)
        already = _applied_ids(conn)
        for migration_id, migrate in MIGRATIONS:
            if migration_id in already:
                continue
            migrate(conn)
            conn.execute(
                text("INSERT INTO schema_migrations (id) VALUES (:id)"),
                {"id": migration_id},
            )
            applied.append(migration_id)
    return applied
