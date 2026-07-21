"""Migrations that reshape workflow templates, rather than the schema.

Split out of `migrations.py`, which had grown past the organization gate's
limit. The division is by what a migration changes: DDL there, the content of
`workflow_templates.stages_json` here. They share `_snapshot_template_version`,
which is what made them a cluster rather than an arbitrary cut.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def _snapshot_template_version(
    conn: Connection, template_id: str, version: int, change_note: str
) -> None:
    """Record a template version snapshot, matching what a Studio edit writes.

    Columns are listed explicitly rather than relying on defaults: create_all
    builds these tables from the models, where a Python default renders as NOT
    NULL with no DDL default.
    """
    if not table_exists(conn, "workflow_template_versions"):
        return
    snapshot = (
        conn.execute(
            text(
                "SELECT slug, name, description, stages_json, transitions_json, source_path, "
                "built_in FROM workflow_templates WHERE id=:id"
            ),
            {"id": template_id},
        )
        .mappings()
        .fetchone()
    )
    conn.execute(
        text(
            "INSERT INTO workflow_template_versions "
            "(id, template_id, version, snapshot_json, created_by, change_note, created_at) "
            "VALUES (:id, :tid, :v, :snap, 'migration', :note, :now)"
        ),
        {
            "id": str(uuid4()),
            "tid": template_id,
            "v": version,
            "snap": json.dumps(dict(snapshot)),
            "note": change_note,
            "now": datetime.now(timezone.utc),
        },
    )


_EVIDENCE_TEMPLATE = "studio-loregarden-tdd-v3"
_EVIDENCE_STAGE = "verify"
_EVIDENCE_TOOL = "loregarden_attach_evidence"


def m_require_verify_evidence(conn: Connection) -> None:
    """Make the verify stage produce a verdict rather than assert one.

    verify exists to check a stage's done-claim, so a verify that advances
    without recording what it found is the same unverified pass it was added to
    prevent. Requiring verify_verdict is the narrowest place to start: it is the
    one stage whose entire job is producing that artifact.

    Deliberately not requiring real_surface anywhere yet. That would block
    implement until agents habitually capture output from the running system,
    which is a behaviour change to roll out once verify is proven.
    """
    if not table_exists(conn, "workflow_templates"):
        return

    # Grant the tool first. A stage required to record evidence without the tool
    # to record it is blocked with no way to comply, and agent tool grants are
    # stored per row rather than read from the defaults.
    if table_exists(conn, "studio_agents"):
        for row in (
            conn.execute(text("SELECT id, mcp_tools_json FROM studio_agents")).mappings().all()
        ):
            tools = json.loads(row["mcp_tools_json"] or "[]")
            if not isinstance(tools, list) or _EVIDENCE_TOOL in tools:
                continue
            tools.append(_EVIDENCE_TOOL)
            conn.execute(
                text("UPDATE studio_agents SET mcp_tools_json=:t WHERE id=:id"),
                {"t": json.dumps(tools), "id": row["id"]},
            )

    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _EVIDENCE_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    verify = next((s for s in stages if s.get("key") == _EVIDENCE_STAGE), None)
    if verify is None or verify.get("required_evidence"):
        return
    verify["required_evidence"] = ["verify_verdict"]

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Verify must record a verdict")


_IMPLEMENT_STAGE = "implement"


def m_require_implement_real_surface(conn: Connection) -> None:
    """Make implement show the change working, not just that its tests pass.

    Green tests say the code does what its tests say. They do not say the
    feature works on the surface a user touches, and that second claim is the
    one nothing has ever checked.

    Light work is exempt at gate time rather than here: triage decides that per
    ticket, so the requirement stays on the stage and the waiver is applied when
    it runs.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _EVIDENCE_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    implement = next((s for s in stages if s.get("key") == _IMPLEMENT_STAGE), None)
    if implement is None or implement.get("required_evidence"):
        return
    implement["required_evidence"] = ["real_surface"]

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Implement must show it working")


_REVIEW_TEMPLATE = "studio-loregarden-tdd-v3"
_REVIEW_KEY = "review"
# One lane per lens. They run concurrently and any rejection sends the work back,
# so these are independent readings of the same diff rather than a chain: a
# reviewer looking for coupling is not also looking for injection.
_REVIEW_LANES = [
    ("architecture_reviewer", ""),
    ("static_qa", ""),
    ("security_reviewer", ""),
]


def m_parallel_review_in_v3(conn: Connection) -> None:
    """Review the diff from several angles at once instead of one.

    The stage was a classify with a single default route, so every ticket got
    exactly one reviewer and whatever that reviewer was not looking for went
    unreviewed.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _REVIEW_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    review = next((s for s in stages if s.get("key") == _REVIEW_KEY), None)
    if review is None or review.get("stage_type") == "parallel":
        return

    review["stage_type"] = "parallel"
    review["parallel_agents"] = [
        {"agent_id": agent_id, "skill_name": skill} for agent_id, skill in _REVIEW_LANES
    ]
    # A parallel stage resolves its agents from parallel_agents; leaving the old
    # single route behind would be a second, contradictory answer to the same
    # question.
    review["classify_routes"] = []

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Parallel multi-angle review")


_VERIFY_TEMPLATE = "studio-loregarden-tdd-v3"
_VERIFY_AFTER = "implement"
_VERIFY_KEY = "verify"


def m_verify_stage_in_v3(conn: Connection) -> None:
    """Put an independent verify stage between implement and review on v3.

    A stage closing on its own outcome=pass is what verify exists to check, so it
    sits directly after the stage that makes the claim and routes back to it on a
    refusal. Both the light and heavy triage paths converge on implement, so one
    stage covers both.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json FROM workflow_templates WHERE slug=:s"
            ),
            {"s": _VERIFY_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {s.get("key"): s for s in stages}
    if _VERIFY_KEY in by_key or _VERIFY_AFTER not in by_key:
        return  # already wired, or this template does not have the anchor stage

    anchor_order = int(by_key[_VERIFY_AFTER].get("order") or 0)
    for stage in stages:
        if int(stage.get("order") or 0) > anchor_order:
            stage["order"] = int(stage["order"]) + 1
    stages.append(
        {
            "key": _VERIFY_KEY,
            "name": "Verify",
            "agent_id": "verifier",
            "skill_name": "verify",
            "optional": False,
            "order": anchor_order + 1,
            "stage_type": "verify",
            # Light work skips verification. Triage already decided the ticket was
            # trivial enough to branch past planning; demanding runtime proof of a
            # typo fix spends more than the check is worth.
            "skip_when": "routed_as_light_work",
            "classify_routes": [],
            "parallel_agents": [],
            "gate_commands": [],
            "gate_required": False,
            "model": "",
        }
    )
    stages.sort(key=lambda s: int(s.get("order") or 0))

    # Re-point whatever implement used to advance to, then add the verdict edges.
    transitions = json.loads(row["transitions_json"] or "[]")
    downstream = ""
    for item in transitions:
        if item.get("from") == _VERIFY_AFTER and item.get("when", "") in {"", "pass", "default"}:
            downstream = item.get("to", "")
            item["to"] = _VERIFY_KEY
    if downstream:
        transitions.append({"from": _VERIFY_KEY, "to": downstream, "when": "pass"})
    transitions.append({"from": _VERIFY_KEY, "to": _VERIFY_AFTER, "when": "reject"})

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(stages),
            "tr": json.dumps(transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    _snapshot_template_version(conn, row["id"], new_version, "Verify stage after implement")
    _backfill_verify_into_instances(
        conn, row["id"], {s["key"]: int(s.get("order") or 0) for s in stages}
    )


def _backfill_verify_into_instances(
    conn: Connection, template_id: str, stage_orders: dict[str, int]
) -> None:
    """Add the new stage to live instances without stranding or rewinding them.

    A required stage inserted mid-pipeline is PENDING for every in-flight ticket,
    which both blocks DONE (nothing ever resolves it) and pulls the cursor
    backwards, since the orchestrator runs the earliest pending stage.

    Whether a ticket has already passed the insertion point is decided by where
    its cursor sits, not by one stage's recorded status: a ticket can reach
    review with implement left un-marked after a reroute, and keying off that
    would hand it a pending verify it should never run.
    """
    if not table_exists(conn, "workflow_instances"):
        return
    verify_order = stage_orders.get(_VERIFY_KEY, 0)
    rows = (
        conn.execute(
            text(
                "SELECT id, stages_json, current_stage_key FROM workflow_instances "
                "WHERE template_id=:tid"
            ),
            {"tid": template_id},
        )
        .mappings()
        .fetchall()
    )
    for row in rows:
        entries = json.loads(row["stages_json"] or "[]")
        if any(e.get("key") == _VERIFY_KEY for e in entries):
            continue
        cursor_order = stage_orders.get(row["current_stage_key"] or "", 0)
        already_past = cursor_order > verify_order
        entries.append({"key": _VERIFY_KEY, "status": "wont_do" if already_past else "pending"})
        conn.execute(
            text("UPDATE workflow_instances SET stages_json=:st WHERE id=:id"),
            {"st": json.dumps(entries), "id": row["id"]},
        )


# Keywords that mark a ticket trivial enough to skip planning. Deliberately rare
# words: the classifier is a bag-of-words match over title + description +
# acceptance criteria, so a term that shows up incidentally in a risky ticket
# would skip planning for it. HEAVY is the default, so an unmatched ticket keeps
# the full pipeline — rigor ratchets up, never quietly down.
_LIGHT_WORK_KEYWORDS = [
    "typo",
    "docs",
    "documentation",
    "changelog",
    "comment",
    "formatting",
    "lint",
]

_RIGOR_TRIAGE_TEMPLATE = "studio-loregarden-tdd-v3"
_RIGOR_LIGHT_TARGET = "test-design"


def m_light_heavy_rigor_triage(conn: Connection) -> None:
    """Scale pipeline rigor by change risk on the loregarden TDD v3 template.

    Turns `triage` into a classify stage whose light route branches past
    plan/ui-design/spec, and lets `spec` skip itself when the ticket already
    carries acceptance criteria. Composed from the route `to_stage` and stage
    `skip_when` primitives; no engine change.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:slug"),
            {"slug": _RIGOR_TRIAGE_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {stage.get("key"): stage for stage in stages}
    triage, spec = by_key.get("triage"), by_key.get("spec")
    if not triage or not spec or _RIGOR_LIGHT_TARGET not in by_key:
        return
    if triage.get("stage_type") == "classify":
        return  # already reshaped

    agent_id = triage.get("agent_id") or "ticket_scoper"
    skill_name = triage.get("skill_name", "")
    triage["stage_type"] = "classify"
    triage["classify_routes"] = [
        {
            "languages": [],
            "specialties": _LIGHT_WORK_KEYWORDS,
            "agent_id": agent_id,
            "skill_name": skill_name,
            "default": False,
            "to_stage": _RIGOR_LIGHT_TARGET,
        },
        {
            "languages": [],
            "specialties": [],
            "agent_id": agent_id,
            "skill_name": skill_name,
            "default": True,
            "to_stage": "",
        },
    ]
    spec["skip_when"] = "has_acceptance_criteria"

    # Bump the version so this reshape is auditable alongside Studio edits. The
    # pre-existing v1 snapshot still holds the linear shape, so history is intact.
    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:stages, version=:v WHERE id=:id"),
        {"stages": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "LIGHT/HEAVY rigor triage")


_REFACTOR_TEMPLATE = "studio-loregarden-tdd-v3"
_REFACTOR_SKILL = "refactor"
# One route per implementer, because the skill is orthogonal to who runs it: a
# refactor still belongs to whoever owns that half of the codebase.
#
# Backend is listed first on purpose. A route's specialties are OR-matched, so
# the frontend lane also fires on a bare refactor word and would win every tie
# on position alone — sending backend refactors to the frontend agent. Ordered
# this way a tie falls to backend, and a genuinely frontend refactor still wins
# outright on the extra specialty hit.
_REFACTOR_ROUTES = [
    {
        "languages": [],
        "specialties": ["refactor"],
        "agent_id": "backend_implementer",
        "skill_name": _REFACTOR_SKILL,
        "default": False,
        "to_stage": "",
    },
    {
        "languages": ["typescript", "javascript"],
        "specialties": ["refactor", "frontend"],
        "agent_id": "frontend_implementer",
        "skill_name": _REFACTOR_SKILL,
        "default": False,
        "to_stage": "",
    },
]


def m_refactor_skill_routes(conn: Connection) -> None:
    """Give restructuring work a method instead of leaving it to improvisation.

    Refactors ran through the plain implementer route, so nothing told an agent
    to establish a behavior baseline or find every reference before moving code.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _REFACTOR_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    implement = next((s for s in stages if s.get("key") == _IMPLEMENT_STAGE), None)
    if implement is None:
        return
    routes = implement.get("classify_routes") or []
    if any(r.get("skill_name") == _REFACTOR_SKILL for r in routes):
        return

    # Last among the scored lanes, immediately before the fallback. Specialties
    # are OR-matched, so the frontend refactor lane also fires on a bare "modal"
    # or "tab" — ahead of the plain frontend lane it would tie on that single
    # hit and steal ordinary UI work on position alone. Placed behind it, a
    # refactor lane can only win by matching strictly more, which takes an
    # actual refactor word.
    insert_at = next((i for i, r in enumerate(routes) if r.get("default")), len(routes))
    implement["classify_routes"] = routes[:insert_at] + _REFACTOR_ROUTES + routes[insert_at:]

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(
        conn, row["id"], new_version, "Route refactors to the refactor skill"
    )


_PLAN_TEMPLATE = "studio-loregarden-tdd-v3"
_PLAN_STAGE = "plan"
_PLAN_SKILL = "plan"


def m_plan_skill_on_plan_stage(conn: Connection) -> None:
    """Point the plan stage at the skill that tells it to attach its plan.

    The stage declared no skill, so nothing told the planner where its output
    should go, and the plan survived only inside a run-log transcript no later
    stage reads.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug=:s"),
            {"s": _PLAN_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    plan = next((s for s in stages if s.get("key") == _PLAN_STAGE), None)
    # An operator who set their own skill here meant it; only fill the gap.
    if plan is None or (plan.get("skill_name") or "").strip():
        return
    plan["skill_name"] = _PLAN_SKILL

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    _snapshot_template_version(conn, row["id"], new_version, "Plan stage attaches its plan")


_HYPERPLAN_TEMPLATE = "studio-loregarden-tdd-v3"
_HYPERPLAN_STAGE = "plan"
_HYPERPLAN_SYNTHESIS_KEY = "plan-synthesis"
# Three lenses, one agent. They differ by skill rather than by role because the
# argument each makes is a way of reading the ticket, not a different job — and
# three near-identical role bodies would drift apart the moment one is edited.
_HYPERPLAN_LANES = [
    ("planner", "plan-simplest"),
    ("planner", "plan-risk"),
    ("planner", "plan-seams"),
]


def m_adversarial_planning(conn: Connection) -> None:
    """Plan from three angles at once, then reconcile them into one plan.

    A single planner's first plausible approach became the plan, and nothing
    argued the other side of it. Fanning out costs three runs; the synthesis
    stage is what makes them worth more than one, by forcing the disagreements
    to be settled before spec and test-design build on either answer.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text(
                "SELECT id, version, stages_json, transitions_json "
                "FROM workflow_templates WHERE slug=:s"
            ),
            {"s": _HYPERPLAN_TEMPLATE},
        )
        .mappings()
        .fetchone()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    by_key = {s.get("key"): s for s in stages}
    plan = by_key.get(_HYPERPLAN_STAGE)
    if plan is None or _HYPERPLAN_SYNTHESIS_KEY in by_key:
        return
    if plan.get("stage_type") == "parallel":
        return

    plan["stage_type"] = "parallel"
    plan["parallel_agents"] = [
        {"agent_id": agent_id, "skill_name": skill} for agent_id, skill in _HYPERPLAN_LANES
    ]
    # Each lane names its own lens, so the stage-level skill is now dead weight.
    # Only the value 0031 set is cleared: anything else was an operator's choice,
    # and undoing just our own default is the same restraint 0019 used.
    if (plan.get("skill_name") or "") == _PLAN_SKILL:
        plan["skill_name"] = ""

    plan_order = int(plan.get("order") or 0)
    for stage in stages:
        if int(stage.get("order") or 0) > plan_order:
            stage["order"] = int(stage["order"]) + 1
    stages.append(
        {
            "key": _HYPERPLAN_SYNTHESIS_KEY,
            "name": "Plan synthesis",
            "agent_id": "planner",
            "skill_name": "plan-synthesis",
            "optional": False,
            "order": plan_order + 1,
            "stage_type": "agent",
            "classify_routes": [],
            "parallel_agents": [],
            "gate_commands": [],
            "gate_required": False,
            "model": "",
        }
    )
    stages.sort(key=lambda s: int(s.get("order") or 0))

    # Whatever plan advanced to now sits behind synthesis, so the settled plan
    # exists before anything downstream reads one.
    transitions = json.loads(row["transitions_json"] or "[]")
    downstream = ""
    for item in transitions:
        if item.get("from") == _HYPERPLAN_STAGE and item.get("when", "") in {"", "pass", "default"}:
            downstream = item.get("to", "")
            item["to"] = _HYPERPLAN_SYNTHESIS_KEY
    if downstream:
        transitions.append({"from": _HYPERPLAN_SYNTHESIS_KEY, "to": downstream, "when": "pass"})

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text(
            "UPDATE workflow_templates SET stages_json=:st, transitions_json=:tr, version=:v "
            "WHERE id=:id"
        ),
        {
            "st": json.dumps(stages),
            "tr": json.dumps(transitions),
            "v": new_version,
            "id": row["id"],
        },
    )
    _snapshot_template_version(conn, row["id"], new_version, "Adversarial planning")
