"""A criterion may be a person's judgment, and the roles and skill that ask say when.

Adds what a `person` settlement records (`criterion_tables`): the kind of
judgment and the question put to the person. Nothing is backfilled. Then
refreshes what the askers read: the verifier and AC gatekeeper (who settle
criteria and check the review kit's steps), the spec role (who writes the
criteria) and the human-verification skill.

Roles via `refresh_role_from_seed`: one a person has edited is reported, not
overwritten. The skill gains its section at the end, as `plan_skills_name_their_files`
does, only while built in.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261125_person_judged_criteria"

#: Each refreshed role's seed file and the words that mark the change in it.
ROLES: dict[str, tuple[str, str]] = {
    "ac_gatekeeper": (
        "agents/acceptance_criteria_gatekeeper.md",
        "## What may reach the person at the gate",
    ),
    "verifier": ("agents/misc_agents/verifier_v1.md", "- **A person's judgment:**"),
    "spec": ("agents/2_spec/spec_v1.md", "**A person is not a test rig.**"),
}

SKILL_SLUG = "human-verification-brief"
SKILL_MARKER = "## Ask only for a judgment"

#: Kept word for word in the skill's SKILL.md; a test holds them together.
SKILL_SECTION = """\
## Ask only for a judgment

Before a check reaches a person, ask who could answer it.

- **A test or a scripted run could** (what latched, what a label reads, how much,
  how long, whether anything errored): do it, and record the result. It is not a
  step. "Nobody wrote the test" means write it.
- **Nobody could** (counting frames, comparing numbers on a moving screen, an
  audience, a second player, a week of use): rewrite it as something observable
  in one sitting, or measure it.
- **Only a person could** (feel, look, sound, whether it makes sense, a choice
  that is the owner's, hardware only they have): that is the step. Say which in
  `judges` and why in `why_person`. Both are shown to the person.

When nothing is left, the kit says so in `nothing_to_judge`, with what proved the
change. One honest judgment is worth more than five checks of facts.
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


@migration("20261125_person_judged_criteria", after="20261124_drop_worktree_conflicts")
def m_person_judged_criteria(conn: Connection) -> None:
    if table_exists(conn, "criterion_settlements"):
        add_columns_if_missing(
            conn,
            "criterion_settlements",
            {
                "judges": "ALTER TABLE criterion_settlements ADD COLUMN judges VARCHAR",
                "person_ask": (
                    "ALTER TABLE criterion_settlements "
                    "ADD COLUMN person_ask VARCHAR NOT NULL DEFAULT ''"
                ),
            },
        )
    for slug, (role_file, marker) in ROLES.items():
        refresh_role_from_seed(
            conn, migration_id=MIGRATION_ID, slug=slug, role_file=role_file, marker=marker
        )
    _append_skill_section(conn)


def _append_skill_section(conn: Connection) -> None:
    if not table_exists(conn, "skills") or not table_exists(conn, "skill_versions"):
        return
    row = (
        conn.execute(
            text(
                f"SELECT id, version, {', '.join(_SNAPSHOT_FIELDS)} FROM skills WHERE slug = :slug"
            ),
            {"slug": SKILL_SLUG},
        )
        .mappings()
        .fetchone()
    )
    if row is None or not row["built_in"] or SKILL_MARKER in row["body"]:
        return
    body = f"{row['body'].rstrip()}\n\n{SKILL_SECTION}"
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
            "note": "Ask a person only for a judgment",
            "now": now,
        },
    )
