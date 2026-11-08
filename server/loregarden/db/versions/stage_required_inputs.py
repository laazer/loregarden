"""Review, verify and gate stages declare the inputs they need (`stage_readiness`).

Measured before this landed (live database, read-only, 2026-11-08): of the 810
review, verify and gate runs since 2026-10-01, none ran on a ticket whose every
earlier run recorded an empty change, and 7 of 1199 such runs ever ran on a
ticket with no acceptance criteria. So the check is expected to fire rarely; it
exists so the rare case costs nothing instead of a turn.

Every template is reshaped by one rule, on the stage's role rather than its key:

- a verify or gate stage, or a single-agent stage run by the verifier or a
  gatekeeper: the diff and the acceptance criteria;
- a parallel stage with a review lens, or a single-agent stage run by a
  reviewer: the diff. A lens reads code, and judges it on its own terms.

A stage that already declares inputs is left alone, so an edit made in Studio
is never overwritten. Ticket instances pinned to an older template version keep
running without the check, as they do for every template change.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_JUDGES = frozenset({"verifier", "gatekeeper", "ac_gatekeeper"})
_REVIEWERS = frozenset(
    {"architecture_reviewer", "static_qa", "security_reviewer", "gdscript_reviewer"}
)
_JUDGE_TYPES = frozenset({"verify", "gate"})
_DIFF = "diff"
_CRITERIA = "acceptance_criteria"


def _inputs_for(stage: dict) -> list[str]:
    lanes = {lane.get("agent_id") for lane in stage.get("parallel_agents") or []}
    if stage.get("stage_type") in _JUDGE_TYPES or stage.get("agent_id") in _JUDGES:
        return [_DIFF, _CRITERIA]
    if lanes & _REVIEWERS or stage.get("agent_id") in _REVIEWERS:
        return [_DIFF]
    return []


@migration("20261108_stage_required_inputs", after="20261107_criterion_settlements")
def m_stage_required_inputs(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    rows = (
        conn.execute(text("SELECT id, version, stages_json FROM workflow_templates"))
        .mappings()
        .all()
    )
    for row in rows:
        stages = json.loads(row["stages_json"] or "[]")
        changed = False
        for stage in stages:
            inputs = _inputs_for(stage)
            if inputs and not stage.get("required_inputs"):
                stage["required_inputs"] = inputs
                changed = True
        if not changed:
            continue
        new_version = int(row["version"] or 1) + 1
        conn.execute(
            text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
            {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
        )
        snapshot_template_version(
            conn, row["id"], new_version, "Review, verify and gate declare their inputs"
        )
