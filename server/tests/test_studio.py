import json

from fastapi.testclient import TestClient
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef
from loregarden.services.studio_service import (
    parse_agent_generate_payload,
    parse_markdown_frontmatter,
    parse_workflow_generate_payload,
    resolve_classify_route,
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
                            "skill_name": "apply_patch",
                        },
                        {
                            "languages": ["python"],
                            "specialties": ["backend"],
                            "agent_id": "backend_implementer",
                            "skill_name": "apply_patch",
                            "default": True,
                        },
                    ],
                },
                {
                    "key": "review",
                    "name": "Review",
                    "stage_type": "gate",
                    "agent_id": "gatekeeper",
                    "skill_name": "ac_gate",
                    "gate_required": True,
                    "order": 3,
                },
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
    assert body["built_in"] is True
    assert body["read_only"] is True
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
