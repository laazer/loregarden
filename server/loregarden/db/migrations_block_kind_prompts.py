"""Every built-in role's outcome section says a blocked report needs a kind (749).

The stage-report contract is injected into every prompt, so agents see the
field; this makes the role bodies say it too, since the "Stage outcome
(required)" boilerplate is what an agent re-reads before it reports. Role
bodies are DB-authoritative, so the seed files alone change nothing for an
existing install — the same situation `0123` handled for the design agent, and
the same two guards: only a body missing the sentence is touched, and only one
no person has edited (a hand-edited body is left alone and reported).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

MIGRATION_ID = "0137_block_kind_in_role_prompts"

#: What every refreshed seed file names and no pre-749 body did (checked: 0 of
#: 27 built-in bodies mentioned it); its presence is what the refresh checks.
BLOCK_KIND_SENTENCE = "blocked_kind"


def _versions_by(conn: Connection, agent_id: str) -> set[str]:
    return {
        entry[0]
        for entry in conn.execute(
            text("SELECT created_by FROM studio_agent_versions WHERE agent_id=:id"),
            {"id": agent_id},
        ).fetchall()
    }


def _refresh_from_seed(conn: Connection, slug: str, role_file: str) -> None:
    row = (
        conn.execute(
            text("SELECT id, version, role_body FROM studio_agents WHERE slug=:s"), {"s": slug}
        )
        .mappings()
        .fetchone()
    )
    if row is None or BLOCK_KIND_SENTENCE in (row["role_body"] or ""):
        return
    editors = _versions_by(conn, row["id"]) - {"seed", "migration"}
    if editors:
        logger.warning(
            "%s: %r lacks the blocked_kind sentence and was edited by %s — leaving it alone; "
            "add it by hand in Studio so its blocked reports carry a kind.",
            MIGRATION_ID,
            slug,
            ", ".join(sorted(editors)),
        )
        return
    path = settings.agent_context_dir / role_file
    if not path.is_file():
        logger.warning("%s: %s is missing; cannot refresh %r", MIGRATION_ID, path, slug)
        return
    role_body = path.read_text(encoding="utf-8")
    if BLOCK_KIND_SENTENCE not in role_body:
        logger.warning("%s: seed for %r lacks the sentence too; not refreshing", MIGRATION_ID, slug)
        return

    now = datetime.now(timezone.utc)
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE studio_agents SET role_body=:body, version=:v, updated_at=:now WHERE id=:id"),
        {"body": role_body, "v": new_version, "now": now, "id": row["id"]},
    )
    snapshot = (
        conn.execute(
            text(
                "SELECT slug, name, description, adapter, default_model, timeout, default_skill, "
                "mcp_enabled, mcp_tools_json, gate_checks_json, handoff_checks_json, "
                "tool_grants_json, built_in FROM studio_agents WHERE id=:id"
            ),
            {"id": row["id"]},
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
            "agent_id": row["id"],
            "v": new_version,
            "snapshot": json.dumps(dict(snapshot)),
            "note": f"{MIGRATION_ID}: role body refreshed from seed (blocked_kind in outcome)",
            "now": now,
        },
    )
    logger.info("%s: refreshed %r from %s", MIGRATION_ID, slug, path)


def m_block_kind_in_role_prompts(conn: Connection) -> None:
    if not table_exists(conn, "studio_agents") or not table_exists(conn, "studio_agent_versions"):
        return
    for slug, cfg in AGENTS.items():
        _refresh_from_seed(conn, slug, cfg["role_file"])
