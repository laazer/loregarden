import type { TicketDetail, WorkflowStageView } from "../../api/types";
import { buildStageMenuActions } from "../workflowStageActions";

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "ticket-1",
    external_id: "test-ticket",
    title: "Test ticket",
    state: "in_progress",
    priority: 3,
    workspace_slug: "loregarden",
    workflow_stage_key: "ac_gate",
    workflow_stage_status: "running",
    workflow_stage_name: "AC Gate",
    run_code: "",
    work_item_type: "task",
    parent_ticket_id: null,
    milestone: "",
    branch: "",
    child_count: 0,
    current_stage_agent: "ac_gatekeeper",
    description: "",
    acceptance_criteria: [],
    checked_acceptance_criteria: [],
    revision: 1,
    last_updated_by: "human",
    next_status: "Proceed",
    blocking_issues: "",
    state_locked: false,
    workflow_template_slug: "blobert-tdd",
    workflow_template_name: "Blobert TDD",
    workflow_transitions: [
      { from: "ac_gate", to: "playtest", when: "pass" },
      { from: "ac_gate", to: "implementation", when: "reject", agent_id: "core_simulation" },
    ],
    stages: [
      stage("implementation", "Implementation", 6, "pending"),
      stage("script_review", "Script Review", 7, "done"),
      stage("ac_gate", "AC Gate", 8, "running", "gate"),
    ],
    artifacts: {},
    ...overrides,
  };
}

function stage(
  key: string,
  name: string,
  order: number,
  status: WorkflowStageView["status"],
  stageType = "agent",
): WorkflowStageView {
  return {
    key,
    name,
    order,
    status,
    agent_id: stageType === "gate" ? "ac_gatekeeper" : "core_simulation",
    skill_name: "apply_patch",
    optional: false,
    note: "",
    stage_type: stageType,
    agents: [],
  };
}

describe("buildStageMenuActions", () => {
  it("includes run and upstream route actions for gate stages", () => {
    const actions = buildStageMenuActions({
      ticket: ticket(),
      stage: ticket().stages[2],
      runCheck: { allowed: true, reason: "Run AC Gate" },
      isRunning: false,
      workflowBusy: false,
      canCopyTerminal: true,
    });

    expect(actions.some((action) => action.kind === "run")).toBe(true);
    expect(actions.some((action) => action.kind === "route-upstream" && action.label === "Route to Implementation")).toBe(
      true,
    );
  });

  it("offers set cursor when viewing a non-current stage", () => {
    const actions = buildStageMenuActions({
      ticket: ticket(),
      stage: ticket().stages[0],
      runCheck: { allowed: true, reason: "Run Implementation" },
      isRunning: false,
      workflowBusy: false,
      canCopyTerminal: true,
    });

    expect(actions.some((action) => action.kind === "set-cursor")).toBe(true);
  });

  it("omits set cursor for the current stage", () => {
    const actions = buildStageMenuActions({
      ticket: ticket(),
      stage: ticket().stages[2],
      runCheck: { allowed: true, reason: "Run AC Gate" },
      isRunning: false,
      workflowBusy: false,
      canCopyTerminal: true,
    });

    expect(actions.some((action) => action.kind === "set-cursor")).toBe(false);
  });
});

// AC19. Each "Copy terminal command" click calls `start_stage_execution`, so a
// second click opens a second `AgentRun` and `fail_interrupted_runs` kills the
// first. The server call now takes as long as a merge of up to 134 commits, so
// the window for that second click is seconds wide rather than milliseconds.
// This is a correctness guard, not polish.
describe("buildStageMenuActions — a handoff already in flight", () => {
  const args = (overrides: Record<string, unknown> = {}) => ({
    ticket: ticket(),
    stage: ticket().stages[0],
    runCheck: { allowed: true, reason: "Run Implementation" },
    isRunning: false,
    workflowBusy: false,
    canCopyTerminal: true,
    copyTerminalPending: false,
    ...overrides,
  });

  const copyTerminal = (built: ReturnType<typeof buildStageMenuActions>) =>
    built.find((action) => action.kind === "copy-terminal");

  it("offers the item normally when nothing is in flight", () => {
    const action = copyTerminal(buildStageMenuActions(args()));

    expect(action).toBeDefined();
    expect(action!.disabled).toBeFalsy();
  });

  it("disables the item and says why while a handoff is being prepared", () => {
    const action = copyTerminal(buildStageMenuActions(args({ copyTerminalPending: true })));

    expect(action).toBeDefined();
    expect(action!.disabled).toBe(true);
    expect(action!.title).toBe("Already preparing a terminal command for this ticket");
  });

  it("leaves the run action's own disabled state alone", () => {
    // The pending handoff belongs to one stage's copy item. Reusing the
    // workflow-wide `workflowBusy` flag instead would disable the item on
    // every stage of every ticket, and disable Run here too.
    const built = buildStageMenuActions(args({ copyTerminalPending: true }));

    expect(built.find((action) => action.kind === "run")!.disabled).toBe(false);
  });
});
