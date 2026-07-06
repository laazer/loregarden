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
    "ac_gatekeeper": {
        "name": "AC Gatekeeper",
        "role_file": "agents/acceptance_criteria_gatekeeper.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "core_simulation": {
        "name": "Core Simulation Agent",
        "role_file": "agents/5_core_simulation/core_simulation_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "gameplay_systems": {
        "name": "Gameplay Systems Agent",
        "role_file": "agents/6_gameplay_systems/gameplay_systems_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "presentation": {
        "name": "Presentation Agent",
        "role_file": "agents/7_presentation/presentation_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "engine_integration": {
        "name": "Engine Integration Agent",
        "role_file": "agents/8_engine_integration/engine_integration_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "implementation_frontend": {
        "name": "Implementation Frontend Agent",
        "role_file": "agents/misc_agents/implementation_frontend_v1.md",
        "adapter": "cursor",
        "timeout": 1800,
    },
    "gdscript_reviewer": {
        "name": "GDScript Reviewer",
        "role_file": "agents/common_assets/gdscript_reviewer_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "architecture_reviewer": {
        "name": "Architecture Reviewer",
        "role_file": "agents/misc_agents/architecture_reviewer_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "learning": {
        "name": "Learning Agent",
        "role_file": "agents/9_learning/learning_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "blog_post": {
        "name": "Blog Post Agent",
        "role_file": "agents/10_blog_post/blog_post_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "retriever": {
        "name": "Research Librarian",
        "role_file": "agents/misc_agents/research_librarian_v1.md",
        "adapter": "claude",
        "timeout": 600,
    },
    "triage": {
        "name": "Triage Assistant",
        "role_file": "agents/misc_agents/research_librarian_v1.md",
        "adapter": "claude",
        "timeout": 300,
        "claude_model": "haiku",
    },
    "ticket_scoper": {
        "name": "Ticket Scoper",
        "role_file": "agents/misc_agents/ticket_scoper_v1.md",
        "adapter": "claude",
        "timeout": 600,
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
