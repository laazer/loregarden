import type { OrchestrationProfileView } from "../../../../api/gateTypes";
import type {
  StudioAgent,
  StudioWorkflow,
  StudioWorkflowStage,
  WorkspaceSummary,
} from "../../../../api/types";

export function workspace(overrides: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {
    id: "ws-1",
    slug: "blobert",
    name: "Blobert",
    repo_path: "/repo/blobert",
    repo_root: "/repo/blobert",
    repo_exists: true,
    ticket_count: 0,
    blocked_count: 0,
    workflow_template_slug: "blobert-tdd",
    cli_adapter: "claude",
    ...overrides,
  } as WorkspaceSummary;
}

export function profile(overrides: Partial<OrchestrationProfileView> = {}): OrchestrationProfileView {
  return {
    slug: "blobert",
    name: "Blobert",
    driver: "builtin_autopilot",
    workflow_template: "blobert-tdd",
    orchestrator_skill: "autopilot",
    gates_enabled: true,
    gates_configured: true,
    gates_commands: ["ruff check {workspace_root}"],
    gates_transition_script: "",
    gates_transition_script_resolved: "",
    gates_autofix_commands: [],
    gates_autofix_agent_fallback: true,
    gates_autofix_max_agent_attempts: 3,
    gates_placeholders: { workspace_root: "/repo/blobert" },
    gates_suggested_commands: [],
    max_stages_per_run: 0,
    source_path: "agent_context/orchestration/blobert.yaml",
    ...overrides,
  };
}

export function stage(overrides: Partial<StudioWorkflowStage> = {}): StudioWorkflowStage {
  return {
    key: "implement",
    name: "Implement",
    stage_type: "agent",
    agent_id: "backend_implementer",
    skill_name: "",
    optional: false,
    order: 1,
    exit_actions_enabled: false,
    exit_actions: [],
    classify_routes: [],
    parallel_agents: [],
    model: "",
    ...overrides,
  };
}

export function workflow(overrides: Partial<StudioWorkflow> = {}): StudioWorkflow {
  return {
    id: "wf-1",
    slug: "blobert-tdd",
    name: "Blobert TDD",
    description: "",
    stages: [stage()],
    transitions: [],
    published_template_id: null,
    published_template_slug: "blobert-tdd",
    ...overrides,
  } as StudioWorkflow;
}

export function agent(overrides: Partial<StudioAgent> = {}): StudioAgent {
  return {
    slug: "backend_implementer",
    name: "Backend Implementer",
    gate_checks: [],
    handoff_checks: [],
    ...overrides,
  } as StudioAgent;
}
