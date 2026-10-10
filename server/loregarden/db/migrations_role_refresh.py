"""Refresh a built-in agent's role body from its seed file, when nobody edited it.

Role bodies are DB-authoritative, so editing `agent_context/agents/**` changes
nothing for an existing install until a migration copies the new text in. This
is the copy, with the guards 0123 and 0137 each wrote by hand:

- **Marker-gated.** Only a body missing `marker` is touched, so a row already
  carrying the change — or a later rewrite of it — is left alone.
- **Person-gated.** A row any person has edited (a version written by someone
  other than `AUTOMATED_EDITORS`) is not overwritten; it is reported, because
  the stage will run without the change until someone merges it by hand.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.config import settings
from loregarden.db.migrations_block_kind_prompts import AUTOMATED_EDITORS
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _editors(conn: Connection, agent_id: str) -> set[str]:
    rows = conn.execute(
        text("SELECT created_by FROM studio_agent_versions WHERE agent_id=:id"), {"id": agent_id}
    ).fetchall()
    return {entry[0] for entry in rows} - AUTOMATED_EDITORS


def refresh_role_from_seed(
    conn: Connection, *, migration_id: str, slug: str, role_file: str, marker: str
) -> bool:
    """Copy `role_file` into `slug`'s role body; return whether it wrote."""
    row = (
        conn.execute(
            text("SELECT id, version, role_body FROM studio_agents WHERE slug=:s"), {"s": slug}
        )
        .mappings()
        .fetchone()
    )
    if row is None or marker in (row["role_body"] or ""):
        return False
    editors = _editors(conn, row["id"])
    if editors:
        logger.warning(
            "%s: %r was edited by %s and lacks %r — leaving it alone; merge the seed "
            "file's new section into it by hand in Studio.",
            migration_id,
            slug,
            ", ".join(sorted(editors)),
            marker,
        )
        return False
    path = settings.agent_context_dir / role_file
    if not path.is_file():
        logger.warning("%s: %s is missing; cannot refresh %r", migration_id, path, slug)
        return False
    role_body = path.read_text(encoding="utf-8")
    if marker not in role_body:
        logger.warning("%s: seed for %r lacks %r too; not refreshing", migration_id, slug, marker)
        return False

    now = datetime.now(timezone.utc)
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE studio_agents SET role_body=:body, version=:v, updated_at=:now WHERE id=:id"),
        {"body": role_body, "v": new_version, "now": now, "id": row["id"]},
    )
    snapshot_agent_version(
        conn,
        agent_id=row["id"],
        version=new_version,
        note=f"{migration_id}: role body refreshed from seed ({marker})",
        now=now,
    )
    logger.info("%s: refreshed %r from %s", migration_id, slug, path)
    return True


def snapshot_agent_version(
    conn: Connection, *, agent_id: str, version: int, note: str, now: datetime
) -> None:
    """Record the agent row as it now stands as `version`, authored by a migration."""
    snapshot = (
        conn.execute(
            text(
                "SELECT slug, name, description, adapter, default_model, timeout, default_skill, "
                "mcp_enabled, mcp_tools_json, gate_checks_json, handoff_checks_json, "
                "tool_grants_json, built_in FROM studio_agents WHERE id=:id"
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
            "VALUES (:id, :agent_id, :v, :snapshot, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "agent_id": agent_id,
            "v": version,
            "snapshot": json.dumps(dict(snapshot)),
            "note": note,
            "now": now,
        },
    )
