import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.models.domain import StudioAgent, StudioWorkflow, WorkflowTemplate
from loregarden.services.studio_service import resolve_classify_route
from loregarden.models.domain import ClassifyRoute, Ticket, WorkflowStageDef


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
            "gate_checks": [{"kind": "workflow_gate", "title": "Sign off tests", "impact": "Blocks merge"}],
            "handoff_checks": [{"kind": "mcp_complete", "prompt": "Call loregarden_complete_stage when done."}],
        },
    )
    assert create.status_code == 200
    data = create.json()
    assert data["slug"] == "api-tester"
    assert "loregarden_get_ticket" in data["mcp_tools"]

    listed = client.get("/api/studio/agents")
    assert listed.status_code == 200
    slugs = {item["slug"] for item in listed.json()}
    assert "api-tester" in slugs
    assert "planner" in slugs

    from loregarden.agents.registry import get_agent

    cfg = get_agent("api-tester")
    assert cfg is not None
    assert cfg["role_body"].startswith("You test HTTP")


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
    assert len(body["handoff_checks"]) >= 1


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
    assert "Preview Bot" in body["markdown"]
    assert "loregarden_get_ticket" in body["markdown"]
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
