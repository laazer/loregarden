"""A review point on the design/plan stages, approved by the orchestrator by default.

Before this, a plan flowed straight from `plan-synthesis` / `ui-design` into
`implement`: no live template set `gate_required` on a design stage, so there
was nothing for anyone — person or orchestrator — to approve. Two migrations:

- `0132` gives each orchestration (and each parked queue entry) an
  `approve_design_plans` dial, default on, so the run modal's choice survives
  parking and resume the way `auto_approve` and `stop_at_stage_key` do.
- `0133` sets `gate_required` on the design/plan stages of the live templates
  AND their Studio drafts — the draft too, or the next publish rolls the gate
  back (lg-workflow-integrity-561).

Split out from `migrations_templates`, which is past the size cap.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: Template slug → the stage keys whose output is a design plan there. The
#: predicate the runtime uses is agent-based (`services.design_plan_gate`); this
#: is the one-off list of where those agents sit in the templates that exist.
DESIGN_PLAN_STAGES: dict[str, tuple[str, ...]] = {
    "studio-loregarden-tdd-v3": ("plan-synthesis", "ui-design"),
    "blobert-tdd": ("plan", "ui-design"),
}


def m_approve_design_plans_columns(conn: Connection) -> None:
    for table in ("orchestration_runs", "queued_runs"):
        if table_exists(conn, table):
            add_columns_if_missing(
                conn,
                table,
                {
                    "approve_design_plans": (
                        f"ALTER TABLE {table} ADD COLUMN approve_design_plans "
                        "BOOLEAN NOT NULL DEFAULT 1"
                    )
                },
            )


def _gate_stages(stages: list[dict], keys: tuple[str, ...]) -> bool:
    changed = False
    for stage in stages:
        if stage.get("key") in keys and not stage.get("gate_required"):
            stage["gate_required"] = True
            changed = True
    return changed


def _gate_live_template(conn: Connection, slug: str, keys: tuple[str, ...]) -> None:
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": slug},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return
    stages = json.loads(row["stages_json"] or "[]")
    if not _gate_stages(stages, keys):
        return
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    snapshot_template_version(conn, row["id"], new_version, "Design-plan sign-off gate")


def _gate_studio_draft(conn: Connection, slug: str, keys: tuple[str, ...]) -> None:
    row = (
        conn.execute(
            text("SELECT id, stages_json FROM studio_workflows WHERE slug=:s"), {"s": slug}
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return
    stages = json.loads(row["stages_json"] or "[]")
    if _gate_stages(stages, keys):
        conn.execute(
            text("UPDATE studio_workflows SET stages_json=:st WHERE id=:id"),
            {"st": json.dumps(stages), "id": row["id"]},
        )


def m_design_plan_gates(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    for slug, keys in DESIGN_PLAN_STAGES.items():
        _gate_live_template(conn, slug, keys)
        if table_exists(conn, "studio_workflows"):
            _gate_studio_draft(conn, slug, keys)
