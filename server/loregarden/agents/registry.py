"""Agent registry — adapters only, no orchestration logic."""

AGENTS: dict[str, dict] = {
    "planner": {
        "name": "Planner Agent",
        "role_file": "agents/1_planner/planner_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "spec": {
        "name": "Spec Agent",
        "role_file": "agents/2_spec/spec_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "test_designer": {
        "name": "Test Designer Agent",
        "role_file": "agents/3_test_designer/test_designer_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "test_breaker": {
        "name": "Test Breaker Agent",
        "role_file": "agents/4_test_breaker/test_breaker_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "backend_implementer": {
        "name": "Backend Implementer Agent",
        "role_file": "agents/5_backend_implementer/backend_implementer_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "frontend_implementer": {
        "name": "Frontend Implementer Agent",
        "role_file": "agents/6_frontend_implementer/frontend_implementer_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "static_qa": {
        "name": "Static QA Agent",
        "role_file": "agents/9_static_qa/static_qa_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "gatekeeper": {
        "name": "Acceptance Criteria Gatekeeper",
        "role_file": "agents/15_gatekeeper_review/gatekeeper_review_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "retriever": {
        "name": "Context Retriever",
        "role_file": "agents/misc_agents/research_librarian_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "triage": {
        "name": "Triage Assistant",
        "role_file": "agents/misc_agents/research_librarian_v1.md",
        "adapter": "claude",
        "timeout": 120,
    },
}


def get_agent(agent_id: str) -> dict | None:
    from loregarden.services.studio_service import load_studio_agent_config

    studio = load_studio_agent_config(agent_id)
    if studio:
        return studio
    cfg = AGENTS.get(agent_id)
    if not cfg:
        return None
    return cfg.copy()


def list_agents() -> list[dict]:
    return [{"id": agent_id, **cfg} for agent_id, cfg in AGENTS.items()]
