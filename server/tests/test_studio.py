import json

from fastapi.testclient import TestClient
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef
from loregarden.services.studio_generation import (
    parse_agent_generate_payload,
    parse_workflow_generate_payload,
)
from loregarden.services.studio_routing import resolve_classify_route
from loregarden.services.studio_service import (
    parse_markdown_frontmatter,
    strip_markdown_frontmatter,
)

AGENT_GENERATE_STUB = """Draft agent:

```json
{
  "name": "Localization Reviewer",
  "slug": "localization-reviewer",
  "description": "Reviews localized copy against acceptance criteria",
  "role_body": "Review staged diffs for i18n regressions and missing strings.",
  "adapter": "claude",
  "default_skill": "review",
  "mcp_tools": ["loregarden_get_ticket", "loregarden_attach_artifact", "unknown_tool"]
}
```
"""

WORKFLOW_GENERATE_STUB = """Draft workflow:

```json
{
  "name": "Hotfix Express",
  "slug": "hotfix-express",
  "description": "Fast plan-implement-review loop",
  "stages": [
    {
      "key": "plan",
      "name": "Plan",
      "stage_type": "agent",
      "agent_id": "planner",
      "skill_name": "plan",
      "optional": false,
      "order": 1,
      "gate_required": false,
      "classify_routes": []
    },
    {
      "key": "review",
      "name": "Review",
      "stage_type": "gate",
      "agent_id": "gatekeeper",
      "skill_name": "ac_gate",
      "optional": false,
      "order": 2,
      "gate_required": true,
      "classify_routes": []
    }
  ]
}
```
"""


def test_studio_agent_crud(client: TestClient):
    create = client.post(
        "/api/studio/agents",
        json={
            "slug": "api-tester",
            "name": "API Tester",
            "description": "Validates API endpoints",
            "role_body": "You test HTTP APIs thoroughly.",
            "adapter": "claude",
            "mcp_enabled": True,
            "mcp_tools": ["loregarden_get_ticket", "loregarden_complete_stage"],
            "gate_checks": [
                {"kind": "workflow_gate", "title": "Sign off tests", "impact": "Blocks merge"}
            ],
            "handoff_checks": [
                {"kind": "mcp_complete", "prompt": "Call loregarden_complete_stage when done."}
            ],
        },
    )
    assert create.status_code == 200
    data = create.json()
    assert data["slug"] == "api-tester"
    assert "loregarden_get_ticket" in data["mcp_tools"]
    assert "loregarden_memory_status" in data["mcp_tools"]
    assert "memory_protocol_v1.md" in data["role_body"]

    listed = client.get("/api/studio/agents")
    assert listed.status_code == 200
    slugs = {item["slug"] for item in listed.json()}
    assert "api-tester" in slugs
    assert "planner" in slugs

    from loregarden.agents.registry import get_agent

    cfg = get_agent("api-tester")
    assert cfg is not None
    assert "You test HTTP" in cfg["role_body"]
    assert "memory_protocol_v1.md" in cfg["role_body"]
    assert "loregarden_memory_status" in cfg["mcp_tools"]


def test_studio_workflow_publish(client: TestClient):
    create = client.post(
        "/api/studio/workflows",
        json={
            "slug": "quick-review",
            "name": "Quick Review",
            "description": "Plan then review",
            "stages": [
                {
                    "key": "plan",
                    "name": "Plan",
                    "stage_type": "agent",
                    "agent_id": "planner",
                    "skill_name": "plan",
                    "order": 1,
                },
                {
                    "key": "route_impl",
                    "name": "Route Implementation",
                    "stage_type": "classify",
                    "order": 2,
                    "classify_routes": [
                        {
                            "languages": ["typescript", "javascript"],
                            "specialties": ["frontend"],
                            "agent_id": "frontend_implementer",
                            "skill_name": "refactor",
                        },
                        {
                            "languages": ["python"],
                            "specialties": ["backend"],
                            "agent_id": "backend_implementer",
                            "skill_name": "refactor",
                            "default": True,
                        },
                    ],
                },
                {
                    "key": "review",
                    "name": "Review",
                    "stage_type": "gate",
                    "agent_id": "gatekeeper",
                    "skill_name": "",
                    "gate_required": True,
                    "order": 3,
                },
                {"key": "done", "name": "Done", "stage_type": "agent", "order": 4},
            ],
        },
    )
    assert create.status_code == 200

    publish = client.post("/api/studio/workflows/quick-review/publish")
    assert publish.status_code == 200
    body = publish.json()
    assert body["published_template_slug"] == "studio-quick-review"

    templates = client.get("/api/workflows/templates")
    slugs = {item["slug"] for item in templates.json()}
    assert "studio-quick-review" in slugs


def test_studio_agent_default_model_persists(client: TestClient):
    create = client.post(
        "/api/studio/agents",
        json={
            "slug": "model-pinned-agent",
            "name": "Model Pinned Agent",
            "role_body": "You do a focused thing.",
            "adapter": "claude",
            "default_model": "opus",
        },
    )
    assert create.status_code == 200
    assert create.json()["default_model"] == "opus"

    from loregarden.agents.registry import get_agent

    cfg = get_agent("model-pinned-agent")
    assert cfg is not None
    assert cfg["default_model"] == "opus"

    update = client.patch(
        "/api/studio/agents/model-pinned-agent",
        json={"default_model": "haiku"},
    )
    assert update.status_code == 200
    assert update.json()["default_model"] == "haiku"


def test_studio_workflow_stage_model_survives_publish(client: TestClient):
    create = client.post(
        "/api/studio/workflows",
        json={
            "slug": "model-pinned-workflow",
            "name": "Model Pinned Workflow",
            "stages": [
                {
                    "key": "plan",
                    "name": "Plan",
                    "stage_type": "agent",
                    "agent_id": "planner",
                    "skill_name": "plan",
                    "order": 1,
                    "model": "opus",
                },
                {"key": "done", "name": "Done", "stage_type": "agent", "order": 2},
            ],
        },
    )
    assert create.status_code == 200
    assert create.json()["stages"][0]["model"] == "opus"

    publish = client.post("/api/studio/workflows/model-pinned-workflow/publish")
    assert publish.status_code == 200

    from loregarden.db.session import engine
    from loregarden.models.domain import WorkflowTemplate
    from sqlmodel import Session, select

    with Session(engine) as session:
        template = session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-model-pinned-workflow")
        ).first()
        assert template is not None
        stages = json.loads(template.stages_json)
        assert stages[0]["model"] == "opus"


def test_resolve_classify_route_python_backend():
    ticket = Ticket(
        id="t1",
        external_id="01-test",
        workspace_id="ws",
        title="Add FastAPI endpoint",
        description="Implement python backend route for studio API",
    )
    stage = WorkflowStageDef(
        key="route_impl",
        name="Route Implementation",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["typescript"],
                specialties=["frontend"],
                agent_id="frontend_implementer",
                skill_name="apply_patch",
            ),
            ClassifyRoute(
                languages=["python"],
                specialties=["backend"],
                agent_id="backend_implementer",
                skill_name="apply_patch",
                default=True,
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "backend_implementer"


def test_resolve_classify_route_prefers_ticket_next_agent():
    ticket = Ticket(
        id="t1",
        external_id="01-test",
        workspace_id="ws",
        title="Unrelated title",
        description="No keywords",
        next_agent="frontend_implementer",
    )
    stage = WorkflowStageDef(
        key="route_impl",
        name="Route Implementation",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["python"],
                specialties=["backend"],
                agent_id="backend_implementer",
                skill_name="apply_patch",
                default=True,
            ),
            ClassifyRoute(
                languages=["typescript"],
                specialties=["frontend"],
                agent_id="frontend_implementer",
                skill_name="apply_patch",
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "frontend_implementer"
    assert skill == "apply_patch"


def test_resolve_classify_route_matches_frontend_synonyms_without_literal_keywords():
    ticket = Ticket(
        id="t1",
        external_id="33-add-smart-import-button-to-import-modal-ui",
        workspace_id="ws",
        title="Add smart import button to import modal UI",
        description="Update import modal to present smart import as an option alongside regular import.",
    )
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["typescript", "javascript"],
                specialties=["frontend"],
                agent_id="frontend_implementer",
                skill_name="apply_patch",
            ),
            ClassifyRoute(
                languages=["typescript", "javascript"],
                specialties=["backend"],
                agent_id="backend_implementer",
                skill_name="apply_patch",
                default=True,
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "frontend_implementer"
    assert skill == "apply_patch"


def test_resolve_classify_route_ignores_description_keyword_noise():
    """Specs and rework notes mention client paths without making the ticket frontend.

    Regression for ticket 327: a server skill-lookup fix kept routing to
    frontend_implementer because the accumulated description scored UI synonyms.
    """
    ticket = Ticket(
        id="t327",
        external_id="327-skill-lookup-silently-returns-nothing-when-a-wor",
        workspace_id="ws",
        title="Skill lookup silently returns nothing when a workspace has no skills",
        description=(
            "Frontend R7 is already implemented in client/src/api/types.ts and "
            "StudioPage.tsx with typescript react component tests. The remaining "
            "required work is backend-owned under server/**."
        ),
        acceptance_criteria_json=json.dumps(
            [
                "A workspace without agent_context/skills resolves from the default set",
                "Missing declared skills fail the run loudly",
            ]
        ),
    )
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["typescript", "javascript"],
                specialties=["frontend"],
                agent_id="frontend_implementer",
            ),
            ClassifyRoute(
                specialties=["backend"],
                languages=["typescript", "javascript"],
                agent_id="backend_implementer",
                default=True,
            ),
        ],
    )
    agent_id, _ = resolve_classify_route(ticket, stage)
    assert agent_id == "backend_implementer"


def test_resolve_classify_route_word_boundary_avoids_false_substring_match():
    ticket = Ticket(
        id="t1",
        external_id="01-test",
        workspace_id="ws",
        title="Add a logo to the header",
        description="Swap the logo image asset in the header component.",
    )
    stage = WorkflowStageDef(
        key="route_impl",
        name="Route Implementation",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=[],
                specialties=["go"],
                agent_id="go_implementer",
                skill_name="apply_patch",
            ),
            ClassifyRoute(
                agent_id="fallback_implementer",
                skill_name="apply_patch",
                default=True,
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "fallback_implementer"


def test_studio_mcp_tools(client: TestClient):
    res = client.get("/api/studio/mcp-tools")
    assert res.status_code == 200
    tools = res.json()
    assert "loregarden_get_ticket" in tools
    assert "loregarden_complete_stage" in tools


def test_studio_mcp_tool_guides(client: TestClient):
    res = client.get("/api/studio/mcp-tool-guides")
    assert res.status_code == 200
    guides = res.json()
    assert len(guides) >= 10
    get_ticket = next(item for item in guides if item["name"] == "loregarden_get_ticket")
    assert "When to use" in get_ticket["when_to_use"] or get_ticket["when_to_use"]
    assert get_ticket["example"].startswith("tools/call")


def test_studio_defaults(client: TestClient):
    res = client.get("/api/studio/defaults")
    assert res.status_code == 200
    body = res.json()
    assert "loregarden_get_ticket" in body["mcp_tools"]
    assert "loregarden_memory_status" in body["mcp_tools"]
    assert "loregarden_search_memory" in body["mcp_tools"]
    assert len(body["handoff_checks"]) >= 1


def test_parse_markdown_frontmatter():
    raw = """---
description: Acceptance Criteria Gatekeeper
model: claude-3.7-sonnet
globs: []
alwaysApply: false
---
You are the gatekeeper.
"""
    parsed = parse_markdown_frontmatter(raw)
    assert parsed["description"] == "Acceptance Criteria Gatekeeper"
    assert parsed["model"] == "claude-3.7-sonnet"
    assert parsed["alwaysApply"] == "false"


def test_strip_markdown_frontmatter():
    raw = """---
description: Acceptance Criteria Gatekeeper
model: claude-3.7-sonnet
globs: []
alwaysApply: false
---
You are the gatekeeper.
"""
    stripped = strip_markdown_frontmatter(raw)
    assert "description:" not in stripped
    assert "alwaysApply:" not in stripped
    assert stripped.startswith("You are the gatekeeper.")


def test_studio_agent_preview_strips_frontmatter(client: TestClient):
    res = client.post(
        "/api/studio/agents/preview",
        json={
            "name": "Gatekeeper",
            "description": "Gate",
            "role_body": (
                "---\n"
                "description: Hidden metadata\n"
                "model: claude-3.7-sonnet\n"
                "---\n"
                "You are the gatekeeper."
            ),
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert "Hidden metadata" not in body["markdown"]
    assert "You are the gatekeeper." in body["markdown"]
    assert body["profile"]["description"] == "Hidden metadata"
    assert body["profile"]["model"] == "claude-3.7-sonnet"


def test_studio_agent_preview(client: TestClient):
    res = client.post(
        "/api/studio/agents/preview",
        json={
            "name": "Preview Bot",
            "description": "Test preview",
            "role_body": "You are a test agent.",
            "mcp_enabled": True,
            "mcp_tools": ["loregarden_get_ticket", "loregarden_attach_artifact"],
            "handoff_checks": [{"kind": "mcp_complete", "prompt": "Finish cleanly."}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Preview Bot"
    assert body["profile"]["description"] == "Test preview"
    assert body["profile"]["provider"] == "claude"
    assert "loregarden_get_ticket" in body["markdown"]
    assert "memory_protocol_v1.md" in body["markdown"]
    assert "Finish cleanly." in body["markdown"]
    assert "role" in body["sections"]


def test_studio_lists_builtin_workflows(client: TestClient):
    res = client.get("/api/studio/workflows")
    assert res.status_code == 200
    workflows = res.json()
    slugs = {item["slug"] for item in workflows}
    assert "loregarden-tdd" in slugs or any(item.get("built_in") for item in workflows)

    if "loregarden-tdd" in slugs:
        detail = client.get("/api/studio/workflows/loregarden-tdd")
        assert detail.status_code == 200
        assert detail.json()["read_only"] is True
        assert len(detail.json()["stages"]) >= 1


def test_builtin_agent_has_role_body(client: TestClient):
    res = client.get("/api/studio/agents/planner")
    assert res.status_code == 200
    body = res.json()
    # Built-ins are now seeded DB rows: flagged built_in for provenance, but
    # editable (read_only False) and versioned.
    assert body["built_in"] is True
    assert body["read_only"] is False
    assert body["version"] >= 1
    assert len(body["role_body"]) > 20
    assert "memory_protocol_v1.md" in body["role_body"]


def test_parse_agent_generate_payload_filters_unknown_tools():
    generated = parse_agent_generate_payload(AGENT_GENERATE_STUB)
    assert generated is not None
    assert generated.name == "Localization Reviewer"
    assert generated.slug == "localization-reviewer"
    assert "loregarden_get_ticket" in generated.mcp_tools
    assert "unknown_tool" not in generated.mcp_tools


def test_parse_workflow_generate_payload():
    generated = parse_workflow_generate_payload(
        WORKFLOW_GENERATE_STUB,
        agent_ids=["planner", "gatekeeper", "backend_implementer"],
        skills=["plan", "ac_gate", "apply_patch"],
    )
    assert generated is not None
    assert generated.name == "Hotfix Express"
    assert len(generated.stages) == 2
    assert generated.stages[0].agent_id == "planner"
    assert generated.stages[1].stage_type == "gate"


def test_parse_workflow_generate_payload_supports_parallel_stage():
    stub = """Draft workflow:

```json
{
  "name": "Review Fanout",
  "slug": "review-fanout",
  "description": "Plan then fan out to parallel reviewers",
  "stages": [
    {
      "key": "plan",
      "name": "Plan",
      "stage_type": "agent",
      "agent_id": "planner",
      "skill_name": "plan",
      "optional": false,
      "order": 1,
      "gate_required": false
    },
    {
      "key": "review",
      "name": "Review",
      "stage_type": "parallel",
      "optional": false,
      "order": 2,
      "gate_required": false,
      "parallel_agents": [
        {"agent_id": "gatekeeper", "skill_name": "ac_gate"},
        {"agent_id": "backend_implementer", "skill_name": "apply_patch"},
        {"agent_id": "unknown_agent", "skill_name": "apply_patch"}
      ]
    }
  ]
}
```"""
    generated = parse_workflow_generate_payload(
        stub,
        agent_ids=["planner", "gatekeeper", "backend_implementer"],
        skills=["plan", "ac_gate", "apply_patch"],
    )
    assert generated is not None
    assert len(generated.stages) == 2
    review_stage = generated.stages[1]
    assert review_stage.stage_type == "parallel"
    assert [member.agent_id for member in review_stage.parallel_agents] == [
        "gatekeeper",
        "backend_implementer",
    ]


def test_studio_generate_agent_endpoint(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_STUDIO_GENERATE_STUB_RESPONSE", AGENT_GENERATE_STUB)
    res = client.post(
        "/api/studio/agents/generate",
        json={"description": "An agent that reviews localization changes"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Localization Reviewer"
    assert body["role_body"].startswith("Review staged diffs")


def test_studio_generate_workflow_endpoint(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_STUDIO_GENERATE_STUB_RESPONSE", WORKFLOW_GENERATE_STUB)
    res = client.post(
        "/api/studio/workflows/generate",
        json={"description": "A quick hotfix workflow with review gate"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "hotfix-express"
    assert len(body["stages"]) == 2


def test_studio_generate_requires_description(client: TestClient):
    res = client.post("/api/studio/agents/generate", json={"description": "   "})
    assert res.status_code == 400


def _two_stage_workflow(slug: str) -> dict:
    # A terminal `done` stage is required (every workflow must be able to finalize);
    # the two meaningful stages are implement + gate.
    return {
        "slug": slug,
        "name": slug,
        "description": "",
        "stages": [
            {"key": "implement", "name": "Implement", "stage_type": "agent", "order": 1},
            {"key": "gate", "name": "Gate", "stage_type": "agent", "order": 2},
            {"key": "done", "name": "Done", "stage_type": "agent", "order": 3},
        ],
    }


def test_studio_stage_edit_preserves_reject_transitions(client: TestClient):
    """Editing a stage must not wipe hand-authored rework routes.

    This regenerated a bare linear chain on every stage edit, silently dropping every
    `when: reject` edge — which is why studio templates had none, and why a rejected
    gate could only ever rewind one stage instead of routing where it asked.
    """
    body = _two_stage_workflow("reject-preserve")
    body["transitions"] = [
        {"from": "implement", "to": "gate", "when": "pass"},
        {"from": "gate", "to": "implement", "when": "reject"},
    ]
    assert client.post("/api/studio/workflows", json=body).status_code in (200, 201)

    # A stage-only edit — exactly what the Studio editor sends.
    updated = client.patch(
        "/api/studio/workflows/reject-preserve",
        json={
            "stages": [
                {
                    "key": "implement",
                    "name": "Implement (renamed)",
                    "stage_type": "agent",
                    "order": 1,
                },
                {"key": "gate", "name": "Gate", "stage_type": "agent", "order": 2},
                {"key": "done", "name": "Done", "stage_type": "agent", "order": 3},
            ]
        },
    )
    assert updated.status_code == 200
    transitions = updated.json()["transitions"]
    assert {"from": "gate", "to": "implement", "when": "reject"} in transitions
    assert {"from": "implement", "to": "gate", "when": "pass"} in transitions


def test_studio_stage_edit_prunes_transitions_to_removed_stages(client: TestClient):
    """A route to a deleted stage is a phantom target — drop it rather than keep
    a key apply_stage_route can't resolve."""
    body = _two_stage_workflow("reject-prune")
    body["transitions"] = [
        {"from": "implement", "to": "gate", "when": "pass"},
        {"from": "gate", "to": "implement", "when": "reject"},
    ]
    assert client.post("/api/studio/workflows", json=body).status_code in (200, 201)

    updated = client.patch(
        "/api/studio/workflows/reject-prune",
        json={
            "stages": [
                {"key": "gate", "name": "Gate", "stage_type": "agent", "order": 1},
                {"key": "done", "name": "Done", "stage_type": "agent", "order": 2},
            ]
        },
    )
    assert updated.status_code == 200
    # Both authored edges referenced the removed `implement` stage, so both prune;
    # the surviving `done` stage carries no route of its own.
    assert updated.json()["transitions"] == []


def test_studio_stage_edit_seeds_linear_chain_only_when_none_exist(client: TestClient):
    """The bootstrap branch: a workflow with no routes of its own gets a linear
    chain. create_workflow auto-seeds, so reaching this needs an explicit clear."""
    assert client.post(
        "/api/studio/workflows", json=_two_stage_workflow("reject-seed")
    ).status_code in (200, 201)
    assert (
        client.patch("/api/studio/workflows/reject-seed", json={"transitions": []}).status_code
        == 200
    )

    updated = client.patch(
        "/api/studio/workflows/reject-seed",
        json={
            "stages": [
                {"key": "implement", "name": "Implement", "stage_type": "agent", "order": 1},
                {"key": "gate", "name": "Gate", "stage_type": "agent", "order": 2},
                {"key": "done", "name": "Done", "stage_type": "agent", "order": 3},
            ]
        },
    )
    assert updated.status_code == 200
    assert updated.json()["transitions"] == [
        {"from": "implement", "to": "gate"},
        {"from": "gate", "to": "done"},
    ]


def test_studio_adding_a_stage_preserves_routes_without_inventing_edges(client: TestClient):
    """Adding a stage no longer re-links the chain — preserving authored routes wins
    over auto-wiring, and an unrouted forward hop still resolves via stage order
    (StateMachine.resolve_next_stage_key falls back to next_stage_key)."""
    body = _two_stage_workflow("reject-add")
    body["transitions"] = [
        {"from": "implement", "to": "gate", "when": "pass"},
        {"from": "gate", "to": "implement", "when": "reject"},
    ]
    assert client.post("/api/studio/workflows", json=body).status_code in (200, 201)

    updated = client.patch(
        "/api/studio/workflows/reject-add",
        json={
            "stages": [
                {"key": "implement", "name": "Implement", "stage_type": "agent", "order": 1},
                {"key": "gate", "name": "Gate", "stage_type": "agent", "order": 2},
                {"key": "done", "name": "Done", "stage_type": "agent", "order": 3},
            ]
        },
    )
    assert updated.status_code == 200
    transitions = updated.json()["transitions"]
    assert {"from": "gate", "to": "implement", "when": "reject"} in transitions
    assert not [item for item in transitions if item["to"] == "done"]


def test_workflow_rejects_classify_branch_to_unknown_stage():
    """A phantom branch target must fail on save, not mid-run at routing time."""
    import pytest
    from loregarden.models.domain import StudioWorkflowStage
    from loregarden.services.studio_service import _validate_stage_route_targets

    stages = [
        StudioWorkflowStage(
            key="triage",
            name="Triage",
            stage_type="classify",
            order=1,
            classify_routes=[ClassifyRoute(agent_id="backend_implementer", to_stage="nonexistent")],
        ),
        StudioWorkflowStage(key="implement", name="Implement", agent_id="backend", order=2),
    ]
    with pytest.raises(ValueError, match="branch to unknown stage"):
        _validate_stage_route_targets(stages)

    stages[0].classify_routes[0].to_stage = "implement"
    _validate_stage_route_targets(stages)  # valid target: no raise


def test_workflow_rejects_a_classify_route_only_list_order_can_choose():
    """A route selectable by nothing is chosen by position, which is not a rule.

    `_select_classify_route` chooses three ways: content scoring on specialties
    or languages, the pin when it names exactly one route, or the route marked
    `default`. A route with none of those is reachable only by being first in the
    list — and "whichever is first" is the failure that sent 471 live tickets
    down a docs shortcut they had nothing to do with.
    """
    import pytest
    from loregarden.models.domain import StudioWorkflowStage
    from loregarden.services.studio_service import _validate_classify_routes_are_selectable

    stages = [
        StudioWorkflowStage(
            key="triage",
            name="Triage",
            stage_type="classify",
            order=1,
            classify_routes=[
                ClassifyRoute(agent_id="backend_implementer", to_stage=""),
                ClassifyRoute(agent_id="frontend_implementer", to_stage=""),
            ],
        ),
    ]
    with pytest.raises(ValueError, match="list position"):
        _validate_classify_routes_are_selectable(stages)

    # Either way of making it choosable is enough.
    stages[0].classify_routes[0].specialties = ["backend"]
    stages[0].classify_routes[1].default = True
    _validate_classify_routes_are_selectable(stages)


def test_two_routes_may_share_an_agent_and_differ_in_branch():
    """Deliberately allowed, and the live triage stage depends on it.

    The scoper triages everything, and typo/docs work skips ahead to test-design.
    A pin cannot say which of the two it meant, so `_select_classify_route`
    declines to let it steer there — the template is fine, and rejecting it would
    delete a working shortcut to fix a bug that lives in the matching, not here.
    """
    from loregarden.models.domain import StudioWorkflowStage
    from loregarden.services.studio_service import _validate_classify_routes_are_selectable

    stages = [
        StudioWorkflowStage(
            key="triage",
            name="Triage",
            stage_type="classify",
            order=1,
            classify_routes=[
                ClassifyRoute(
                    agent_id="ticket_scoper", specialties=["docs"], to_stage="test-design"
                ),
                ClassifyRoute(agent_id="ticket_scoper", default=True, to_stage=""),
            ],
        ),
        StudioWorkflowStage(key="test-design", name="Design Tests", agent_id="td", order=2),
    ]
    _validate_classify_routes_are_selectable(stages)
