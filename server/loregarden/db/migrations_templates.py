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
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_utils import table_exists
from loregarden.services.skill_service import skill_seed_root
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


def _has_terminal_stage(stages: list[dict]) -> bool:
    """A workflow can only finalize on a terminal stage — the `terminal` flag, or
    the `done` key as the historical fallback (mirrors studio_routing.is_terminal_stage)."""
    return any(bool(s.get("terminal")) or s.get("key") == "done" for s in stages)


def _terminal_done_stage(order: int) -> dict:
    """A terminal, agentless `done` stage the orchestrator finalizes the ticket on."""
    return {
        "key": "done",
        "name": "Done",
        "agent_id": "",
        "skill_name": "",
        "optional": False,
        "order": order,
        "stage_type": "agent",
        "terminal": True,
        "classify_routes": [],
        "parallel_agents": [],
        "gate_commands": [],
        "gate_required": False,
        "model": "",
    }


def m_ensure_terminal_stage(conn: Connection) -> None:
    """Every workflow template must end at a terminal stage the orchestrator can
    finalize on. The studio-loregarden-tdd v2/v3 templates end at `gate`, which is
    not terminal and has no pass-route, so a passing final gate has nowhere to
    advance: the pipeline re-loops through implement/verify/review instead of
    marking the ticket done. Append a terminal `done` stage to any template that
    lacks one and route its current last stage into it on pass.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    rows = (
        conn.execute(
            text("SELECT id, slug, version, stages_json, transitions_json FROM workflow_templates")
        )
        .mappings()
        .fetchall()
    )
    for row in rows:
        stages = json.loads(row["stages_json"] or "[]")
        if not stages or _has_terminal_stage(stages):
            continue
        last = max(stages, key=lambda s: int(s.get("order") or 0))
        stages.append(_terminal_done_stage(int(last.get("order") or 0) + 1))

        transitions = json.loads(row["transitions_json"] or "[]")
        # Route the old last stage to `done` on a clean pass, alongside whatever
        # reject edge it already has (e.g. gate -> implement on reject).
        transitions.append({"from": last["key"], "to": "done", "when": "pass"})

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
        _snapshot_template_version(conn, row["id"], new_version, "Terminal done stage")


#: Skill names that were declared on templates/drafts but never existed as
#: `agent_context/skills/*/SKILL.md` files. Clearing them is required before
#: missing-skill resolution becomes fatal — otherwise live stages (notably
#: `verify` on studio-loregarden-tdd-v3) brick on the loud-failure path.
_PHANTOM_SKILL_NAMES = frozenset(
    {
        "verify",
        "consult",
        "spec",
        "test_design",
        "test_break",
        "apply_patch",
        "static_qa",
        "index_repo",
        "run_tests",
        "ac_gate",
        "learning",
        "review",
    }
)


#: Every place inside a stage that may name a skill: the stage itself, and each
#: entry of these fan-out / routing slots.
_SKILL_SLOT_KEYS = ("agents", "parallel_agents", "classify_routes")


def _skill_holders(stage: dict) -> list[dict]:
    """The stage plus every fan-out/route entry that carries its own skill name."""
    holders = [stage]
    for slot in _SKILL_SLOT_KEYS:
        holders.extend(stage.get(slot) or [])
    return holders


def _clear_skill_names(stages: list[dict], unknown: Callable[[str], bool]) -> bool:
    """Blank, in place, every skill name `unknown` rejects. True if anything changed."""
    changed = False
    for stage in stages:
        for holder in _skill_holders(stage):
            if unknown(holder.get("skill_name") or ""):
                holder["skill_name"] = ""
                changed = True
    return changed


def _clear_phantom_skill_slots(stages: list[dict]) -> bool:
    return _clear_skill_names(stages, lambda name: name in _PHANTOM_SKILL_NAMES)


def m_clear_phantom_skill_names(conn: Connection) -> None:
    """Strip undeclared skill names from templates and studio drafts.

    Paired with the loud SkillNotFoundError path: clearing phantoms and failing
    loudly must land together, or every stage that still names a phantom dies.
    """
    if table_exists(conn, "workflow_templates"):
        rows = (
            conn.execute(text("SELECT id, stages_json, version FROM workflow_templates"))
            .mappings()
            .all()
        )
        for row in rows:
            stages = json.loads(row["stages_json"] or "[]")
            if not _clear_phantom_skill_slots(stages):
                continue
            new_version = int(row["version"] or 1) + 1
            conn.execute(
                text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
                {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
            )
            _snapshot_template_version(conn, row["id"], new_version, "Clear phantom skill names")

    if table_exists(conn, "studio_workflows"):
        rows = conn.execute(text("SELECT id, stages_json FROM studio_workflows")).mappings().all()
        for row in rows:
            stages = json.loads(row["stages_json"] or "[]")
            if not _clear_phantom_skill_slots(stages):
                continue
            conn.execute(
                text("UPDATE studio_workflows SET stages_json=:st WHERE id=:id"),
                {"st": json.dumps(stages), "id": row["id"]},
            )


#: The two playtest items this migration retires, matched on their exact text so
#: a checklist an operator has since edited is left alone.
_RETIRED_PLAYTEST_ITEMS = (
    "Create or update the test level scene(s) needed to exercise this change",
    "Load the affected scene(s) in the Godot editor and run them",
)

_PLAYTEST_SCENES_PLACEHOLDER = "{{playtest_scenes}}"


def _rewrite_playtest_checklist(stages: list[dict]) -> bool:
    """Swap the two static scene items for the `{{playtest_scenes}}` placeholder.

    The first item asked a human to author the test scene at a sign-off gate,
    where nothing is built — that is now briefed to the last authoring stage
    ahead of the gate. The second told them to "load the affected scene(s)"
    without saying which; the placeholder expands to the scenes the ticket's
    branch actually changes.
    """
    changed = False
    for stage in stages:
        checklist = stage.get("checklist") or []
        if not any(item in _RETIRED_PLAYTEST_ITEMS for item in checklist):
            continue
        rewritten: list[str] = []
        for item in checklist:
            if item not in _RETIRED_PLAYTEST_ITEMS:
                rewritten.append(item)
            elif _PLAYTEST_SCENES_PLACEHOLDER not in rewritten:
                rewritten.append(_PLAYTEST_SCENES_PLACEHOLDER)
        stage["checklist"] = rewritten
        changed = True
    return changed


def m_playtest_scene_placeholder(conn: Connection) -> None:
    """Retire the two hand-written playtest scene items across live templates."""
    if not table_exists(conn, "workflow_templates"):
        return
    rows = (
        conn.execute(text("SELECT id, stages_json, version FROM workflow_templates"))
        .mappings()
        .all()
    )
    for row in rows:
        stages = json.loads(row["stages_json"] or "[]")
        if not _rewrite_playtest_checklist(stages):
            continue
        new_version = int(row["version"] or 1) + 1
        conn.execute(
            text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
            {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
        )
        _snapshot_template_version(conn, row["id"], new_version, "Playtest scene placeholder")


#: Playtest items an agent stage already covers. The AC placeholder duplicates
#: `ac_gate`, which evidences every criterion one stage earlier; the regression
#: sweep is what the review stage exists to do; console output is observable
#: from the implementer's own run. All three asked a human to redo work that
#: had already been signed off, or to do work nobody had been assigned.
_AGENT_OWNED_PLAYTEST_ITEMS = (
    "{{acceptance_criteria}}",
    "Check for regressions in adjacent systems the change touches",
    "Confirm no console errors/warnings appear during play",
)

_TICKET_INTENT_PLACEHOLDER = "{{ticket_intent}}"

#: Where each retired duty lands. Keyed by stage key, since the two stages that
#: inherit them are the ones that should have owned them all along.
_STAGE_BRIEFS = {
    "implementation": (
        "Your run must end with no console errors or warnings, and you must say so with "
        "the evidence that shows it. The playtest gate no longer asks a human to watch "
        "the output for you."
    ),
    "script_review": (
        "Hunt regressions in the adjacent systems this change touches, not just defects "
        "in the lines it changed. That sweep used to be a bullet on the playtest "
        "checklist, where a human was asked to do it by eye after the fact; it is yours "
        "now."
    ),
}


def _retire_agent_owned_playtest_items(stages: list[dict]) -> bool:
    """Strip the agent-owned items from human gates and brief the owning stages.

    What survives on the gate is what no agent can sign off: which scenes to
    open, and whether the change delivers what the ticket asked for.
    """
    changed = False
    for stage in stages:
        checklist = stage.get("checklist") or []
        if not any(item in _AGENT_OWNED_PLAYTEST_ITEMS for item in checklist):
            continue
        kept = [item for item in checklist if item not in _AGENT_OWNED_PLAYTEST_ITEMS]
        if _TICKET_INTENT_PLACEHOLDER not in kept:
            kept.append(_TICKET_INTENT_PLACEHOLDER)
        stage["checklist"] = kept
        changed = True

    if not changed:
        return False
    for stage in stages:
        brief = _STAGE_BRIEFS.get(stage.get("key") or "")
        if brief and not (stage.get("stage_brief") or "").strip():
            stage["stage_brief"] = brief
    return True


def m_retire_agent_owned_gate_items(conn: Connection) -> None:
    """Move every mechanically-checkable playtest item onto the stage that owns it."""
    if not table_exists(conn, "workflow_templates"):
        return
    rows = (
        conn.execute(text("SELECT id, stages_json, version FROM workflow_templates"))
        .mappings()
        .all()
    )
    for row in rows:
        stages = json.loads(row["stages_json"] or "[]")
        if not _retire_agent_owned_playtest_items(stages):
            continue
        new_version = int(row["version"] or 1) + 1
        conn.execute(
            text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
            {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
        )
        _snapshot_template_version(conn, row["id"], new_version, "Retire agent-owned gate items")


def _template_stages_by_version(conn: Connection, template_id: str) -> dict[int, list[dict]]:
    """Every stage list this template has ever been pinned at, keyed by version.

    Read from `workflow_template_versions.snapshot_json` — which holds
    `stages_json` as a JSON *string nested inside it*, not as a column. A query
    that selects a bare `stages_json` alongside these rows silently resolves it
    against `workflow_templates` and hands back the current template for every
    version, making a broken snapshot look correct.

    The live template is overlaid at its own version last: it is authoritative
    for head even if a snapshot row for head drifted or was never written.
    """
    by_version: dict[int, list[dict]] = {}
    if table_exists(conn, "workflow_template_versions"):
        rows = (
            conn.execute(
                text(
                    "SELECT version, snapshot_json FROM workflow_template_versions "
                    "WHERE template_id=:id"
                ),
                {"id": template_id},
            )
            .mappings()
            .all()
        )
        for row in rows:
            snapshot = json.loads(row["snapshot_json"] or "{}")
            by_version[int(row["version"])] = json.loads(snapshot.get("stages_json") or "[]")

    live = (
        conn.execute(
            text("SELECT version, stages_json FROM workflow_templates WHERE id=:id"),
            {"id": template_id},
        )
        .mappings()
        .fetchone()
    )
    if live is not None:
        by_version[int(live["version"] or 1)] = json.loads(live["stages_json"] or "[]")
    return by_version


def _terminal_only_successor(
    pinned_version: int, pinned: list[dict], by_version: dict[int, list[dict]]
) -> int | None:
    """Lowest version *after* `pinned_version` that is `pinned` plus a terminal
    stage and nothing else.

    The equality is on the whole stage list, not just the keys: repinning is only
    safe when the ticket keeps running the *same* definitions it started under.
    A version that also renamed an agent, added a gate command, or reordered a
    stage is not a candidate, and such an instance is left pinned where it is.
    """
    for version in sorted(by_version):
        if version <= pinned_version:
            continue
        candidate = by_version[version]
        if len(candidate) <= len(pinned) or candidate[: len(pinned)] != pinned:
            continue
        added = candidate[len(pinned) :]
        if all(_has_terminal_stage([stage]) for stage in added):
            return version
    return None


def m_repin_terminal_less_instances(conn: Connection) -> None:
    """Move workflow instances off a pinned version that has no terminal stage.

    `m_ensure_terminal_stage` appended a terminal `done` stage to every template
    that ended at `gate`, but only to the templates. Instances already pinned to
    the version *before* that fix kept resolving the terminal-less snapshot, so a
    passing final gate had nowhere to advance to and the pipeline re-looped
    through implement/verify/review instead of finishing. On the live database
    that is 120 instances pinned to `studio-loregarden-tdd-v3` version 9, 103 of
    them on tickets that have not reached done/wont_do.

    Repinning rather than backfilling, deliberately. The two options give up
    different guarantees:

    * Backfilling a `done` stage into version 9's snapshot would fix every
      affected instance in one write and touch no pins — but it rewrites an
      applied, immutable version snapshot. Pinning only means something because
      a version, once written, is what it was; a migration that edits history
      makes every other pin unverifiable and would make "the ticket runs the
      definition it started under" a claim no one can check.
    * Repinning gives up a weaker promise: the affected tickets run version 10
      rather than the version 9 they started under. Here that costs nothing
      measurable, because version 10 *is* version 9 plus the terminal `done`
      stage and the `gate -> done` pass edge — every stage definition the ticket
      started under is byte-identical.

    So the migration does not trust that shape, it *requires* it:
    `_terminal_only_successor` only accepts a version that is the pinned stage
    list plus terminal stages and nothing else. An instance with no such
    successor is left alone rather than silently moved onto a different
    workflow — visible, and a smaller problem than a wrong pin.

    Idempotent: a repinned instance now resolves a terminal stage and is skipped
    on any later run, as is every instance that was already pinned correctly.
    """
    if not table_exists(conn, "workflow_instances") or not table_exists(conn, "workflow_templates"):
        return
    rows = (
        conn.execute(
            text(
                "SELECT id, template_id, template_version FROM workflow_instances "
                "WHERE template_version IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    stages_cache: dict[str, dict[int, list[dict]]] = {}
    for row in rows:
        template_id = row["template_id"]
        if template_id not in stages_cache:
            stages_cache[template_id] = _template_stages_by_version(conn, template_id)
        by_version = stages_cache[template_id]
        pinned_version = int(row["template_version"])
        # A pin with no snapshot falls back to the live template at read time,
        # which m_ensure_terminal_stage already fixed. Nothing to repin.
        pinned = by_version.get(pinned_version)
        if pinned is None or _has_terminal_stage(pinned):
            continue
        target = _terminal_only_successor(pinned_version, pinned, by_version)
        if target is None:
            continue
        conn.execute(
            text("UPDATE workflow_instances SET template_version=:v WHERE id=:id"),
            {"v": target, "id": row["id"]},
        )


def _registered_skill_slugs(conn: Connection) -> frozenset[str]:
    """Every skill name that resolves today, read the way `get_skill` resolves one.

    `skills.registry.get_skill` looks in the `skills` table, and on a miss seeds
    the table from `agent_context/skills/*/SKILL.md` and looks again. A name is
    therefore registered if it is in the table *or* has a seedable directory, and
    a migration that consulted only one of the two would call a name phantom on a
    database that simply had not been seeded yet.

    Deliberately not a hardcoded list: a copy of the skill names goes stale the
    first time someone adds or removes one, and 0068's frozen `_PHANTOM_SKILL_NAMES`
    is the evidence — it was already incomplete by the time this ran.
    """
    slugs: set[str] = set()
    if table_exists(conn, "skills"):
        slugs.update(row[0] for row in conn.execute(text("SELECT slug FROM skills")) if row[0])
    seed_root = skill_seed_root()
    if seed_root.is_dir():
        slugs.update(child.name for child in seed_root.iterdir() if (child / "SKILL.md").is_file())
    return frozenset(slugs)


def _names_unregistered_skill(stages: list[dict], registered: frozenset[str]) -> bool:
    """Whether any stage (or fan-out/route entry) names a skill nothing can resolve."""
    return any(
        bool(holder.get("skill_name")) and holder["skill_name"] not in registered
        for stage in stages
        for holder in _skill_holders(stage)
    )


def _skill_clearing_successor(
    pinned_version: int,
    pinned: list[dict],
    by_version: dict[int, list[dict]],
    registered: frozenset[str],
) -> int | None:
    """Lowest version after `pinned_version` that is `pinned` with its unresolvable
    skill names blanked — plus, at most, appended terminal stages — and nothing else.

    Composed with 0088's rule rather than replacing it: an instance can be stranded
    on both defects at once, and the terminal-stage tail is the one other difference
    already established as semantically empty for a pinned ticket.

    The comparison is on whole stage dicts, so a candidate that also renamed an
    agent, reordered a stage, or changed a gate command is rejected and the
    instance stays pinned where it is. Visible beats silently moved.
    """
    normalized = json.loads(json.dumps(pinned))
    _clear_skill_names(normalized, lambda name: bool(name) and name not in registered)
    for version in sorted(by_version):
        if version <= pinned_version:
            continue
        candidate = by_version[version]
        if len(candidate) < len(normalized) or candidate[: len(normalized)] != normalized:
            continue
        if _names_unregistered_skill(candidate, registered):
            continue
        added = candidate[len(normalized) :]
        if all(_has_terminal_stage([stage]) for stage in added):
            return version
    return None


def m_repin_unregistered_skill_instances(conn: Connection) -> None:
    """Move workflow instances off a pinned version that names a skill nothing can
    resolve, so the stage dies at dispatch with `SkillNotFoundError`.

    `m_clear_phantom_skill_names` (0068) blanked those names on the *templates*, and
    every version published after it is clean. A pin, though, is a frozen snapshot:
    an instance pinned to a version written before 0068 still resolves the phantom,
    and `render_stage_prompt` raises for it on the builtin driver and the external
    harness alike. On the live database that is 257 instances pinned to
    `studio-loregarden-tdd-v3` version 10, whose `verify` stage names skill
    `'verify'` — a name that has never existed in `agent_context/skills`.

    **Why 0088 was not enough, and why this is not a fix to it.** 0088 moved
    instances off version 9, which had no terminal stage, onto the lowest successor
    that was version 9 plus terminal stages and nothing else — version 10. That rule
    is correct and stays: a repin may not change the workflow a ticket is running,
    so the *minimal* successor is the only defensible target. Version 10 happens to
    carry a second, unrelated defect (the phantom `verify` skill), which 0088
    faithfully preserved because clearing it was not 0088's difference to make.
    Widening 0088 after the fact would have been the wrong repair twice over — it is
    an applied id, and its rule is not what is broken. This is an orthogonal second
    repair with its own minimality rule: a candidate qualifies only if it differs
    from the pinned version by blanked unresolvable skill names (and, composing with
    0088, appended terminal stages) and nothing else. On the live data version 11 is
    exactly version 10 with `verify.skill_name` cleared, so it qualifies; a version
    that had also renamed an agent would not, and that instance would be left pinned
    where it is rather than silently moved onto a different workflow.

    "Registered" is resolved against the live registry — the `skills` table union
    the seedable `agent_context/skills` directories, which is how `get_skill` itself
    answers — not against a hardcoded name list that goes stale on the next skill
    someone adds.

    Idempotent: a repinned instance now resolves only registered skills and is
    skipped on any later run, as is every instance that was already clean. Snapshots
    are never rewritten; the only write is `workflow_instances.template_version`.
    """
    if not table_exists(conn, "workflow_instances") or not table_exists(conn, "workflow_templates"):
        return
    registered = _registered_skill_slugs(conn)
    if not registered:
        # No registry to judge against — an un-seeded database with the skills
        # directory absent. Every name would look phantom; repin nothing.
        return
    rows = (
        conn.execute(
            text(
                "SELECT id, template_id, template_version FROM workflow_instances "
                "WHERE template_version IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    stages_cache: dict[str, dict[int, list[dict]]] = {}
    for row in rows:
        template_id = row["template_id"]
        if template_id not in stages_cache:
            stages_cache[template_id] = _template_stages_by_version(conn, template_id)
        by_version = stages_cache[template_id]
        pinned_version = int(row["template_version"])
        # A pin with no snapshot resolves the live template at read time, which
        # 0068 already cleaned. Nothing frozen, nothing to repin.
        pinned = by_version.get(pinned_version)
        if pinned is None or not _names_unregistered_skill(pinned, registered):
            continue
        target = _skill_clearing_successor(pinned_version, pinned, by_version, registered)
        if target is None:
            continue
        conn.execute(
            text("UPDATE workflow_instances SET template_version=:v WHERE id=:id"),
            {"v": target, "id": row["id"]},
        )
