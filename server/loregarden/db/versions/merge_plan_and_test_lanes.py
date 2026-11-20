"""Fold plan-synthesis into plan, and test-break into test-design, on the v3 template.

Before implement, a `studio-loregarden-tdd-v3` ticket ran about ten cold-started
agent runs. Each one re-read the ticket and re-explored the same code before it
could start (17 to 44 tool calls before its first write, median by stage, runs
since 2026-11-12). Stages before implement took 2.27B of the ~4.7B context
tokens spent on the 70 tickets that ran end to end since 2026-10-20.

Two pairs did not need to be separate runs:

- **plan** fanned out to three planners (`plan-simplest`, `plan-risk`,
  `plan-seams`) and `plan-synthesis` reconciled them: four cold starts, ~537M
  context tokens. One of 70 rework findings ever targeted `plan-synthesis`. Now
  one `planner` run argues all three cases and decides (`plan-lenses` skill),
  and carries the sign-off `plan-synthesis` had.
- **test-break** re-read everything `test-design` had just read in order to
  attack its tests. Now `test-design` designs and then attacks its own tests
  (`test-break-pass` skill), with test-break's sign-off and both timeouts.

Downstream stages read the newest `plan` artifact whatever wrote it
(`plan_context.latest_plan`), and the design-plan gate keys on the `planner`
agent (`design_plan_gate.is_design_plan_stage`), so neither cares which stage
key produced the plan.

Instances pin their template version, so an in-flight ticket keeps the stages it
started with. Tickets in `backlog` that have never run an agent are moved to the
new version, with their stage list rebuilt, so the backlog does not keep the
old shape for months. Anything that has run, or whose stage list is not all
`pending`, keeps its pin.
"""

from __future__ import annotations

import json
import logging

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)

_TEMPLATE = "studio-loregarden-tdd-v3"
_PLAN, _SYNTHESIS = "plan", "plan-synthesis"
_TEST_DESIGN, _TEST_BREAK = "test-design", "test-break"
PLAN_SKILL = "plan-lenses"
TEST_SKILL = "test-break-pass"
_PENDING = "pending"
#: The lanes ran in parallel (plan p95 997s) and synthesis after them; one run
#: now does all four jobs in sequence. Provisional: re-measure once it has run.
_PLAN_TIMEOUT_SECONDS = 2400
_BACKLOG = "backlog"


@migration("20261119_merge_plan_and_test_lanes", after="20261117_symbol_map")
def m_merge_plan_and_test_lanes(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json "
                "FROM workflow_templates WHERE slug=:s"
            ),
            {"s": _TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return
    stages = json.loads(row["stages_json"] or "[]")
    keys = {stage.get("key") for stage in stages}
    if _SYNTHESIS not in keys and _TEST_BREAK not in keys:
        return  # already merged
    if not {_PLAN, _SYNTHESIS, _TEST_DESIGN, _TEST_BREAK} <= keys:
        # A person has reshaped this template in Studio; merging half of it
        # would leave a stage graph nobody designed. Not silent: the saving
        # this migration exists for does not happen.
        logger.warning(
            "%s: %s no longer has all of plan, plan-synthesis, test-design and test-break; "
            "leaving it as it is",
            "20261119_merge_plan_and_test_lanes",
            _TEMPLATE,
        )
        return

    merged = merge_stages(stages)
    transitions = merge_transitions(json.loads(row["transitions_json"] or "[]"))
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(merged),
            "tr": json.dumps(transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    snapshot_template_version(
        conn,
        row["id"],
        new_version,
        "One planner run instead of three lanes plus synthesis; test-design attacks its own tests",
    )
    repinned = _repin_unstarted_backlog(conn, row["id"], new_version, merged)
    logger.info("%s: moved %d never-run backlog tickets to v%d", _TEMPLATE, repinned, new_version)


def merge_stages(stages: list[dict]) -> list[dict]:
    """`stages` with plan-synthesis folded into plan and test-break into test-design."""
    by_key = {stage["key"]: stage for stage in stages}
    synthesis, test_break = by_key[_SYNTHESIS], by_key[_TEST_BREAK]

    plan = dict(by_key[_PLAN])
    plan.update(
        stage_type="agent",
        agent_id="planner",
        skill_name=PLAN_SKILL,
        parallel_agents=[],
        timeout_seconds=plan.get("timeout_seconds") or _PLAN_TIMEOUT_SECONDS,
        exit_actions_enabled=synthesis.get("exit_actions_enabled", False),
        exit_actions=_renamed_actions(synthesis, plan),
    )
    test_design = dict(by_key[_TEST_DESIGN])
    test_design.update(
        skill_name=TEST_SKILL,
        exit_actions_enabled=test_break.get("exit_actions_enabled", False),
        exit_actions=_renamed_actions(test_break, test_design),
    )
    timeouts = [s.get("timeout_seconds") for s in (by_key[_TEST_DESIGN], test_break)]
    if all(timeouts):
        test_design["timeout_seconds"] = sum(timeouts)

    replaced = {_PLAN: plan, _TEST_DESIGN: test_design}
    kept = [
        replaced.get(stage["key"], stage)
        for stage in stages
        if stage["key"] not in (_SYNTHESIS, _TEST_BREAK)
    ]
    ordered = sorted(kept, key=lambda stage: stage.get("order", 0))
    return [{**stage, "order": index} for index, stage in enumerate(ordered, start=1)]


def _renamed_actions(source: dict, target: dict) -> list[dict]:
    """`source`'s exit actions, with its stage name in their text replaced by `target`'s.

    The sign-off reads "Approve Plan synthesis completion"; on the merged stage
    it should name the stage a person is actually approving.
    """
    old, new = source.get("name", ""), target.get("name", "")
    if not old or not new:
        return json.loads(json.dumps(source.get("exit_actions", [])))
    return json.loads(json.dumps(source.get("exit_actions", [])).replace(old, new))


def merge_transitions(transitions: list[dict]) -> list[dict]:
    """`transitions` re-pointed past the two removed stages, duplicates dropped.

    plan -> plan-synthesis and test-design -> test-break become self-loops and
    go; plan-synthesis -> ui-design (on pass) becomes plan -> ui-design;
    test-break -> spec (on reject) duplicates test-design -> spec and goes.
    """
    renamed = {_SYNTHESIS: _PLAN, _TEST_BREAK: _TEST_DESIGN}
    out: list[dict] = []
    for edge in transitions:
        moved = {
            **edge,
            "from": renamed.get(edge["from"], edge["from"]),
            "to": renamed.get(edge["to"], edge["to"]),
        }
        if moved["from"] != moved["to"] and moved not in out:
            out.append(moved)
    return out


def _repin_unstarted_backlog(
    conn: Connection, template_id: str, version: int, stages: list[dict]
) -> int:
    if not table_exists(conn, "workflow_instances") or not table_exists(conn, "tickets"):
        return 0
    has_runs = table_exists(conn, "agent_runs")
    rows = (
        conn.execute(
            text(
                "SELECT i.id, i.stages_json, i.current_stage_key FROM workflow_instances i "
                "JOIN tickets t ON t.id = i.ticket_id "
                "WHERE i.template_id = :tid AND t.state = :backlog "
                "AND (i.template_version IS NULL OR i.template_version < :v)"
                + (
                    " AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.ticket_id = t.id)"
                    if has_runs
                    else ""
                )
            ),
            {"tid": template_id, "backlog": _BACKLOG, "v": version},
        )
        .mappings()
        .fetchall()
    )
    keys = [stage["key"] for stage in stages]
    fresh = json.dumps([{"key": key, "status": _PENDING} for key in keys])
    moved = 0
    for row in rows:
        statuses = json.loads(row["stages_json"] or "[]")
        if any(entry.get("status") != _PENDING for entry in statuses):
            continue
        if row["current_stage_key"] and row["current_stage_key"] not in keys:
            continue
        conn.execute(
            text("UPDATE workflow_instances SET template_version=:v, stages_json=:st WHERE id=:id"),
            {"v": version, "st": fresh, "id": row["id"]},
        )
        moved += 1
    return moved
