"""Migration 0116: the human-verification brief, in the skill and in the askers.

Two independent changes, each guarding itself, in the append-only style of this
package: seed `human-verification-brief` into `skills`, and refresh the role
bodies of the two agents that hand a person something to look at
(`visual_qa`, `ac_gatekeeper`) from their seed files.

The skill seed in `migrations_skills` was one-time, so a directory added after
it ran reaches no install without a migration of its own.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.config import settings
from loregarden.db.migration_utils import table_exists
from loregarden.services.skill_service import parse_skill_markdown
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

#: The skill this migration seeds, and the directory it is seeded from.
SKILL_SLUG = "human-verification-brief"

#: The agents that ask a human to look, and the seed file each is refreshed from.
#: Only these two; an agent an operator has edited keeps its text (see the guard).
REFRESH_ROLE_FILES: dict[str, str] = {
    "visual_qa": "agents/misc_agents/visual_qa_v1.md",
    "ac_gatekeeper": "agents/acceptance_criteria_gatekeeper.md",
}


def m_human_verification_brief(conn: Connection) -> None:
    """Seed the brief skill and refresh the two asker role bodies."""
    _seed_skill(conn)
    for slug, role_file in REFRESH_ROLE_FILES.items():
        _refresh_role_body(conn, slug=slug, role_file=role_file)


def _seed_skill(conn: Connection) -> None:
    if not table_exists(conn, "skills") or not table_exists(conn, "skill_versions"):
        logger.info("0116: no skills tables; nothing to seed")
        return
    existing = conn.execute(
        text("SELECT id FROM skills WHERE slug = :slug"), {"slug": SKILL_SLUG}
    ).fetchone()
    if existing is not None:
        logger.info("0116: %r already present; leaving it alone", SKILL_SLUG)
        return

    path = settings.agent_context_dir / "skills" / SKILL_SLUG / "SKILL.md"
    if not path.is_file():
        logger.warning("0116: %s is missing; cannot seed %r", path, SKILL_SLUG)
        return
    parsed = parse_skill_markdown(path.read_text(encoding="utf-8"), slug=SKILL_SLUG)
    if not parsed.body.strip():
        logger.warning("0116: empty seed body for %r; not seeding", SKILL_SLUG)
        return

    now = datetime.now(timezone.utc)
    skill_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO skills "
            "(id, slug, name, description, body, required_capabilities_json, "
            "pack_id, pack_commit, upstream_name, version, built_in, created_at, updated_at) "
            "VALUES (:id, :slug, :name, :description, :body, '[]', "
            "NULL, NULL, NULL, 1, 1, :now, :now)"
        ),
        {
            "id": skill_id,
            "slug": SKILL_SLUG,
            "name": parsed.name,
            "description": parsed.description,
            "body": parsed.body,
            "now": now,
        },
    )
    conn.execute(
        text(
            "INSERT INTO skill_versions "
            "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
            "VALUES (:id, :skill_id, 1, :snapshot, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "skill_id": skill_id,
            "snapshot": json.dumps(
                {
                    "slug": SKILL_SLUG,
                    "name": parsed.name,
                    "description": parsed.description,
                    "body": parsed.body,
                    "required_capabilities_json": "[]",
                    "pack_id": None,
                    "pack_commit": None,
                    "upstream_name": None,
                    "built_in": True,
                }
            ),
            "note": "0116_human_verification_brief: seeded from agent_context/skills",
            "now": now,
        },
    )
    logger.info("0116: seeded skill %r", SKILL_SLUG)


def _refresh_role_body(conn: Connection, *, slug: str, role_file: str) -> None:
    if not table_exists(conn, "studio_agents") or not table_exists(conn, "studio_agent_versions"):
        return
    row = conn.execute(
        text("SELECT id, version, role_body FROM studio_agents WHERE slug = :slug"),
        {"slug": slug},
    ).fetchone()
    if row is None:
        logger.info("0116: no %r agent row; nothing to refresh", slug)
        return

    agent_id, version, current_role_body = row[0], int(row[1]), row[2] or ""
    creators = [
        entry[0]
        for entry in conn.execute(
            text("SELECT created_by FROM studio_agent_versions WHERE agent_id = :id"),
            {"id": agent_id},
        ).fetchall()
    ]
    if version != 1 or creators != ["seed"]:
        logger.info(
            "0116: leaving %r role body alone — version=%s, history=%s (operator edits present)",
            slug,
            version,
            creators or "none",
        )
        return

    path = settings.agent_context_dir / role_file
    if not path.is_file():
        logger.warning("0116: %s is missing; cannot refresh %r", path, slug)
        return
    role_body = path.read_text(encoding="utf-8")
    if not role_body.strip() or role_body == current_role_body:
        logger.info("0116: %r role body already current; no refresh needed", slug)
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
                "0116_human_verification_brief: refreshed the seeded role body so a "
                "human-eyes AC is asked as a derived brief. Restore v1 to undo."
            ),
            "now": now,
        },
    )
    logger.info("0116: refreshed the untouched %r role body and recorded it as v2", slug)
