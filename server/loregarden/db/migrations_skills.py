"""Migrations for the `skills` / `skill_versions` schema and its built-in seed.

Split out of `migrations.py`, which had grown past the organization gate's limit
again. The division follows `migrations_mcp.py`: a cluster that belongs together
moves as a cluster. These own the two skill tables from creation through the
one-time seed of the built-in skills, and nothing else touches them.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.config import settings
from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from loregarden.services.skill_service import parse_skill_markdown, validate_skill_slug
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def m_skill_versioning(conn: Connection) -> None:
    if not table_exists(conn, "skills"):
        conn.execute(
            text(
                """
                CREATE TABLE skills (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    required_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    pack_id TEXT,
                    pack_commit TEXT,
                    upstream_name TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    built_in INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
        )
    add_columns_if_missing(
        conn,
        "skills",
        {
            "required_capabilities_json": (
                "ALTER TABLE skills ADD COLUMN required_capabilities_json "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
            "pack_id": "ALTER TABLE skills ADD COLUMN pack_id TEXT",
            "pack_commit": "ALTER TABLE skills ADD COLUMN pack_commit TEXT",
            "upstream_name": "ALTER TABLE skills ADD COLUMN upstream_name TEXT",
            "version": "ALTER TABLE skills ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "built_in": "ALTER TABLE skills ADD COLUMN built_in INTEGER NOT NULL DEFAULT 0",
            "created_at": "ALTER TABLE skills ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            "updated_at": "ALTER TABLE skills ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        },
    )
    if not index_exists(conn, "ix_skills_slug"):
        conn.execute(text("CREATE UNIQUE INDEX ix_skills_slug ON skills (slug)"))

    if not table_exists(conn, "skill_versions"):
        conn.execute(
            text(
                """
                CREATE TABLE skill_versions (
                    id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT '',
                    change_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(skill_id) REFERENCES skills(id)
                )
                """
            )
        )
    if not index_exists(conn, "ix_skill_versions_skill_id"):
        conn.execute(text("CREATE INDEX ix_skill_versions_skill_id ON skill_versions (skill_id)"))
    if not index_exists(conn, "ix_skill_versions_skill_version"):
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_skill_versions_skill_version "
                "ON skill_versions (skill_id, version)"
            )
        )

    _seed_builtin_skills_for_migration(conn)


def _seed_builtin_skills_for_migration(conn: Connection) -> None:
    root = settings.agent_context_dir / "skills"
    if not root.is_dir():
        logger.warning("skill migration seed directory missing: %s", root)
        return

    existing = {row[0] for row in conn.execute(text("SELECT slug FROM skills")).fetchall()}
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        try:
            slug = validate_skill_slug(child.name)
        except ValueError as exc:
            logger.warning("skill migration skipping %s: %s", child, exc)
            continue
        if slug in existing:
            continue
        skill_md = child / "SKILL.md"
        try:
            markdown = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("skill migration skipping unreadable seed %s: %s", skill_md, exc)
            continue
        parsed = parse_skill_markdown(markdown, slug=slug)
        if not parsed.body.strip():
            logger.warning("skill migration skipping empty seed body for %r", slug)
            continue
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
                "slug": slug,
                "name": parsed.name,
                "description": parsed.description,
                "body": parsed.body,
                "now": now,
            },
        )
        snapshot = {
            "slug": slug,
            "name": parsed.name,
            "description": parsed.description,
            "body": parsed.body,
            "required_capabilities_json": "[]",
            "pack_id": None,
            "pack_commit": None,
            "upstream_name": None,
            "built_in": True,
        }
        conn.execute(
            text(
                "INSERT INTO skill_versions "
                "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
                "VALUES (:id, :skill_id, 1, :snapshot, 'migration', "
                "'Seeded built-in skill from agent_context/skills', :now)"
            ),
            {
                "id": str(uuid4()),
                "skill_id": skill_id,
                "snapshot": json.dumps(snapshot),
                "now": now,
            },
        )
        existing.add(slug)
