"""Per-agent tool grants, and the one-time role-body refresh that goes with them.

One migration, two independent changes, each guarding itself — the append-only
convention in this package.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.config import settings
from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

#: The built-in whose role body became load-bearing when the chat rails started
#: rendering it. Only this slug is refreshed; a custom agent is the operator's.
_REFRESH_SLUG = "triage"
_REFRESH_ROLE_FILE = "agents/misc_agents/baxter_v1.md"


def m_agent_tool_grants(conn: Connection) -> None:
    """Add ``studio_agents.tool_grants_json`` and refresh an untouched Baxter role.

    The refresh fires only for a pristine seeded row — version 1 with a single
    ``created_by='seed'`` history entry. An operator who has edited Baxter in
    Studio keeps their text; that is the whole point of the guard, and on any
    install where Baxter has been edited this correctly does nothing.

    Whichever branch runs, it says so. A migration that quietly declines to act
    is indistinguishable from one that never ran at all, and this one's decision
    depends on data an operator cannot see from the schema.
    """
    add_columns_if_missing(
        conn,
        "studio_agents",
        {
            "tool_grants_json": (
                "ALTER TABLE studio_agents ADD COLUMN tool_grants_json TEXT NOT NULL DEFAULT '{}'"
            ),
        },
    )

    if not table_exists(conn, "studio_agents") or not table_exists(conn, "studio_agent_versions"):
        return

    row = conn.execute(
        text("SELECT id, version, role_body FROM studio_agents WHERE slug = :slug"),
        {"slug": _REFRESH_SLUG},
    ).fetchone()
    if row is None:
        logger.info("0100: no %r agent row; nothing to refresh", _REFRESH_SLUG)
        return

    agent_id, version, current_role_body = row[0], int(row[1]), row[2] or ""
    history = conn.execute(
        text("SELECT created_by FROM studio_agent_versions WHERE agent_id = :id"),
        {"id": agent_id},
    ).fetchall()
    creators = [entry[0] for entry in history]
    if version != 1 or creators != ["seed"]:
        logger.info(
            "0100: leaving %r role body alone — version=%s, history=%s (operator edits present)",
            _REFRESH_SLUG,
            version,
            creators or "none",
        )
        return

    path = settings.agent_context_dir / _REFRESH_ROLE_FILE
    if not path.is_file():
        logger.warning("0100: %s is missing; cannot refresh %r", path, _REFRESH_SLUG)
        return
    role_body = path.read_text(encoding="utf-8")
    if not role_body.strip() or role_body == current_role_body:
        logger.info("0100: %r role body already current; no refresh needed", _REFRESH_SLUG)
        return

    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            "UPDATE studio_agents SET role_body = :body, version = 2, updated_at = :now "
            "WHERE id = :id"
        ),
        {"body": role_body, "now": now, "id": agent_id},
    )
    snapshot = (
        conn.execute(
            text(
                "SELECT slug, name, description, adapter, default_model, timeout, default_skill, "
                "mcp_enabled, mcp_tools_json, gate_checks_json, handoff_checks_json, "
                "tool_grants_json, built_in FROM studio_agents WHERE id = :id"
            ),
            {"id": agent_id},
        )
        .mappings()
        .fetchone()
    )
    conn.execute(
        text(
            "INSERT INTO studio_agent_versions "
            "(id, agent_id, version, snapshot_json, created_by, change_note, created_at) "
            "VALUES (:id, :agent_id, 2, :snapshot, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "agent_id": agent_id,
            "snapshot": json.dumps({**dict(snapshot), "role_body": role_body}, default=str),
            "note": (
                "0100_agent_tool_grants: refreshed the seeded role body now that the chat "
                "rails render it. Restore v1 to undo."
            ),
            "now": now,
        },
    )
    logger.info("0100: refreshed the untouched %r role body and recorded it as v2", _REFRESH_SLUG)
