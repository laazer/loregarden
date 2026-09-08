"""Migration 0114: one stage-key spelling, everywhere a stage key is stored.

Its own module rather than another block in `migrations_templates`, which was at
1525 lines against a 1500 cap once this landed. The split is along a real seam:
everything here is about one rename — which spellings, which surfaces carry them,
and which surfaces share the spellings while meaning something else.

See `lg-workflow-integrity-660` for the measurement behind the surface list.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_columns, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: The five spelling forks, and the side chosen for all of them
#: (lg-workflow-integrity-660). The short/kebab form wins because
#: `run_duration_stats.CANONICAL_STAGE_KEYS` already declared exactly these five
#: canonical in exactly this direction (lg-workflow-integrity-558), because
#: `studio-loregarden-tdd-v3` — 568 of 672 version-pinned instances — is already
#: written this way, and because it reads as the same family as `backend-impl`
#: and `frontend-impl`, which AC3 requires stay distinct from the generic stage.
#:
#: `backend-impl` and `frontend-impl` are deliberately absent: they are specialist
#: stages that make the agent choice the generic stage defers, not spellings of it.
CANONICAL_STAGE_RENAMES: dict[str, str] = {
    "planning": "plan",
    "specification": "spec",
    "test_design": "test-design",
    "test_break": "test-break",
    "implementation": "implement",
}

#: Every (table, column) that stores a bare stage key.
#:
#: Deliberately absent, because they share the spellings while belonging to other
#: vocabularies: `artifacts.kind` (agent-chosen artifact kinds, the open tier
#: lg-workflow-integrity-613 keeps on purpose — 'specification' and 'test_design'
#: appear there) and `agent_runs.skill_name` ('test_break', 'test_design' are
#: skills; a skill is not a stage). Renaming either would corrupt a vocabulary
#: this migration has no authority over.
STAGE_KEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tickets", "workflow_stage_key"),
    ("workflow_instances", "current_stage_key"),
    ("orchestration_runs", "current_stage_key"),
    ("orchestration_runs", "stop_at_stage_key"),
    ("queued_runs", "stage_key"),
    ("queued_runs", "stop_at_stage_key"),
    ("approvals", "stage_key"),
    ("memory_briefings", "stage_key"),
    ("agent_runs", "stage_key"),
    ("stage_fanout_groups", "stage_key"),
    ("stage_fanout_groups", "pre_fanout_workflow_stage_key"),
)


def _renamed(key: str | None) -> str | None:
    """The canonical spelling of one key. Unknown keys pass through unchanged."""
    if key is None:
        return None
    return CANONICAL_STAGE_RENAMES.get(key, key)


def _rename_stage_entries(stages: list) -> bool:
    """Rewrite `key` on each stage, and any classify route pointing at one.

    Instance stage maps and template stage lists share this shape — a list of
    dicts carrying `key` — so one walker serves both. `classify_routes[].to_stage`
    names a stage too; none holds a fork today, and renaming it anyway costs
    nothing and stops a future route dangling.
    """
    changed = False
    for stage in stages:
        replacement = _renamed(stage.get("key"))
        if replacement != stage.get("key"):
            stage["key"] = replacement
            changed = True
        for route in stage.get("classify_routes") or []:
            target = _renamed(route.get("to_stage"))
            if target != route.get("to_stage"):
                route["to_stage"] = target
                changed = True
    return changed


def _rename_transitions(transitions: list) -> bool:
    """Rewrite the stage keys a transition joins. `from`/`to` name stages."""
    changed = False
    for transition in transitions:
        for side in ("from", "to"):
            replacement = _renamed(transition.get(side))
            if replacement != transition.get(side):
                transition[side] = replacement
                changed = True
    return changed


def _rename_json_column(conn: Connection, table: str, column: str, rename) -> None:
    """Load, rewrite and store one JSON column across a table, skipping no-ops."""
    if not table_exists(conn, table) or column not in table_columns(conn, table):
        return
    rows = conn.execute(text(f"SELECT id, {column} FROM {table}")).mappings().all()
    for row in rows:
        payload = json.loads(row[column] or "[]")
        if not rename(payload):
            continue
        conn.execute(
            text(f"UPDATE {table} SET {column}=:payload WHERE id=:id"),
            {"payload": json.dumps(payload), "id": row["id"]},
        )


def _rename_version_snapshots(conn: Connection) -> None:
    """Rewrite the stage keys inside stored template-version snapshots.

    This is the surface whose absence would strand work. 100 instances pin a
    version whose snapshot carries the old spellings (loregarden-tdd v2: 52,
    blobert-tdd v2: 41 and v4: 7), and a pinned instance resolves its stages
    through the snapshot, not the template. Renaming the cursor without renaming
    the snapshot leaves exactly the dangling key AC2 forbids.

    Rewritten in place rather than by adding a new version: a pin names a version
    number, so a new snapshot would simply not be the one those instances read.
    The rename preserves structure, which is what a snapshot exists to hold.
    """
    if not table_exists(conn, "workflow_template_versions"):
        return
    if "snapshot_json" not in table_columns(conn, "workflow_template_versions"):
        return
    rows = (
        conn.execute(text("SELECT id, snapshot_json FROM workflow_template_versions"))
        .mappings()
        .all()
    )
    for row in rows:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        stages = json.loads(snapshot.get("stages_json") or "[]")
        transitions = json.loads(snapshot.get("transitions_json") or "[]")
        if not (_rename_stage_entries(stages) | _rename_transitions(transitions)):
            continue
        snapshot["stages_json"] = json.dumps(stages)
        snapshot["transitions_json"] = json.dumps(transitions)
        conn.execute(
            text("UPDATE workflow_template_versions SET snapshot_json=:snap WHERE id=:id"),
            {"snap": json.dumps(snapshot), "id": row["id"]},
        )


def m_canonical_stage_keys(conn: Connection) -> None:
    """Apply one stage-key spelling everywhere it is stored.

    `lg-workflow-integrity-102` chose a convention and deleted the legacy alias
    map; this is its AC1, split out because a rename is not a template edit. Every
    surface moves in one transaction, which is also the answer to running work:
    a ticket mid-stage has its cursor, its instance stage map, its orchestration
    run and its template rewritten together, so it resolves to the renamed key
    rather than to one that no longer exists.

    Templates are not version-bumped. A bump would leave the 672 pinned instances
    reading the old snapshot, which is the opposite of what this needs; the
    snapshots are rewritten in place instead.
    """
    for table, column in STAGE_KEY_COLUMNS:
        # Both guards are load-bearing. This migration runs against databases old
        # enough to predate some of these columns — `tickets.workflow_stage_key`
        # among them — and a migration that assumes its own schema is a migration
        # that only works forwards from today.
        if not table_exists(conn, table) or column not in table_columns(conn, table):
            continue
        for old, new in CANONICAL_STAGE_RENAMES.items():
            conn.execute(
                text(f"UPDATE {table} SET {column}=:new WHERE {column}=:old"),
                {"new": new, "old": old},
            )

    for table in ("workflow_templates", "studio_workflows"):
        # The Studio drafts matter as much as the templates: a draft left on the
        # old spellings republishes the forks straight back over the renamed
        # template, which is the drift lg-workflow-integrity-561 fixed.
        _rename_json_column(conn, table, "stages_json", _rename_stage_entries)
        _rename_json_column(conn, table, "transitions_json", _rename_transitions)

    _rename_json_column(conn, "workflow_instances", "stages_json", _rename_stage_entries)
    _rename_json_column(
        conn, "stage_fanout_groups", "pre_fanout_stage_map_json", _rename_stage_entries
    )
    _rename_version_snapshots(conn)
