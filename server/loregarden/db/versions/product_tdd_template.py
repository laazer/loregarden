"""A template for product workspaces, and loremaker moved onto it.

Product workspaces ran `loregarden-tdd`: no triage, so every ticket was
implemented by `backend_implementer`; no verify; one `static_qa` stage and a
gatekeeper as the whole review. `product-tdd` has the shape of
`studio-loregarden-tdd-v3` without its loregarden-only parts: the `visual_qa`
lane (its role file runs loregarden's `npm run visual-qa`; loremaker's runner is
lmkr-visual-qa-4), and the brief that measures volume on the live database.

It keeps the one sign-off `loregarden-tdd` had, now on the gate, rather than
adding v3's four.

A new template rather than an edit: `loregarden-tdd` also serves lore-eden and
tinkercg. And not `WorkflowService.set_workspace_template`, which gives every
ticket on the old template a fresh all-pending stage list, started or not. Here
only never-run backlog moves; a started ticket stays on the template it began
under, which its instance names.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_template_moves import move_never_run_backlog
from loregarden.db.migrations_templates import snapshot_template_version
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261125_product_tdd_template"
TEMPLATE = "product-tdd"
WORKSPACE = "loremaker"
_PREVIOUS_TEMPLATE = "loregarden-tdd"

_UI_DESIGN_BRIEF = (
    "Decide the user-facing behaviour this ticket leaves undecided, before anyone writes it. "
    "For every surface it touches, record as acceptance criteria: the question it answers for "
    "the person using it, the action it leads to, and how it behaves at the largest realistic "
    "volume of this product's data. Then the five states: loading, empty, error, slow or in "
    "flight, and how it is reached and dismissed from the keyboard. A ticket that changes no "
    "surface a person sees needs none of that — say so in one line and pass.\n\n"
    "Record the decisions with `loregarden_update_ticket` as acceptance criteria, so the "
    "implementer is held to them and the gatekeeper can check them. Long rationale goes to "
    "`loregarden_attach_artifact`. Never write a markdown file: nothing reads it, and the "
    "orchestrator sweeps it into an unrelated commit."
)
_IMPLEMENT_BRIEF = (
    "Use the test and lint commands this repository defines (Taskfile, AGENTS.md), and say "
    "which ran with their counts. For `real_surface`, record what the running product "
    "returned: a response body, a screenshot or a console trace. This repository may have no "
    "`npm run visual-qa`; do not look for one."
)

_STAGES: list[dict] = [
    {
        "key": "triage",
        "name": "Triage",
        "order": 1,
        "stage_type": "classify",
        "agent_id": "ticket_scoper",
        "model": "sonnet",
        "classify_routes": [
            {
                "specialties": [
                    "typo",
                    "docs",
                    "documentation",
                    "changelog",
                    "comment",
                    "formatting",
                    "lint",
                ],
                "agent_id": "ticket_scoper",
                "to_stage": "test-design",
            },
            {"agent_id": "ticket_scoper", "default": True},
        ],
    },
    {
        "key": "plan",
        "name": "Plan",
        "order": 2,
        "agent_id": "planner",
        "skill_name": "plan-lenses",
        "timeout_seconds": 2400,
    },
    {
        "key": "ui-design",
        "name": "UI Design",
        "order": 3,
        "agent_id": "ui-design-decision",
        "skip_when": "no_ui_work",
        "stage_brief": _UI_DESIGN_BRIEF,
    },
    {
        "key": "spec",
        "name": "Specification",
        "order": 4,
        "agent_id": "spec",
        "skip_when": "has_acceptance_criteria",
    },
    {
        "key": "test-design",
        "name": "Design Tests",
        "order": 5,
        "agent_id": "test_designer",
        "skill_name": "test-break-pass",
        "timeout_seconds": 2700,
    },
    {
        "key": "implement",
        "name": "Implement",
        "order": 6,
        "stage_type": "classify",
        "agent_id": "backend_implementer",
        "classify_routes": [
            {
                "languages": ["typescript", "javascript"],
                "specialties": ["frontend"],
                "agent_id": "frontend_implementer",
            },
            {
                "specialties": ["refactor"],
                "agent_id": "backend_implementer",
                "skill_name": "refactor",
            },
            {
                "languages": ["typescript", "javascript"],
                "specialties": ["refactor", "frontend"],
                "agent_id": "frontend_implementer",
                "skill_name": "refactor",
            },
            {
                "languages": ["python"],
                "specialties": ["backend"],
                "agent_id": "backend_implementer",
                "default": True,
            },
        ],
        "required_evidence": ["real_surface"],
        "stage_brief": _IMPLEMENT_BRIEF,
        "timeout_seconds": 2400,
    },
    {
        "key": "verify",
        "name": "Verify",
        "order": 7,
        "stage_type": "verify",
        "agent_id": "verifier",
        "skip_when": "routed_as_light_work",
        "required_evidence": ["verify_verdict"],
        "required_inputs": ["diff", "acceptance_criteria"],
        "timeout_seconds": 1800,
    },
    {
        "key": "review",
        "name": "Code Review",
        "order": 8,
        "stage_type": "parallel",
        "agent_id": "architecture_reviewer",
        "parallel_agents": [
            {"agent_id": "architecture_reviewer"},
            {"agent_id": "static_qa"},
            {"agent_id": "security_reviewer"},
        ],
        "required_inputs": ["diff"],
        "timeout_seconds": 1200,
    },
    {
        "key": "gate",
        "name": "Quality Gate",
        "order": 9,
        "stage_type": "gate",
        "agent_id": "gatekeeper",
        "required_inputs": ["diff", "acceptance_criteria"],
        "timeout_seconds": 1200,
        "exit_actions_enabled": True,
        "exit_actions": [
            {
                "key": "operator-sign-off",
                "label": "Approve Quality Gate completion",
                "requirement": {
                    "kind": "operator_judgment",
                    "decision_prompt": "Approve completion of stage 'Quality Gate'.",
                },
            }
        ],
    },
    {"key": "done", "name": "Done", "order": 10, "agent_id": "", "terminal": True},
]

#: Every stored stage states its exit actions, as 0138 left the others.
STAGES: list[dict] = [
    {"exit_actions_enabled": False, "exit_actions": [], **stage} for stage in _STAGES
]

TRANSITIONS: list[dict] = [
    {"from": "triage", "to": "plan"},
    {"from": "plan", "to": "ui-design"},
    {"from": "ui-design", "to": "spec"},
    {"from": "spec", "to": "test-design"},
    {"from": "test-design", "to": "implement"},
    {"from": "test-design", "to": "spec", "when": "reject"},
    {"from": "implement", "to": "verify"},
    {"from": "verify", "to": "review", "when": "pass"},
    {"from": "verify", "to": "implement", "when": "reject"},
    {"from": "review", "to": "gate", "when": "pass"},
    {"from": "review", "to": "implement", "when": "reject"},
    {"from": "gate", "to": "done", "when": "pass"},
    {"from": "gate", "to": "implement", "when": "reject"},
]


@migration("20261125_product_tdd_template", after="20261124_drop_worktree_conflicts")
def m_product_tdd_template(conn: Connection) -> None:
    if not table_exists(conn, "workflow_templates"):
        return
    template_id = _id(conn, TEMPLATE)
    if template_id is not None:
        return
    template_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO workflow_templates (id, slug, name, description, stages_json, "
            "transitions_json, source_path, version, built_in, created_at) VALUES "
            "(:id, :slug, 'Product TDD', :description, :stages, :transitions, :source, 1, 1, :now)"
        ),
        {
            "id": template_id,
            "slug": TEMPLATE,
            "description": (
                "TDD for a product workspace: triage routing, an independent verify, parallel "
                "architecture, static and security review, and a signed-off quality gate."
            ),
            "stages": json.dumps(STAGES),
            "transitions": json.dumps(TRANSITIONS),
            "source": f"migration:{MIGRATION_ID}",
            "now": datetime.now(timezone.utc),
        },
    )
    snapshot_template_version(conn, template_id, 1, "Product template")
    _move_workspace(conn, template_id)


def _id(conn: Connection, slug: str) -> str | None:
    return conn.execute(
        text("SELECT id FROM workflow_templates WHERE slug=:s"), {"s": slug}
    ).scalar()


def _move_workspace(conn: Connection, template_id: str) -> None:
    """Point loremaker's new tickets, and its never-run backlog, at the template."""
    previous_id = _id(conn, _PREVIOUS_TEMPLATE)
    if previous_id is None or not table_exists(conn, "workspaces"):
        return
    rebound = conn.execute(
        text(
            "UPDATE workspaces SET workflow_template_id=:new "
            "WHERE slug=:ws AND workflow_template_id=:old"
        ),
        {"new": template_id, "ws": WORKSPACE, "old": previous_id},
    ).rowcount
    if not rebound:
        # Either there is no such workspace here, or a person chose its template.
        logger.info("%s: %s is not on %s; not moved", MIGRATION_ID, WORKSPACE, _PREVIOUS_TEMPLATE)
        return
    moved = move_never_run_backlog(
        conn,
        workspace_slug=WORKSPACE,
        from_template_id=previous_id,
        to_template_id=template_id,
        version=1,
        stages=STAGES,
    )
    logger.info("%s: moved %d never-run tickets onto %s", WORKSPACE, moved, TEMPLATE)
