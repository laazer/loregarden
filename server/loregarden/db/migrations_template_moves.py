"""Moving tickets between template versions, shared by migrations that reshape one.

Two rules, for the two kinds of ticket a reshape meets:

- A `backlog` ticket nothing has ever run is moved, so the backlog does not keep
  the old shape for months.
- Every other ticket keeps the definition it is on. An unpinned instance follows
  the live template, so it is pinned to the outgoing version before the edit.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from sqlalchemy import Connection, text

_PENDING = "pending"
_BACKLOG = "backlog"


def move_never_run_backlog(
    conn: Connection,
    *,
    workspace_slug: str,
    from_template_id: str,
    to_template_id: str,
    version: int,
    stages: list[dict],
) -> int:
    """Move `workspace_slug`'s never-run backlog onto `to_template_id` at `version`.

    Never run means no agent or orchestration run and every stage still pending,
    so the cursor can go to the new first stage without losing anything.
    """
    if not (table_exists(conn, "workflow_instances") and table_exists(conn, "tickets")):
        return 0
    unrun = "".join(
        f" AND NOT EXISTS (SELECT 1 FROM {table} r WHERE r.ticket_id = t.id)"
        for table in ("agent_runs", "orchestration_runs")
        if table_exists(conn, table)
    )
    rows = (
        conn.execute(
            text(
                "SELECT i.id, i.ticket_id, i.stages_json FROM workflow_instances i "
                "JOIN tickets t ON t.id = i.ticket_id "
                "JOIN workspaces w ON w.id = t.workspace_id "
                "WHERE i.template_id = :tid AND w.slug = :ws AND t.state = :backlog" + unrun
            ),
            {"tid": from_template_id, "ws": workspace_slug, "backlog": _BACKLOG},
        )
        .mappings()
        .all()
    )
    ordered = [s["key"] for s in sorted(stages, key=lambda s: s["order"])]
    fresh = json.dumps([{"key": key, "status": _PENDING} for key in ordered])
    moved = 0
    for row in rows:
        statuses = json.loads(row["stages_json"] or "[]")
        if any(entry.get("status") != _PENDING for entry in statuses):
            continue
        conn.execute(
            text(
                "UPDATE workflow_instances SET template_id=:tid, template_version=:v, "
                "current_stage_key=:key, stages_json=:st WHERE id=:id"
            ),
            {"tid": to_template_id, "v": version, "key": ordered[0], "st": fresh, "id": row["id"]},
        )
        conn.execute(
            text("UPDATE tickets SET workflow_stage_key=:key WHERE id=:id"),
            {"key": ordered[0], "id": row["ticket_id"]},
        )
        moved += 1
    return moved


def pin_unpinned(conn: Connection, template_id: str, version: int) -> int:
    """Pin instances that follow `template_id` live to `version`, its current one.

    Call before the template is edited: the snapshot written here, when `version`
    has none, is of the row as it stands.
    """
    if not (
        table_exists(conn, "workflow_instances")
        and table_exists(conn, "workflow_template_versions")
    ):
        return 0
    snapshotted = conn.execute(
        text("SELECT 1 FROM workflow_template_versions WHERE template_id=:tid AND version=:v"),
        {"tid": template_id, "v": version},
    ).first()
    if snapshotted is None:
        snapshot_template_version(conn, template_id, version, "Pinned before a template reshape")
    return conn.execute(
        text(
            "UPDATE workflow_instances SET template_version=:v "
            "WHERE template_id=:tid AND template_version IS NULL"
        ),
        {"v": version, "tid": template_id},
    ).rowcount


__all__ = ["move_never_run_backlog", "pin_unpinned"]
