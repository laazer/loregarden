"""Triage, static QA and repair run on Sonnet rather than the workspace's Opus.

Each is narrow and mechanical next to planning or implementing, and each ran on
Opus because nothing pinned it. `static_qa` and `repair` are pinned on the agent.
Triage is pinned on the v3 template's `triage` stage instead: its agent,
`ticket_scoper`, also scopes tickets in Ticket Studio, which keeps its model.

Only an empty pin is filled — a model a person chose is left alone.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_role_refresh import snapshot_agent_version
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

MIGRATION_ID = "20261009_sonnet_for_mechanical_lanes"
MODEL = "sonnet"
AGENTS = ("static_qa", "repair")
TEMPLATE = "studio-loregarden-tdd-v3"
STAGE = "triage"


@migration("20261009_sonnet_for_mechanical_lanes", after="20261009_verifier_selects_tests")
def m_sonnet_for_mechanical_lanes(conn: Connection) -> None:
    if table_exists(conn, "studio_agents") and table_exists(conn, "studio_agent_versions"):
        for slug in AGENTS:
            _pin_agent(conn, slug)
    if table_exists(conn, "workflow_templates"):
        _pin_stage(conn)


def _pin_agent(conn: Connection, slug: str) -> None:
    row = (
        conn.execute(
            text("SELECT id, version, default_model FROM studio_agents WHERE slug=:s"),
            {"s": slug},
        )
        .mappings()
        .fetchone()
    )
    if row is None or row["default_model"]:
        return
    now = datetime.now(timezone.utc)
    version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE studio_agents SET default_model=:m, version=:v, updated_at=:now WHERE id=:id"),
        {"m": MODEL, "v": version, "now": now, "id": row["id"]},
    )
    snapshot_agent_version(
        conn,
        agent_id=row["id"],
        version=version,
        note=f"{MIGRATION_ID}: default model {MODEL}",
        now=now,
    )


def _pin_stage(conn: Connection) -> None:
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return
    stages = json.loads(row["stages_json"] or "[]")
    stage = next((s for s in stages if s.get("key") == STAGE), None)
    if stage is None or stage.get("model"):
        return
    stage["model"] = MODEL
    version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": version, "id": row["id"]},
    )
    snapshot_template_version(conn, row["id"], version, f"Triage on {MODEL}")
