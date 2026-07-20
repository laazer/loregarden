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
from datetime import datetime, timezone
from uuid import uuid4

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


def _m_branch_triage_message_status(conn: Connection) -> None:
    """Branch triage turns run in the background, so a message row carries its own
    lifecycle. Existing rows predate async execution and are all settled: default
    'complete' backfills them correctly.
    """
    _add_columns_if_missing(
        conn,
        "branch_triage_messages",
        {
            "status": (
                "ALTER TABLE branch_triage_messages ADD COLUMN status "
                "TEXT NOT NULL DEFAULT 'complete'"
            )
        },
    )


def _m_definition_versioning(conn: Connection) -> None:
    """Agents (studio_agents) and workflow templates become DB-authoritative and
    versioned. Adds a head `version` + `built_in` flag to each, append-only
    `*_versions` snapshot tables, and per-run/per-ticket version pins. Existing
    rows are backfilled to version 1 with a v1 snapshot so history is complete.
    """
    _add_columns_if_missing(
        conn,
        "studio_agents",
        {
            "version": "ALTER TABLE studio_agents ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "built_in": "ALTER TABLE studio_agents ADD COLUMN built_in INTEGER NOT NULL DEFAULT 0",
        },
    )
    _add_columns_if_missing(
        conn,
        "workflow_templates",
        {
            "version": (
                "ALTER TABLE workflow_templates ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
            ),
            "built_in": (
                "ALTER TABLE workflow_templates ADD COLUMN built_in INTEGER NOT NULL DEFAULT 0"
            ),
        },
    )
    _add_columns_if_missing(
        conn,
        "agent_runs",
        {"agent_version": "ALTER TABLE agent_runs ADD COLUMN agent_version INTEGER"},
    )
    _add_columns_if_missing(
        conn,
        "workflow_instances",
        {"template_version": "ALTER TABLE workflow_instances ADD COLUMN template_version INTEGER"},
    )

    if not _table_exists(conn, "studio_agent_versions"):
        conn.execute(
            text(
                """
                CREATE TABLE studio_agent_versions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT '',
                    change_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(agent_id) REFERENCES studio_agents(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_studio_agent_versions_agent_version "
                "ON studio_agent_versions (agent_id, version)"
            )
        )
    if not _table_exists(conn, "workflow_template_versions"):
        conn.execute(
            text(
                """
                CREATE TABLE workflow_template_versions (
                    id TEXT PRIMARY KEY,
                    template_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT '',
                    change_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(template_id) REFERENCES workflow_templates(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_workflow_template_versions_template_version "
                "ON workflow_template_versions (template_id, version)"
            )
        )

    # Backfill a v1 snapshot for every pre-existing row so version history is
    # complete from the migration forward (mirrors _m_clear_classify... row work).
    # Guarded: an old-schema DB may not have these tables yet.
    agent_cols = [
        "slug",
        "name",
        "description",
        "role_body",
        "adapter",
        "default_model",
        "timeout",
        "default_skill",
        "mcp_enabled",
        "mcp_tools_json",
        "gate_checks_json",
        "handoff_checks_json",
        "built_in",
    ]
    agent_rows = (
        conn.execute(text(f"SELECT id, {', '.join(agent_cols)} FROM studio_agents")).mappings()
        if _table_exists(conn, "studio_agents")
        else []
    )
    for row in agent_rows:
        if conn.execute(
            text("SELECT 1 FROM studio_agent_versions WHERE agent_id=:aid AND version=1"),
            {"aid": row["id"]},
        ).fetchone():
            continue
        snapshot = {col: row[col] for col in agent_cols}
        conn.execute(
            text(
                # change_note is written explicitly rather than left to the column
                # DEFAULT: init_db runs SQLModel.create_all before migrations, so
                # these tables are usually created from the model, where the Python
                # default renders as NOT NULL with no DDL default. The CREATE TABLE
                # below is then a no-op and an omitted column violates NOT NULL.
                "INSERT INTO studio_agent_versions "
                "(id, agent_id, version, snapshot_json, created_by, change_note, created_at) "
                "VALUES (:id, :aid, 1, :snap, 'migration', '', :now)"
            ),
            {
                "id": str(uuid4()),
                "aid": row["id"],
                "snap": json.dumps(snapshot),
                "now": datetime.now(timezone.utc),
            },
        )

    tpl_cols = ["slug", "name", "description", "stages_json", "transitions_json", "source_path"]
    tpl_rows = (
        conn.execute(text(f"SELECT id, {', '.join(tpl_cols)} FROM workflow_templates")).mappings()
        if _table_exists(conn, "workflow_templates")
        else []
    )
    for row in tpl_rows:
        built_in = 0 if str(row["source_path"] or "").startswith("studio:") else 1
        conn.execute(
            text("UPDATE workflow_templates SET built_in=:b WHERE id=:id"),
            {"b": built_in, "id": row["id"]},
        )
        if conn.execute(
            text("SELECT 1 FROM workflow_template_versions WHERE template_id=:tid AND version=1"),
            {"tid": row["id"]},
        ).fetchone():
            continue
        snapshot = {col: row[col] for col in tpl_cols}
        snapshot["built_in"] = built_in
        conn.execute(
            text(
                # Explicit change_note, same reason as the agent backfill above.
                "INSERT INTO workflow_template_versions "
                "(id, template_id, version, snapshot_json, created_by, change_note, created_at) "
                "VALUES (:id, :tid, 1, :snap, 'migration', '', :now)"
            ),
            {
                "id": str(uuid4()),
                "tid": row["id"],
                "snap": json.dumps(snapshot),
                "now": datetime.now(timezone.utc),
            },
        )


def _m_agent_run_changed_paths(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "changed_paths_json": (
                "ALTER TABLE agent_runs ADD COLUMN changed_paths_json TEXT NOT NULL DEFAULT '[]'"
            )
        },
    )


def _m_artifact_evidence(conn: Connection) -> None:
    _add_columns_if_missing(
        conn,
        "artifacts",
        {
            "evidence_kind": (
                "ALTER TABLE artifacts ADD COLUMN evidence_kind TEXT NOT NULL DEFAULT ''"
            ),
            "commit_sha": "ALTER TABLE artifacts ADD COLUMN commit_sha TEXT NOT NULL DEFAULT ''",
        },
    )


def _snapshot_template_version(
    conn: Connection, template_id: str, version: int, change_note: str
) -> None:
    """Record a template version snapshot, matching what a Studio edit writes.

    Columns are listed explicitly rather than relying on defaults: create_all
    builds these tables from the models, where a Python default renders as NOT
    NULL with no DDL default.
    """
    if not _table_exists(conn, "workflow_template_versions"):
        return
    snapshot = (
        conn.execute(
            text(
                "SELECT slug, name, description, stages_json, transitions_json, source_path, "
                "built_in FROM workflow_templates WHERE id=:id"
            ),
            {"id": template_id},
        )
        .mappings()
        .fetchone()
    )
    conn.execute(
        text(
            "INSERT INTO workflow_template_versions "
            "(id, template_id, version, snapshot_json, created_by, change_note, created_at) "
            "VALUES (:id, :tid, :v, :snap, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "tid": template_id,
            "v": version,
            "snap": json.dumps(dict(snapshot)),
            "note": change_note,
            "now": datetime.now(timezone.utc),
        },
    )


_EVIDENCE_TEMPLATE = "studio-loregarden-tdd-v3"
_EVIDENCE_STAGE = "verify"
_EVIDENCE_TOOL = "loregarden_attach_evidence"


def _m_require_verify_evidence(conn: Connection) -> None:
    """Make the verify stage produce a verdict rather than assert one.

    verify exists to check a stage's done-claim, so a verify that advances
    without recording what it found is the same unverified pass it was added to
    prevent. Requiring verify_verdict is the narrowest place to start: it is the
    one stage whose entire job is producing that artifact.

    Deliberately not requiring real_surface anywhere yet. That would block
    implement until agents habitually capture output from the running system,
    which is a behaviour change to roll out once verify is proven.
    """
    if not _table_exists(conn, "workflow_templates"):
        return

    # Grant the tool first. A stage required to record evidence without the tool
    # to record it is blocked with no way to comply, and agent tool grants are
    # stored per row rather than read from the defaults.
    if _table_exists(conn, "studio_agents"):
        for row in (
            conn.execute(text("SELECT id, mcp_tools_json FROM studio_agents")).mappings().all()
        ):
            tools = json.loads(row["mcp_tools_json"] or "[]")
            if not isinstance(tools, list) or _EVIDENCE_TOOL in tools:
                continue
            tools.append(_EVIDENCE_TOOL)
            conn.execute(
                text("UPDATE studio_agents SET mcp_tools_json=:t WHERE id=:id"),
                {"t": json.dumps(tools), "id": row["id"]},
            )

    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _EVIDENCE_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    verify = next((s for s in stages if s.get("key") == _EVIDENCE_STAGE), None)
    if verify is None or verify.get("required_evidence"):
        return
    verify["required_evidence"] = ["verify_verdict"]

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Verify must record a verdict")


_IMPLEMENT_STAGE = "implement"


def _m_require_implement_real_surface(conn: Connection) -> None:
    """Make implement show the change working, not just that its tests pass.

    Green tests say the code does what its tests say. They do not say the
    feature works on the surface a user touches, and that second claim is the
    one nothing has ever checked.

    Light work is exempt at gate time rather than here: triage decides that per
    ticket, so the requirement stays on the stage and the waiver is applied when
    it runs.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _EVIDENCE_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    implement = next((s for s in stages if s.get("key") == _IMPLEMENT_STAGE), None)
    if implement is None or implement.get("required_evidence"):
        return
    implement["required_evidence"] = ["real_surface"]

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Implement must show it working")


_REVIEW_TEMPLATE = "studio-loregarden-tdd-v3"
_REVIEW_KEY = "review"
# One lane per lens. They run concurrently and any rejection sends the work back,
# so these are independent readings of the same diff rather than a chain: a
# reviewer looking for coupling is not also looking for injection.
_REVIEW_LANES = [
    ("architecture_reviewer", ""),
    ("static_qa", ""),
    ("security_reviewer", ""),
]


def _m_parallel_review_in_v3(conn: Connection) -> None:
    """Review the diff from several angles at once instead of one.

    The stage was a classify with a single default route, so every ticket got
    exactly one reviewer and whatever that reviewer was not looking for went
    unreviewed.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _REVIEW_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    review = next((s for s in stages if s.get("key") == _REVIEW_KEY), None)
    if review is None or review.get("stage_type") == "parallel":
        return

    review["stage_type"] = "parallel"
    review["parallel_agents"] = [
        {"agent_id": agent_id, "skill_name": skill} for agent_id, skill in _REVIEW_LANES
    ]
    # A parallel stage resolves its agents from parallel_agents; leaving the old
    # single route behind would be a second, contradictory answer to the same
    # question.
    review["classify_routes"] = []

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Parallel multi-angle review")


_VERIFY_TEMPLATE = "studio-loregarden-tdd-v3"
_VERIFY_AFTER = "implement"
_VERIFY_KEY = "verify"


def _m_verify_stage_in_v3(conn: Connection) -> None:
    """Put an independent verify stage between implement and review on v3.

    A stage closing on its own outcome=pass is what verify exists to check, so it
    sits directly after the stage that makes the claim and routes back to it on a
    refusal. Both the light and heavy triage paths converge on implement, so one
    stage covers both.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json FROM workflow_templates WHERE slug=:s"
            ),
            {"s": _VERIFY_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {s.get("key"): s for s in stages}
    if _VERIFY_KEY in by_key or _VERIFY_AFTER not in by_key:
        return  # already wired, or this template does not have the anchor stage

    anchor_order = int(by_key[_VERIFY_AFTER].get("order") or 0)
    for stage in stages:
        if int(stage.get("order") or 0) > anchor_order:
            stage["order"] = int(stage["order"]) + 1
    stages.append(
        {
            "key": _VERIFY_KEY,
            "name": "Verify",
            "agent_id": "verifier",
            "skill_name": "verify",
            "optional": False,
            "order": anchor_order + 1,
            "stage_type": "verify",
            # Light work skips verification. Triage already decided the ticket was
            # trivial enough to branch past planning; demanding runtime proof of a
            # typo fix spends more than the check is worth.
            "skip_when": "routed_as_light_work",
            "classify_routes": [],
            "parallel_agents": [],
            "gate_commands": [],
            "gate_required": False,
            "model": "",
        }
    )
    stages.sort(key=lambda s: int(s.get("order") or 0))

    # Re-point whatever implement used to advance to, then add the verdict edges.
    transitions = json.loads(row["transitions_json"] or "[]")
    downstream = ""
    for item in transitions:
        if item.get("from") == _VERIFY_AFTER and item.get("when", "") in {"", "pass", "default"}:
            downstream = item.get("to", "")
            item["to"] = _VERIFY_KEY
    if downstream:
        transitions.append({"from": _VERIFY_KEY, "to": downstream, "when": "pass"})
    transitions.append({"from": _VERIFY_KEY, "to": _VERIFY_AFTER, "when": "reject"})

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(stages),
            "tr": json.dumps(transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    _snapshot_template_version(conn, row["id"], new_version, "Verify stage after implement")
    _backfill_verify_into_instances(
        conn, row["id"], {s["key"]: int(s.get("order") or 0) for s in stages}
    )


def _backfill_verify_into_instances(
    conn: Connection, template_id: str, stage_orders: dict[str, int]
) -> None:
    """Add the new stage to live instances without stranding or rewinding them.

    A required stage inserted mid-pipeline is PENDING for every in-flight ticket,
    which both blocks DONE (nothing ever resolves it) and pulls the cursor
    backwards, since the orchestrator runs the earliest pending stage.

    Whether a ticket has already passed the insertion point is decided by where
    its cursor sits, not by one stage's recorded status: a ticket can reach
    review with implement left un-marked after a reroute, and keying off that
    would hand it a pending verify it should never run.
    """
    if not _table_exists(conn, "workflow_instances"):
        return
    verify_order = stage_orders.get(_VERIFY_KEY, 0)
    rows = (
        conn.execute(
            text(
                "SELECT id, stages_json, current_stage_key FROM workflow_instances "
                "WHERE template_id=:tid"
            ),
            {"tid": template_id},
        )
        .mappings()
        .fetchall()
    )
    for row in rows:
        entries = json.loads(row["stages_json"] or "[]")
        if any(e.get("key") == _VERIFY_KEY for e in entries):
            continue
        cursor_order = stage_orders.get(row["current_stage_key"] or "", 0)
        already_past = cursor_order > verify_order
        entries.append({"key": _VERIFY_KEY, "status": "wont_do" if already_past else "pending"})
        conn.execute(
            text("UPDATE workflow_instances SET stages_json=:st WHERE id=:id"),
            {"st": json.dumps(entries), "id": row["id"]},
        )


# Keywords that mark a ticket trivial enough to skip planning. Deliberately rare
# words: the classifier is a bag-of-words match over title + description +
# acceptance criteria, so a term that shows up incidentally in a risky ticket
# would skip planning for it. HEAVY is the default, so an unmatched ticket keeps
# the full pipeline — rigor ratchets up, never quietly down.
_LIGHT_WORK_KEYWORDS = [
    "typo",
    "docs",
    "documentation",
    "changelog",
    "comment",
    "formatting",
    "lint",
]

_RIGOR_TRIAGE_TEMPLATE = "studio-loregarden-tdd-v3"
_RIGOR_LIGHT_TARGET = "test-design"


def _m_light_heavy_rigor_triage(conn: Connection) -> None:
    """Scale pipeline rigor by change risk on the loregarden TDD v3 template.

    Turns `triage` into a classify stage whose light route branches past
    plan/ui-design/spec, and lets `spec` skip itself when the ticket already
    carries acceptance criteria. Composed from the route `to_stage` and stage
    `skip_when` primitives; no engine change.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:slug"),
            {"slug": _RIGOR_TRIAGE_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {stage.get("key"): stage for stage in stages}
    triage, spec = by_key.get("triage"), by_key.get("spec")
    if not triage or not spec or _RIGOR_LIGHT_TARGET not in by_key:
        return
    if triage.get("stage_type") == "classify":
        return  # already reshaped

    agent_id = triage.get("agent_id") or "ticket_scoper"
    skill_name = triage.get("skill_name", "")
    triage["stage_type"] = "classify"
    triage["classify_routes"] = [
        {
            "languages": [],
            "specialties": _LIGHT_WORK_KEYWORDS,
            "agent_id": agent_id,
            "skill_name": skill_name,
            "default": False,
            "to_stage": _RIGOR_LIGHT_TARGET,
        },
        {
            "languages": [],
            "specialties": [],
            "agent_id": agent_id,
            "skill_name": skill_name,
            "default": True,
            "to_stage": "",
        },
    ]
    spec["skip_when"] = "has_acceptance_criteria"

    # Bump the version so this reshape is auditable alongside Studio edits. The
    # pre-existing v1 snapshot still holds the linear shape, so history is intact.
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:stages, version=:v WHERE id=:id"),
        {"stages": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "LIGHT/HEAVY rigor triage")


_REFACTOR_TEMPLATE = "studio-loregarden-tdd-v3"
_REFACTOR_SKILL = "refactor"
# One route per implementer, because the skill is orthogonal to who runs it: a
# refactor still belongs to whoever owns that half of the codebase.
#
# Backend is listed first on purpose. A route's specialties are OR-matched, so
# the frontend lane also fires on a bare refactor word and would win every tie
# on position alone — sending backend refactors to the frontend agent. Ordered
# this way a tie falls to backend, and a genuinely frontend refactor still wins
# outright on the extra specialty hit.
_REFACTOR_ROUTES = [
    {
        "languages": [],
        "specialties": ["refactor"],
        "agent_id": "backend_implementer",
        "skill_name": _REFACTOR_SKILL,
        "default": False,
        "to_stage": "",
    },
    {
        "languages": ["typescript", "javascript"],
        "specialties": ["refactor", "frontend"],
        "agent_id": "frontend_implementer",
        "skill_name": _REFACTOR_SKILL,
        "default": False,
        "to_stage": "",
    },
]


def _m_refactor_skill_routes(conn: Connection) -> None:
    """Give restructuring work a method instead of leaving it to improvisation.

    Refactors ran through the plain implementer route, so nothing told an agent
    to establish a behavior baseline or find every reference before moving code.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _REFACTOR_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    implement = next((s for s in stages if s.get("key") == _IMPLEMENT_STAGE), None)
    if implement is None:
        return
    routes = implement.get("classify_routes") or []
    if any(r.get("skill_name") == _REFACTOR_SKILL for r in routes):
        return

    # Last among the scored lanes, immediately before the fallback. Specialties
    # are OR-matched, so the frontend refactor lane also fires on a bare "modal"
    # or "tab" — ahead of the plain frontend lane it would tie on that single
    # hit and steal ordinary UI work on position alone. Placed behind it, a
    # refactor lane can only win by matching strictly more, which takes an
    # actual refactor word.
    insert_at = next((i for i, r in enumerate(routes) if r.get("default")), len(routes))
    implement["classify_routes"] = routes[:insert_at] + _REFACTOR_ROUTES + routes[insert_at:]

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(
        conn, row["id"], new_version, "Route refactors to the refactor skill"
    )


_PLAN_TEMPLATE = "studio-loregarden-tdd-v3"
_PLAN_STAGE = "plan"
_PLAN_SKILL = "plan"


def _m_plan_skill_on_plan_stage(conn: Connection) -> None:
    """Point the plan stage at the skill that tells it to attach its plan.

    The stage declared no skill, so nothing told the planner where its output
    should go, and the plan survived only inside a run-log transcript no later
    stage reads.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _PLAN_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    plan = next((s for s in stages if s.get("key") == _PLAN_STAGE), None)
    # An operator who set their own skill here meant it; only fill the gap.
    if plan is None or (plan.get("skill_name") or "").strip():
        return
    plan["skill_name"] = _PLAN_SKILL

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Plan stage attaches its plan")


_HYPERPLAN_TEMPLATE = "studio-loregarden-tdd-v3"
_HYPERPLAN_STAGE = "plan"
_HYPERPLAN_SYNTHESIS_KEY = "plan-synthesis"
# Three lenses, one agent. They differ by skill rather than by role because the
# argument each makes is a way of reading the ticket, not a different job — and
# three near-identical role bodies would drift apart the moment one is edited.
_HYPERPLAN_LANES = [
    ("planner", "plan-simplest"),
    ("planner", "plan-risk"),
    ("planner", "plan-seams"),
]


def _m_adversarial_planning(conn: Connection) -> None:
    """Plan from three angles at once, then reconcile them into one plan.

    A single planner's first plausible approach became the plan, and nothing
    argued the other side of it. Fanning out costs three runs; the synthesis
    stage is what makes them worth more than one, by forcing the disagreements
    to be settled before spec and test-design build on either answer.
    """
    if not _table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json "
                "FROM workflow_templates WHERE slug=:s"
            ),
            {"s": _HYPERPLAN_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {s.get("key"): s for s in stages}
    plan = by_key.get(_HYPERPLAN_STAGE)
    if plan is None or _HYPERPLAN_SYNTHESIS_KEY in by_key:
        return
    if plan.get("stage_type") == "parallel":
        return

    plan["stage_type"] = "parallel"
    plan["parallel_agents"] = [
        {"agent_id": agent_id, "skill_name": skill} for agent_id, skill in _HYPERPLAN_LANES
    ]
    # Each lane names its own lens, so the stage-level skill is now dead weight.
    # Only the value 0031 set is cleared: anything else was an operator's choice,
    # and undoing just our own default is the same restraint 0019 used.
    if (plan.get("skill_name") or "") == _PLAN_SKILL:
        plan["skill_name"] = ""

    plan_order = int(plan.get("order") or 0)
    for stage in stages:
        if int(stage.get("order") or 0) > plan_order:
            stage["order"] = int(stage["order"]) + 1
    stages.append(
        {
            "key": _HYPERPLAN_SYNTHESIS_KEY,
            "name": "Plan synthesis",
            "agent_id": "planner",
            "skill_name": "plan-synthesis",
            "optional": False,
            "order": plan_order + 1,
            "stage_type": "agent",
            "classify_routes": [],
            "parallel_agents": [],
            "gate_commands": [],
            "gate_required": False,
            "model": "",
        }
    )
    stages.sort(key=lambda s: int(s.get("order") or 0))

    # Whatever plan advanced to now sits behind synthesis, so the settled plan
    # exists before anything downstream reads one.
    transitions = json.loads(row["transitions_json"] or "[]")
    downstream = ""
    for item in transitions:
        if item.get("from") == _HYPERPLAN_STAGE and item.get("when", "") in {"", "pass", "default"}:
            downstream = item.get("to", "")
            item["to"] = _HYPERPLAN_SYNTHESIS_KEY
    if downstream:
        transitions.append({"from": _HYPERPLAN_SYNTHESIS_KEY, "to": downstream, "when": "pass"})

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(stages),
            "tr": json.dumps(transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    _snapshot_template_version(conn, row["id"], new_version, "Adversarial planning")


def _m_run_messages_table(conn: Connection) -> None:
    """Queue for operator messages sent to a run already in flight."""
    if _table_exists(conn, "run_messages"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE run_messages (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                FOREIGN KEY(run_id) REFERENCES agent_runs(id),
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
            """
        )
    )
    # The bridge polls undelivered messages for one run on every loop pass.
    conn.execute(text("CREATE INDEX ix_run_messages_run_id ON run_messages (run_id)"))


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
    ("0021_branch_triage_message_status", _m_branch_triage_message_status),
    ("0022_definition_versioning", _m_definition_versioning),
    ("0023_light_heavy_rigor_triage", _m_light_heavy_rigor_triage),
    ("0024_agent_run_changed_paths", _m_agent_run_changed_paths),
    ("0025_artifact_evidence", _m_artifact_evidence),
    ("0026_verify_stage_in_v3", _m_verify_stage_in_v3),
    ("0027_parallel_review_in_v3", _m_parallel_review_in_v3),
    ("0028_require_verify_evidence", _m_require_verify_evidence),
    ("0029_require_implement_real_surface", _m_require_implement_real_surface),
    ("0030_refactor_skill_routes", _m_refactor_skill_routes),
    ("0031_plan_skill_on_plan_stage", _m_plan_skill_on_plan_stage),
    ("0032_adversarial_planning", _m_adversarial_planning),
    ("0033_run_messages_table", _m_run_messages_table),
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
