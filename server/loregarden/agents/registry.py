"""Agent registry — adapters only, no orchestration logic."""

AGENTS: dict[str, dict] = {
    "planner": {
        "name": "Planner Agent",
        "role_file": "agents/1_planner/planner_v1.md",
        "adapter": "local",
        "timeout": 120,
    },
    "spec": {
        "name": "Spec Agent",
        "role_file": "agents/2_spec/spec_v1.md",
        "adapter": "local",
        "timeout": 120,
    },
    "test_designer": {
        "name": "Test Designer Agent",
        "role_file": "agents/3_test_designer/test_designer_v1.md",
        "adapter": "local",
        "timeout": 120,
    },
    "test_breaker": {
        "name": "Test Breaker Agent",
        "role_file": "agents/4_test_breaker/test_breaker_v1.md",
        "adapter": "local",
        "timeout": 120,
    },
    "backend_implementer": {
        "name": "Backend Implementer Agent",
        "role_file": "agents/5_backend_implementer/backend_implementer_v1.md",
        "adapter": "local",
        "timeout": 300,
    },
    "frontend_implementer": {
        "name": "Frontend Implementer Agent",
        "role_file": "agents/6_frontend_implementer/frontend_implementer_v1.md",
        "adapter": "local",
        "timeout": 300,
    },
    "static_qa": {
        "name": "Static QA Agent",
        "role_file": "agents/9_static_qa/static_qa_v1.md",
        "adapter": "local",
        "timeout": 180,
    },
    "gatekeeper": {
        "name": "Acceptance Criteria Gatekeeper",
        "role_file": "agents/15_gatekeeper_review/gatekeeper_review_v1.md",
        "adapter": "local",
        "timeout": 120,
    },
    "retriever": {
        "name": "Context Retriever",
        "role_file": "agents/misc_agents/research_librarian_v1.md",
        "adapter": "local",
        "timeout": 120,
    },
}


def get_agent(agent_id: str) -> dict | None:
    return AGENTS.get(agent_id)


def list_agents() -> list[dict]:
    return [{"id": agent_id, **cfg} for agent_id, cfg in AGENTS.items()]
