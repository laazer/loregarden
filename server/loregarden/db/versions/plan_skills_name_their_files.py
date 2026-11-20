"""Tell the plan skills that a plan must name its files.

`attach_artifact` now refuses a plan without `modules_touched`
(`services.path_claims.validate_plan_files`). The built-in skills are seeded
only when missing, so editing their SKILL.md reaches no database that already
has them; without this, every planner would learn the rule from a refusal.

Only built-in rows are touched: a row an operator owns (`built_in = 0`) is
theirs, as the seeding migration treats it. The section is appended, never
substituted, and a body that already mentions `modules_touched` is left alone.
Each change is a new skill version, as an edit through the UI would be.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)

#: The skills that attach a plan artifact.
PLAN_SKILLS = ("plan", "plan-simplest", "plan-seams", "plan-risk", "plan-synthesis")

#: Kept word for word in each of their SKILL.md files; a test holds them together.
FILES_SECTION = """\
## Files touched (required)

The plan's content must carry `modules_touched`: a list of the repo-relative
paths the work will change, with a trailing `/` for a directory, e.g.
`["server/loregarden/services/x.py", "client/src/lib/"]`. A note may follow a
path (`"x.py — the parser"`). The attach is refused without it. Open tickets in
the same landing tree are warned about the files you name, and a person can
order them before either starts writing.
"""

_SNAPSHOT_FIELDS = (
    "slug",
    "name",
    "description",
    "body",
    "required_capabilities_json",
    "pack_id",
    "pack_commit",
    "upstream_name",
    "built_in",
)


@migration("20261119_plan_skills_name_their_files", after="20261119_path_claims")
def m_plan_skills_name_their_files(conn: Connection) -> None:
    if not table_exists(conn, "skills") or not table_exists(conn, "skill_versions"):
        logger.warning("no skills tables; the plan skills were not told to name their files")
        return
    for slug in PLAN_SKILLS:
        _append_section(conn, slug)


def _append_section(conn: Connection, slug: str) -> None:
    row = (
        conn.execute(
            text(
                f"SELECT id, version, {', '.join(_SNAPSHOT_FIELDS)} FROM skills WHERE slug = :slug"
            ),
            {"slug": slug},
        )
        .mappings()
        .fetchone()
    )
    if row is None or not row["built_in"] or "modules_touched" in row["body"]:
        return
    body = f"{row['body'].rstrip()}\n\n{FILES_SECTION}"
    version = row["version"] + 1
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            "UPDATE skills SET body = :body, version = :version, updated_at = :now WHERE id = :id"
        ),
        {"body": body, "version": version, "now": now, "id": row["id"]},
    )
    snapshot = {field: row[field] for field in _SNAPSHOT_FIELDS} | {
        "body": body,
        "built_in": bool(row["built_in"]),
    }
    conn.execute(
        text(
            "INSERT INTO skill_versions "
            "(id, skill_id, version, snapshot_json, created_by, change_note, created_at) "
            "VALUES (:id, :skill_id, :version, :snapshot, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "skill_id": row["id"],
            "version": version,
            "snapshot": json.dumps(snapshot),
            "note": "Plans must name their files (modules_touched)",
            "now": now,
        },
    )
