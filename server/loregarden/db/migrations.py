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
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_ids import assert_migration_ids_are_sound
from loregarden.db.migration_utils import (
    add_columns_if_missing,
    index_exists,
    relax_not_null,
    table_columns,
    table_exists,
)
from loregarden.db.migrations_composer import m_composer_commands
from loregarden.db.migrations_doctor import m_agent_run_preflight
from loregarden.db.migrations_git_boundary import (
    m_agent_run_boundary_verdict,
    m_agent_run_git_boundary,
)
from loregarden.db.migrations_handoffs import m_backfill_handoff_artifacts
from loregarden.db.migrations_mcp import (
    m_mcp_server_health,
    m_mcp_server_rate_limit,
    m_mcp_server_tool_catalog,
    m_mcp_server_tool_policy,
    m_mcp_servers_table,
    m_mcp_tool_calls_table,
)
from loregarden.db.migrations_queue import (
    m_global_agent_slots,
    m_lane_entry_dismissed,
    m_lane_entry_kind,
    m_lane_entry_run_options,
    m_orchestration_timeout_override,
    m_per_slot_queues,
)
from loregarden.db.migrations_skills import m_skill_versioning
from loregarden.db.migrations_stage_fanout import m_stage_fanout_groups
from loregarden.db.migrations_templates import (
    m_adversarial_planning,
    m_clear_phantom_skill_names,
    m_ensure_terminal_stage,
    m_light_heavy_rigor_triage,
    m_parallel_review_in_v3,
    m_plan_skill_on_plan_stage,
    m_refactor_skill_routes,
    m_require_implement_real_surface,
    m_require_verify_evidence,
    m_verify_stage_in_v3,
)
from loregarden.db.migrations_ticket_studio import (
    m_reference_repos,
    m_ticket_studio_preview_state,
    m_ticket_studio_tables,
    m_ticket_studio_turn_lifecycle,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

Migration = Callable[[Connection], None]


def _m_workspace_workflow_override(conn: Connection) -> None:
    add_columns_if_missing(
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
    add_columns_if_missing(
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
    add_columns_if_missing(
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


def _m_workspace_effort_columns(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "workspaces",
        {
            "claude_effort": (
                "ALTER TABLE workspaces ADD COLUMN claude_effort TEXT NOT NULL DEFAULT ''"
            ),
            "cursor_effort": (
                "ALTER TABLE workspaces ADD COLUMN cursor_effort TEXT NOT NULL DEFAULT ''"
            ),
            "lmstudio_effort": (
                "ALTER TABLE workspaces ADD COLUMN lmstudio_effort TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_workspace_opencode_columns(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "workspaces",
        {
            "opencode_model": (
                "ALTER TABLE workspaces ADD COLUMN opencode_model TEXT NOT NULL DEFAULT ''"
            ),
            "opencode_effort": (
                "ALTER TABLE workspaces ADD COLUMN opencode_effort TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_approval_columns(conn: Connection) -> None:
    add_columns_if_missing(
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
    add_columns_if_missing(
        conn,
        "agent_runs",
        {"orchestration_run_id": "ALTER TABLE agent_runs ADD COLUMN orchestration_run_id TEXT"},
    )


def _m_agent_run_auto_approve(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "auto_approve": "ALTER TABLE agent_runs ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0"
        },
    )


def _m_orchestration_run_columns(conn: Connection) -> None:
    add_columns_if_missing(
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
    if table_exists(conn, "triage_messages"):
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


def _m_ticket_diff_comments(conn: Connection) -> None:
    if table_exists(conn, "ticket_diff_comments"):
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
    if table_exists(conn, "branch_diff_comments"):
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
    if table_exists(conn, "branch_triage_messages"):
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


def _m_queued_run_failure_columns(conn: Connection) -> None:
    add_columns_if_missing(
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
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "orchestration_runtime_json": (
                "ALTER TABLE tickets ADD COLUMN orchestration_runtime_json "
                "TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )
    add_columns_if_missing(
        conn,
        "studio_agents",
        {
            "default_model": (
                "ALTER TABLE studio_agents ADD COLUMN default_model TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_triage_message_run_id(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "triage_messages",
        {
            "run_id": "ALTER TABLE triage_messages ADD COLUMN run_id TEXT",
        },
    )


def _m_agent_run_timeout_override(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "timeout_override_seconds": (
                "ALTER TABLE agent_runs ADD COLUMN timeout_override_seconds INTEGER"
            ),
        },
    )


def _m_approval_checklist(conn: Connection) -> None:
    add_columns_if_missing(
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
        if not table_exists(conn, table):
            return
    ticket_columns = table_columns(conn, "tickets")
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
    add_columns_if_missing(
        conn,
        "workspaces",
        {
            "compatibility_posture": (
                "ALTER TABLE workspaces ADD COLUMN compatibility_posture "
                "TEXT NOT NULL DEFAULT 'internal'"
            )
        },
    )
    add_columns_if_missing(
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
    add_columns_if_missing(
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
    add_columns_if_missing(
        conn,
        "studio_agents",
        {
            "version": "ALTER TABLE studio_agents ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "built_in": "ALTER TABLE studio_agents ADD COLUMN built_in INTEGER NOT NULL DEFAULT 0",
        },
    )
    add_columns_if_missing(
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
    add_columns_if_missing(
        conn,
        "agent_runs",
        {"agent_version": "ALTER TABLE agent_runs ADD COLUMN agent_version INTEGER"},
    )
    add_columns_if_missing(
        conn,
        "workflow_instances",
        {"template_version": "ALTER TABLE workflow_instances ADD COLUMN template_version INTEGER"},
    )

    if not table_exists(conn, "studio_agent_versions"):
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
    if not table_exists(conn, "workflow_template_versions"):
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
        if table_exists(conn, "studio_agents")
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
        if table_exists(conn, "workflow_templates")
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
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "changed_paths_json": (
                "ALTER TABLE agent_runs ADD COLUMN changed_paths_json TEXT NOT NULL DEFAULT '[]'"
            )
        },
    )


def _m_artifact_evidence(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "artifacts",
        {
            "evidence_kind": (
                "ALTER TABLE artifacts ADD COLUMN evidence_kind TEXT NOT NULL DEFAULT ''"
            ),
            "commit_sha": "ALTER TABLE artifacts ADD COLUMN commit_sha TEXT NOT NULL DEFAULT ''",
        },
    )


def _m_run_messages_table(conn: Connection) -> None:
    """Queue for operator messages sent to a run already in flight."""
    if table_exists(conn, "run_messages"):
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


def _m_queued_run_created_at(conn: Connection) -> None:
    """Add ``created_at`` to ``queued_runs``.

    SQLite forbids a non-constant default (``datetime('now')``) on
    ``ALTER TABLE ... ADD COLUMN``, so add the column nullable and backfill
    existing rows; new rows get their timestamp from the model default.
    """
    if not table_exists(conn, "queued_runs"):
        return
    if "created_at" in table_columns(conn, "queued_runs"):
        return
    add_columns_if_missing(
        conn,
        "queued_runs",
        {"created_at": "ALTER TABLE queued_runs ADD COLUMN created_at TEXT"},
    )
    conn.execute(
        text("UPDATE queued_runs SET created_at = datetime('now') WHERE created_at IS NULL")
    )


def _m_approval_auto_resolution_audit(conn: Connection) -> None:
    """Add ``resolved_by``/``resolving_orchestration_run_id`` to ``approvals``.

    Ticket 164: auto_approve-mode gate resolutions must still create an
    Approval row, distinguishable from a human sign-off — absence of a row
    must never be the record of an auto-approval.
    """
    if not table_exists(conn, "approvals"):
        return
    add_columns_if_missing(
        conn,
        "approvals",
        {
            "resolved_by": "ALTER TABLE approvals ADD COLUMN resolved_by TEXT NOT NULL DEFAULT ''",
            "resolving_orchestration_run_id": (
                "ALTER TABLE approvals ADD COLUMN resolving_orchestration_run_id TEXT"
            ),
        },
    )


def _m_agent_run_handoff_liveness(conn: Connection) -> None:
    """Add ``handoff_accepted_at``/``handoff_pid`` to ``agent_runs``.

    Terminal-handoff runs are created RUNNING with no supervising process, so
    nothing could ever prove one was alive — a never-pasted command left a
    phantom active run that blocked triage chat and the self-improve restart
    watcher until the next server reload. The pasted command now checks in with
    its shell pid; these columns record that check-in for the stale-run reaper.
    """
    if not table_exists(conn, "agent_runs"):
        return
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "handoff_accepted_at": "ALTER TABLE agent_runs ADD COLUMN handoff_accepted_at TEXT",
            "handoff_pid": "ALTER TABLE agent_runs ADD COLUMN handoff_pid INTEGER",
        },
    )


def _m_ticket_enum_values(conn: Connection) -> None:
    """Rewrite ``tickets.state``/``tickets.workflow_stage_status`` from enum *names*
    to enum *values*.

    Both columns were plain ``Field`` declarations, so SQLAlchemy persisted the member
    name (``BLOCKED``), while every enum column beside them — ``tickets.work_item_type``,
    ``orchestration_runs.status`` — uses ``_str_enum_column`` and persists the value
    (``blocked``). One table, two conventions: anything writing the obvious lowercase
    form out of band produced a row the ORM could not read back, and because the enum
    is resolved on load, a *single* such row raised LookupError on every SELECT over
    tickets — taking down each endpoint that lists them, not just that ticket's.

    Names are hardcoded rather than read off the enums so a later member rename cannot
    retroactively change what this migration does. The mapping is name -> value only,
    so rows already stored as values do not match and are left untouched.
    """
    if not table_exists(conn, "tickets"):
        return
    columns = table_columns(conn, "tickets")
    renames: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
        (
            "state",
            "UPDATE tickets SET state = :value WHERE state = :name",
            (
                ("BACKLOG", "backlog"),
                ("IN_PROGRESS", "in_progress"),
                ("BLOCKED", "blocked"),
                ("DONE", "done"),
                ("WONT_DO", "wont_do"),
            ),
        ),
        (
            "workflow_stage_status",
            "UPDATE tickets SET workflow_stage_status = :value WHERE workflow_stage_status = :name",
            (
                ("PENDING", "pending"),
                ("RUNNING", "running"),
                ("BLOCKED", "blocked"),
                ("AWAITING", "awaiting"),
                ("DONE", "done"),
                ("WONT_DO", "wont_do"),
            ),
        ),
    )
    for column, statement, pairs in renames:
        if column not in columns:
            continue
        for name, value in pairs:
            conn.execute(text(statement), {"name": name, "value": value})


def _m_run_approval_event_enum_values(conn: Connection) -> None:
    """Finish what 0042 started: the last three columns that stored enum *names*.

    ``agent_runs.status``, ``approvals.status`` and ``domain_events.type`` were the
    remaining plain ``Field`` declarations, so the schema still had two conventions
    and a reader still could not tell which one a given column used. That ambiguity
    is the bug — 0042 fixed the two columns that happened to break first.

    ``agent_runs.status`` is the one that mattered: it is the column an operator or
    agent is most likely to correct by hand ("mark this stuck run failed"), and one
    unreadable row would fail every SELECT over runs.

    Note ``EventType`` values are PascalCase, so this is a rename rather than a
    case-fold. As in 0042 the pairs are hardcoded, and the mapping is name -> value
    only, so already-converted rows do not match and re-running is a no-op. The loop
    is duplicated from 0042 rather than shared, because an applied migration's code
    should not change under it.
    """
    renames: tuple[tuple[str, str, str, tuple[tuple[str, str], ...]], ...] = (
        (
            "agent_runs",
            "status",
            "UPDATE agent_runs SET status = :value WHERE status = :name",
            (
                ("QUEUED", "queued"),
                ("RUNNING", "running"),
                ("AWAITING_PERMISSION", "awaiting_permission"),
                ("SUCCEEDED", "succeeded"),
                ("FAILED", "failed"),
                ("CANCELLED", "cancelled"),
            ),
        ),
        (
            "approvals",
            "status",
            "UPDATE approvals SET status = :value WHERE status = :name",
            (
                ("PENDING", "pending"),
                ("APPROVED", "approved"),
                ("REJECTED", "rejected"),
            ),
        ),
        (
            "domain_events",
            "type",
            "UPDATE domain_events SET type = :value WHERE type = :name",
            (
                ("TICKET_CREATED", "TicketCreated"),
                ("TICKET_STATE_CHANGED", "TicketStateChanged"),
                ("WORKFLOW_STARTED", "WorkflowStarted"),
                ("STAGE_STARTED", "StageStarted"),
                ("STAGE_COMPLETED", "StageCompleted"),
                ("AGENT_RUN_STARTED", "AgentRunStarted"),
                ("AGENT_RUN_COMPLETED", "AgentRunCompleted"),
                ("ORCHESTRATION_RUN_STARTED", "OrchestrationRunStarted"),
                ("ORCHESTRATION_RUN_COMPLETED", "OrchestrationRunCompleted"),
                ("ARTIFACT_CREATED", "ArtifactCreated"),
                ("APPROVAL_REQUESTED", "ApprovalRequested"),
                ("APPROVAL_RESOLVED", "ApprovalResolved"),
            ),
        ),
    )
    for table, column, statement, pairs in renames:
        if not table_exists(conn, table) or column not in table_columns(conn, table):
            continue
        for name, value in pairs:
            conn.execute(text(statement), {"name": name, "value": value})


def _m_ticket_scope_reroute_agent(conn: Connection) -> None:
    """Pin for "run this sibling implementer next" when a scoped implementer is
    denied a write onto the other's subtree. Blank on every existing row — no
    reroute is in flight — so an empty-string default backfills correctly.
    """
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "scope_reroute_agent": (
                "ALTER TABLE tickets ADD COLUMN scope_reroute_agent TEXT NOT NULL DEFAULT ''"
            )
        },
    )


def _m_ticket_integration_review(conn: Connection) -> None:
    """Flag for a synthesized parent-level integration-review work item. False on
    every existing row — none were review items before this — so a 0 default
    backfills correctly. child_sort_key uses it to run these last among siblings.
    """
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "is_integration_review": (
                "ALTER TABLE tickets ADD COLUMN is_integration_review INTEGER NOT NULL DEFAULT 0"
            )
        },
    )


def _m_ticket_dependencies_table(conn: Connection) -> None:
    """Directed best-effort "waits for" edges between tickets (ticket_id depends
    on depends_on_ticket_id). Created empty; nothing to backfill."""
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS ticket_dependencies ("
            "id TEXT PRIMARY KEY, "
            "ticket_id TEXT NOT NULL, "
            "depends_on_ticket_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "created_by TEXT NOT NULL DEFAULT ''"
            ")"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ticket_dependencies_ticket_id "
            "ON ticket_dependencies (ticket_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ticket_dependencies_depends_on "
            "ON ticket_dependencies (depends_on_ticket_id)"
        )
    )


def _m_ticket_tags(conn: Connection) -> None:
    """Free-form ticket labels as a JSON array. Every existing ticket is untagged,
    so an empty-array default backfills correctly."""
    add_columns_if_missing(
        conn,
        "tickets",
        {"tags_json": "ALTER TABLE tickets ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'"},
    )


def _m_ticket_relations_table(conn: Connection) -> None:
    """Symmetric non-blocking "see also" edges between tickets, stored once per
    pair in canonical id order. Created empty; nothing to backfill."""
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS ticket_relations ("
            "id TEXT PRIMARY KEY, "
            "ticket_id TEXT NOT NULL, "
            "related_ticket_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "created_by TEXT NOT NULL DEFAULT ''"
            ")"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ticket_relations_ticket_id "
            "ON ticket_relations (ticket_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ticket_relations_related_ticket_id "
            "ON ticket_relations (related_ticket_id)"
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_relations_pair "
            "ON ticket_relations (ticket_id, related_ticket_id)"
        )
    )


def _m_chat_message_parts(conn: Connection) -> None:
    """Persist ordered ChatPart JSON on stored chat messages."""
    add_columns_if_missing(
        conn,
        "branch_triage_messages",
        {
            "parts_json": (
                "ALTER TABLE branch_triage_messages ADD COLUMN parts_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )
    add_columns_if_missing(
        conn,
        "ticket_studio_messages",
        {
            "parts_json": (
                "ALTER TABLE ticket_studio_messages ADD COLUMN parts_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def _m_run_cancel_requested(conn: Connection) -> None:
    """Cooperative cancel flag for in-flight agent and orchestration runs.

    Runs execute on fire-and-forget daemon threads with no process registry, so
    the API can only set a DB flag that the executor/orchestrator polls — the
    same shape as run steering. Nullable TEXT timestamps match handoff_accepted_at.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "cancel_requested_at": "ALTER TABLE agent_runs ADD COLUMN cancel_requested_at TEXT",
        },
    )
    add_columns_if_missing(
        conn,
        "orchestration_runs",
        {
            "cancel_requested_at": (
                "ALTER TABLE orchestration_runs ADD COLUMN cancel_requested_at TEXT"
            ),
        },
    )


def _m_baxter_chat_tables(conn: Connection) -> None:
    """Persist Home Baxter conversations as named sessions.

    Home chat previously lived only in React state and replayed its history from
    the client each turn, so a reload lost the thread and the archive had nothing
    to list. ``triage_messages`` gains the ``parts_json`` its siblings got in
    0048 so ticket triage primitives survive a reload too.
    """
    if not table_exists(conn, "baxter_chat_sessions"):
        conn.execute(
            text(
                """
                CREATE TABLE baxter_chat_sessions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_baxter_chat_sessions_workspace_updated "
                "ON baxter_chat_sessions (workspace_id, updated_at)"
            )
        )
    if not table_exists(conn, "baxter_chat_messages"):
        conn.execute(
            text(
                """
                CREATE TABLE baxter_chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'complete',
                    parts_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(session_id) REFERENCES baxter_chat_sessions(id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_baxter_chat_messages_session_created "
                "ON baxter_chat_messages (session_id, created_at)"
            )
        )
    add_columns_if_missing(
        conn,
        "triage_messages",
        {
            "parts_json": (
                "ALTER TABLE triage_messages ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]'"
            ),
        },
    )


def _m_chat_turn_thinking(conn: Connection) -> None:
    """A place to keep a chat turn's reasoning while the turn is still running.

    Keyed by the ``active_turn_id`` every chat surface already publishes, so one
    table covers Home chat, branch triage, ticket triage and the studio without
    a column on each of their four message tables. Rows are deleted as their
    turn settles — the transcript is folded into the message's ``parts_json``
    then — so this table is empty whenever nothing is running.
    """
    if table_exists(conn, "chat_turn_thinking"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE chat_turn_thinking (
                turn_id TEXT PRIMARY KEY,
                content TEXT NOT NULL DEFAULT '',
                activity TEXT NOT NULL DEFAULT '',
                seq INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX ix_chat_turn_thinking_updated ON chat_turn_thinking (updated_at)")
    )


def _m_chat_turn_answer(conn: Connection) -> None:
    """Stream the reply as well as the reasoning.

    A read-only turn — the advisory chat fallbacks and every Ticket Studio
    scoper turn — emits an empty thinking block: the reply text is the only
    thing that actually streams. Without somewhere to put it those surfaces get
    a live panel with nothing in it.
    """
    add_columns_if_missing(
        conn,
        "chat_turn_thinking",
        {"answer": "ALTER TABLE chat_turn_thinking ADD COLUMN answer TEXT NOT NULL DEFAULT ''"},
    )


def _m_btw_exchanges(conn: Connection) -> None:
    """Somewhere to keep a question asked while a run is still working.

    Not a column on ``run_messages``: that channel is imperative, one-way, and
    keyed to a run that must exist and be steerable. An aside expects an answer,
    is answered by a different agent than the one it is about, and stays valid
    when nothing is running at all.
    """
    if table_exists(conn, "btw_exchanges"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE btw_exchanges (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                observed_run_id TEXT,
                question TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '',
                escalated_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                answered_at TEXT
            )
            """
        )
    )
    conn.execute(text("CREATE INDEX ix_btw_exchanges_ticket ON btw_exchanges (ticket_id)"))
    conn.execute(text("CREATE INDEX ix_btw_exchanges_status ON btw_exchanges (status)"))
    conn.execute(text("CREATE INDEX ix_btw_exchanges_run ON btw_exchanges (observed_run_id)"))
    conn.execute(text("CREATE INDEX ix_btw_exchanges_created ON btw_exchanges (created_at)"))


def _m_git_automation(conn: Connection) -> None:
    """Columns for running a queued ticket in a worktree and landing its work.

    Three separate gaps closed together because they are one feature:

    * ``agent_runs.worktree_id`` — orchestration has always assigned this
      attribute after creating a worktree, but the column never existed, so
      SQLModel dropped it on flush and every worktree was orphaned the moment
      the run finished.
    * ``worktrees.branch`` / ``created_at`` / ``cleaned_at`` — the merge path
      guessed the branch from the worktree's directory name (which is named
      after the run and is not a ref), and two API handlers read timestamps
      that were never stored.
    * ``tickets.git_automation_json`` — per-ticket override of the workspace
      policy. Empty string, not an empty object, so "inherit" stays
      distinguishable from "explicitly everything off".

    The worktrees and conflict_reports tables predate this registry — they have
    only ever been created by ``SQLModel.metadata.create_all`` — so this is
    their first migration. ``add_columns_if_missing`` no-ops on a fresh
    database where create_all already produced the new shape.
    """
    add_columns_if_missing(
        conn,
        "agent_runs",
        {
            "worktree_id": "ALTER TABLE agent_runs ADD COLUMN worktree_id TEXT",
        },
    )
    add_columns_if_missing(
        conn,
        "worktrees",
        {
            "branch": "ALTER TABLE worktrees ADD COLUMN branch TEXT NOT NULL DEFAULT ''",
            "created_at": "ALTER TABLE worktrees ADD COLUMN created_at TEXT",
            "cleaned_at": "ALTER TABLE worktrees ADD COLUMN cleaned_at TEXT",
        },
    )
    add_columns_if_missing(
        conn,
        "conflict_reports",
        {
            "resolution_successful": (
                "ALTER TABLE conflict_reports ADD COLUMN resolution_successful "
                "BOOLEAN NOT NULL DEFAULT 0"
            ),
        },
    )
    add_columns_if_missing(
        conn,
        "tickets",
        {
            "git_automation_json": (
                "ALTER TABLE tickets ADD COLUMN git_automation_json TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_workspace_scoped_runs_and_approvals(conn: Connection) -> None:
    """Let a run and its approvals exist without a ticket.

    Home Baxter chat is workspace-scoped: there is no work item to hang its run
    or its permission prompts on, but it still needs both so its tool calls go
    through the same approval inbox every other agent uses.
    """
    relax_not_null(conn, "agent_runs", "ticket_id")
    relax_not_null(conn, "approvals", "ticket_id")


def _m_branch_triage_message_run(conn: Connection) -> None:
    """Link each background branch-chat assistant turn to its AgentRun."""
    add_columns_if_missing(
        conn,
        "branch_triage_messages",
        {
            "run_id": (
                "ALTER TABLE branch_triage_messages ADD COLUMN run_id TEXT "
                "REFERENCES agent_runs(id)"
            ),
        },
    )
    if table_exists(conn, "branch_triage_messages"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_branch_triage_messages_run_id "
                "ON branch_triage_messages (run_id)"
            )
        )


def _m_workspace_codex_model(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "workspaces",
        {
            "codex_model": (
                "ALTER TABLE workspaces ADD COLUMN codex_model TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def _m_baxter_chat_runtime(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "baxter_chat_sessions",
        {
            "runtime_json": (
                "ALTER TABLE baxter_chat_sessions ADD COLUMN runtime_json "
                "TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )


def _m_worktree_ticket_id(conn: Connection) -> None:
    """``worktrees.ticket_id`` — one worktree per ticket, reused by its stages.

    Worktrees have only ever been keyed by run, which is right for N competing
    attempts at one stage and wrong for a pipeline: each stage would get its
    own tree and never see the previous stage's work.
    """
    add_columns_if_missing(
        conn,
        "worktrees",
        {
            "ticket_id": "ALTER TABLE worktrees ADD COLUMN ticket_id TEXT",
        },
    )
    if table_exists(conn, "worktrees") and not index_exists(conn, "ix_worktrees_ticket_id"):
        conn.execute(text("CREATE INDEX ix_worktrees_ticket_id ON worktrees (ticket_id)"))


MIGRATIONS: list[tuple[str, Migration]] = [
    ("0001_workspace_workflow_override", _m_workspace_workflow_override),
    ("0002_ticket_columns", _m_ticket_columns),
    ("0003_workspace_runtime_columns", _m_workspace_runtime_columns),
    ("0004_approval_columns", _m_approval_columns),
    ("0005_agent_run_orchestration_id", _m_agent_run_orchestration_id),
    ("0006_orchestration_run_columns", _m_orchestration_run_columns),
    ("0007_triage_messages_table", _m_triage_messages_table),
    ("0008_ticket_studio_tables", m_ticket_studio_tables),
    ("0009_ticket_diff_comments", _m_ticket_diff_comments),
    ("0010_branch_diff_comments", _m_branch_diff_comments),
    ("0011_branch_triage_messages", _m_branch_triage_messages),
    ("0012_agent_run_auto_approve", _m_agent_run_auto_approve),
    ("0013_ticket_studio_preview_state", m_ticket_studio_preview_state),
    ("0014_queued_run_failure_columns", _m_queued_run_failure_columns),
    ("0015_agent_model_columns", _m_agent_model_columns),
    ("0016_triage_message_run_id", _m_triage_message_run_id),
    ("0017_agent_run_timeout_override", _m_agent_run_timeout_override),
    ("0018_approval_checklist", _m_approval_checklist),
    ("0019_clear_classify_next_agent_backfill", _m_clear_classify_next_agent_backfill),
    ("0020_compatibility_posture", _m_compatibility_posture),
    ("0021_branch_triage_message_status", _m_branch_triage_message_status),
    ("0022_definition_versioning", _m_definition_versioning),
    ("0023_light_heavy_rigor_triage", m_light_heavy_rigor_triage),
    ("0024_agent_run_changed_paths", _m_agent_run_changed_paths),
    ("0025_artifact_evidence", _m_artifact_evidence),
    ("0026_verify_stage_in_v3", m_verify_stage_in_v3),
    ("0027_parallel_review_in_v3", m_parallel_review_in_v3),
    ("0028_require_verify_evidence", m_require_verify_evidence),
    ("0029_require_implement_real_surface", m_require_implement_real_surface),
    ("0030_refactor_skill_routes", m_refactor_skill_routes),
    ("0031_plan_skill_on_plan_stage", m_plan_skill_on_plan_stage),
    ("0032_adversarial_planning", m_adversarial_planning),
    ("0033_run_messages_table", _m_run_messages_table),
    ("0034_mcp_servers_table", m_mcp_servers_table),
    ("0035_mcp_server_tool_policy", m_mcp_server_tool_policy),
    ("0036_mcp_tool_calls_table", m_mcp_tool_calls_table),
    ("0037_mcp_server_health", m_mcp_server_health),
    ("0038_mcp_server_rate_limit", m_mcp_server_rate_limit),
    ("0039_queued_run_created_at", _m_queued_run_created_at),
    ("0040_approval_auto_resolution_audit", _m_approval_auto_resolution_audit),
    ("0041_agent_run_handoff_liveness", _m_agent_run_handoff_liveness),
    ("0042_ticket_enum_values", _m_ticket_enum_values),
    ("0043_run_approval_event_enum_values", _m_run_approval_event_enum_values),
    ("0044_ticket_scope_reroute_agent", _m_ticket_scope_reroute_agent),
    ("0045_ensure_terminal_stage", m_ensure_terminal_stage),
    ("0046_ticket_integration_review", _m_ticket_integration_review),
    ("0047_ticket_dependencies_table", _m_ticket_dependencies_table),
    ("0048_chat_message_parts", _m_chat_message_parts),
    ("0049_run_cancel_requested", _m_run_cancel_requested),
    ("0050_baxter_chat_tables", _m_baxter_chat_tables),
    ("0051_ticket_studio_turn_lifecycle", m_ticket_studio_turn_lifecycle),
    ("0052_git_automation", _m_git_automation),
    ("0053_workspace_effort_columns", _m_workspace_effort_columns),
    ("0054_workspace_scoped_runs_and_approvals", _m_workspace_scoped_runs_and_approvals),
    ("0055_branch_triage_message_run", _m_branch_triage_message_run),
    ("0056_reference_repos", m_reference_repos),
    ("0057_mcp_server_tool_catalog", m_mcp_server_tool_catalog),
    ("0058_global_agent_slots", m_global_agent_slots),
    ("0059_per_slot_queues", m_per_slot_queues),
    ("0060_chat_turn_thinking", _m_chat_turn_thinking),
    ("0061_chat_turn_answer", _m_chat_turn_answer),
    ("0062_lane_entry_kind", m_lane_entry_kind),
    ("0063_btw_exchanges", _m_btw_exchanges),
    ("0064_lane_entry_run_options", m_lane_entry_run_options),
    ("0065_workspace_codex_model", _m_workspace_codex_model),
    ("0066_baxter_chat_runtime", _m_baxter_chat_runtime),
    ("0067_orchestration_timeout_override", m_orchestration_timeout_override),
    ("0068_clear_phantom_skill_names", m_clear_phantom_skill_names),
    ("0069_skill_versioning", m_skill_versioning),
    ("0070_stage_fanout_groups", m_stage_fanout_groups),
    ("0071_backfill_handoff_artifacts", m_backfill_handoff_artifacts),
    ("0072_ticket_tags", _m_ticket_tags),
    ("0073_ticket_relations", _m_ticket_relations_table),
    ("0074_lane_entry_dismissed", m_lane_entry_dismissed),
    ("0075_composer_commands", m_composer_commands),
    ("0076_worktree_ticket_id", _m_worktree_ticket_id),
    ("0077_workspace_opencode_columns", _m_workspace_opencode_columns),
    ("0078_agent_run_git_boundary", m_agent_run_git_boundary),
    ("0079_agent_run_boundary_verdict", m_agent_run_boundary_verdict),
    ("0080_agent_run_preflight", m_agent_run_preflight),
]

assert_migration_ids_are_sound([migration_id for migration_id, _ in MIGRATIONS])


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


def _warn_if_database_is_ahead(applied_ids: set[str]) -> list[str]:
    """Flag migrations this build has never heard of.

    A migration that rewrites stored values leaves a database only newer code can
    read — check out an older commit, or revert one, and every query over the
    rewritten table fails with a LookupError that says nothing about the real cause.
    The recorded ids say so directly, so name it at startup instead.
    """
    unknown = sorted(applied_ids - {migration_id for migration_id, _ in MIGRATIONS})
    if unknown:
        logger.error(
            "Database has migrations this build does not know about: %s. It was "
            "migrated by newer code, and data those migrations rewrote may not be "
            "readable here. Check out the matching revision rather than running "
            "against it.",
            ", ".join(unknown),
        )
    return unknown


def apply_migrations(engine: Engine) -> list[str]:
    """Apply pending migrations in order. Returns the ids that ran this call."""
    if not str(engine.url).startswith("sqlite"):
        return []
    applied: list[str] = []
    with engine.begin() as conn:
        _ensure_migrations_table(conn)
        already = _applied_ids(conn)
        _warn_if_database_is_ahead(already)
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
