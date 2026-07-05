import type { TicketDetail, WorkflowStageView } from "../../api/client";
import {
  buildOrchestrateTerminalCommand,
  buildStageRunTerminalCommand,
  isAgentWorkflowTicket,
} from "../terminalCommands";
import { isAgentStage } from "../stageDisplay";

function baseTicket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "ticket-uuid-1",
    external_id: "03-wire-cli-agent-runner",
    title: 'Run "CLI" agent',
    state: "in_progress",
    priority: 2,
    workspace_slug: "loregarden",
    workflow_stage_key: "planning",
    workflow_stage_status: "pending",
    workflow_stage_name: "Planning",
    run_code: "",
    work_item_type: "task",
    parent_ticket_id: null,
    milestone: "",
    branch: "",
    child_count: 0,
    description: "",
    acceptance_criteria: [],
    revision: 1,
    last_updated_by: "",
    next_agent: "",
    next_status: "",
    blocking_issues: "",
    state_locked: false,
    workflow_template_slug: "loregarden-tdd",
    workflow_template_name: "Loregarden TDD",
    stages: [],
    artifacts: {},
    ...overrides,
  };
}

function baseStage(overrides: Partial<WorkflowStageView> = {}): WorkflowStageView {
  return {
    key: "planning",
    name: "Planning",
    status: "pending",
    agent_id: "planner",
    skill_name: "plan",
    optional: false,
    note: "",
    stage_type: "agent",
    agents: [],
    ...overrides,
  };
}

describe("terminalCommands", () => {
  it("detects agent workflow tickets", () => {
    expect(isAgentWorkflowTicket(baseTicket())).toBe(true);
    expect(isAgentWorkflowTicket(baseTicket({ workflow_template_slug: "" }))).toBe(false);
  });

  it("detects agent stages", () => {
    expect(isAgentStage(baseStage())).toBe(true);
    expect(isAgentStage(baseStage({ agent_id: "" }))).toBe(false);
    expect(isAgentStage(baseStage({ key: "done" }))).toBe(false);
    expect(isAgentStage(baseStage({ agent_id: "", key: "approval" }))).toBe(false);
  });

  it("builds orchestrate curl command", () => {
    const cmd = buildOrchestrateTerminalCommand(baseTicket(), "http://127.0.0.1:8000");
    expect(cmd).toContain("# Loregarden: orchestrate ticket 03-wire-cli-agent-runner");
    expect(cmd).toContain("curl -sS -X POST 'http://127.0.0.1:8000/api/tickets/ticket-uuid-1/orchestrate'");
    expect(cmd).toContain("-d '{}'");
  });

  it("builds stage run curl command", () => {
    const cmd = buildStageRunTerminalCommand(
      baseTicket(),
      baseStage({ key: "spec", name: "Specification" }),
      "http://127.0.0.1:8000",
    );
    expect(cmd).toContain('# Loregarden: run stage "Specification" (spec)');
    expect(cmd).toContain("curl -sS -X POST 'http://127.0.0.1:8000/api/tickets/ticket-uuid-1/start'");
    expect(cmd).toContain(`-d '${JSON.stringify({ manual: true, stage_key: "spec" })}'`);
  });
});
