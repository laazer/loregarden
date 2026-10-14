"""loregarden's implement stage stops offering the GDScript reviewer.

`studio-loregarden-tdd-v3` is loregarden's workflow (969 tickets, one on
lore-eden, none on blobert), and loregarden has no `.gd` files. Its `implement`
stage still carried a `gdscript_reviewer` route on `gameplay`/`gdscript`. In the
live database that route had never dispatched on loregarden: all 87 of the
agent's runs are blobert `script_review` runs, and no open loregarden ticket
names either term. It only made the stage read as if loregarden routed work to a
game-script reviewer.

Removed from the template's current version only, as a new snapshotted version.
Instances pinned to an earlier version keep their definition: a pin is the
definition a ticket started under, and the route cannot match any of them.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261014_loregarden_implement_no_gdscript"
TEMPLATE_SLUG = "studio-loregarden-tdd-v3"
STAGE_KEY = "implement"
AGENT_ID = "gdscript_reviewer"


@migration("20261014_loregarden_implement_no_gdscript", after="20261014_test_selection_shared")
def m_loregarden_implement_no_gdscript(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:slug"),
            {"slug": TEMPLATE_SLUG},
        )
        .mappings()
        .first()
    )
    if row is None:
        return
    stages = json.loads(row["stages_json"] or "[]")
    stage = next((item for item in stages if item.get("key") == STAGE_KEY), None)
    if stage is None:
        return
    routes = stage.get("classify_routes") or []
    kept = [route for route in routes if route.get("agent_id") != AGENT_ID]
    if len(kept) == len(routes):
        return
    stage["classify_routes"] = kept

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:stages, version=:v WHERE id=:id"),
        {"stages": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    snapshot_template_version(
        conn, row["id"], new_version, "implement: drop the gdscript_reviewer route"
    )
