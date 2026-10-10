"""`ui-design` is skipped when the plan names no UI path.

It ran on every non-light ticket. Measured 2026-10-09 over 59 that ran it since
2026-09-01: 53 never changed a `client/` file, at about 5.6 minutes a run.
`no_ui_work` keeps the light-route skip and adds the plan check
(`studio_routing.plan_names_no_ui_paths`).

Only a stage still on `routed_as_light_work` is changed; one a person has
pointed elsewhere in Studio is left alone.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_TEMPLATE = "studio-loregarden-tdd-v3"
_STAGE = "ui-design"
_FROM = "routed_as_light_work"
_TO = "no_ui_work"


@migration("20261009_ui_design_skip_without_ui", after="20261008_handoff_notice_kind")
def m_ui_design_skip_without_ui(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return
    stages = json.loads(row["stages_json"] or "[]")
    stage = next((s for s in stages if s.get("key") == _STAGE), None)
    if stage is None or stage.get("skip_when") != _FROM:
        return
    stage["skip_when"] = _TO
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    snapshot_template_version(conn, row["id"], new_version, "Skip ui-design when no UI work")
