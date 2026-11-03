import type { TicketDetail } from "../../api/client";
import { agentsAssembleLabel, isLanding } from "../workflowHelpers";

function ticket(overrides: Partial<TicketDetail>): TicketDetail {
  return {
    state: "in_progress",
    workflow_stage_key: "done",
    stages: [
      { key: "gate", name: "Quality Gate", status: "done" },
      { key: "done", name: "Done", status: "pending" },
    ],
    active_orchestration: null,
    ...overrides,
  } as TicketDetail;
}

const live = (stageKey: string) =>
  ({ id: "o1", run_code: "orch_c0f6bc", current_stage_key: stageKey }) as TicketDetail["active_orchestration"];

describe("isLanding", () => {
  it("is a live orchestration on the terminal stage", () => {
    expect(isLanding(ticket({ active_orchestration: live("done") }))).toBe(true);
  });

  it("is not a run still on an agent stage, nor a terminal stage with nothing live", () => {
    expect(isLanding(ticket({ workflow_stage_key: "gate", active_orchestration: live("gate") }))).toBe(false);
    expect(isLanding(ticket({ active_orchestration: null }))).toBe(false);
  });
});

describe("agentsAssembleLabel", () => {
  it("names the live run instead of offering to start another", () => {
    expect(agentsAssembleLabel(ticket({ active_orchestration: live("done") }), false)).toBe("Landing…");
    expect(
      agentsAssembleLabel(ticket({ workflow_stage_key: "gate", active_orchestration: live("gate") }), false),
    ).toBe("Running…");
  });

  it("offers Continue Run on a started ticket with nothing live", () => {
    expect(agentsAssembleLabel(ticket({}), false)).toBe("Continue Run");
  });
});
