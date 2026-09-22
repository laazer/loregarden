import type { TicketHistoryEvent } from "../../api/types";
import { historyLines } from "../ticketHistory";

let seq = 0;
function gate(outcome: string, stage = "implement", fix_tier = "none"): TicketHistoryEvent {
  seq += 1;
  return {
    id: `e${seq}`,
    type: "GateEvaluated",
    ticket_id: "t1",
    workspace_id: "w1",
    payload: { outcome, stage_key: stage, fix_tier },
    created_at: "2026-09-08T10:00:00Z",
  };
}

function stageEvent(type: string, stage = "implement"): TicketHistoryEvent {
  seq += 1;
  return {
    id: `e${seq}`,
    type,
    ticket_id: "t1",
    workspace_id: "w1",
    payload: { stage_key: stage },
    created_at: "2026-09-08T10:00:00Z",
  };
}

describe("historyLines", () => {
  it("shows a gate evaluation with its outcome and stage", () => {
    const [line] = historyLines([gate("failed")]);
    expect(line.text).toBe("Gate implement failed");
    expect(line.tone).toBe("failed");
  });

  it("joins a failure and the fix that cleared it into one story", () => {
    const lines = historyLines([gate("failed"), gate("passed", "implement", "mechanical")]);
    expect(lines).toHaveLength(1);
    expect(lines[0].text).toBe("Gate implement failed, then passed after an automatic fix");
    expect(lines[0].tone).toBe("normal");
  });

  it("names the agent when that is what retried", () => {
    const lines = historyLines([gate("failed"), gate("passed", "implement", "agent")]);
    expect(lines[0].text).toBe("Gate implement failed, then passed after the agent retried");
  });

  it("does NOT join when the pass names no fix", () => {
    /* Without a fix tier the pass is a later evaluation that merely follows.
       Joining them would invent a causal link the data does not have — the
       mistake that made 'failed then passed' look like a recovery before
       lg-workflow-integrity-683 recorded what actually fixed things. */
    const lines = historyLines([gate("failed"), gate("passed", "implement", "none")]);
    expect(lines).toHaveLength(2);
  });

  it("does NOT join across different stages", () => {
    const lines = historyLines([gate("failed", "implement"), gate("passed", "review", "mechanical")]);
    expect(lines).toHaveLength(2);
  });

  it("tells 'could not run' apart from 'failed'", () => {
    /* One is about the work, the other about the machine. */
    const [line] = historyLines([gate("unavailable")]);
    expect(line.text).toBe("Gate implement could not run");
    expect(line.tone).toBe("unavailable");
  });

  it("leaves the other transition kinds reading as they did", () => {
    const lines = historyLines([stageEvent("StageStarted"), stageEvent("StageCompleted")]);
    expect(lines.map((l) => l.text)).toEqual([
      "Stage started implement",
      "Stage completed implement",
    ]);
    expect(lines.every((l) => l.tone === "normal")).toBe(true);
  });

  it("keeps a lone failure visible rather than swallowing it", () => {
    const lines = historyLines([gate("failed"), stageEvent("StageStarted")]);
    expect(lines).toHaveLength(2);
    expect(lines[0].tone).toBe("failed");
  });
});

describe("historyLines on events recorded before fix attribution existed", () => {
  it("does NOT join a failure to a later pass that carries no fix_tier key", () => {
    /* Every event predating lg-workflow-integrity-683 has no fix_tier at all,
       and the live log holds real `failed, failed, passed` runs on one stage.
       An earlier version of this rule tested `!== "none"`, which `undefined`
       passes — so it would have claimed a recovery nothing recorded. Found by
       running the rule over real events, not fixtures. */
    const legacy = (outcome: string) => ({
      id: `L${outcome}${Math.random()}`,
      type: "GateEvaluated",
      ticket_id: "t1",
      workspace_id: "w1",
      payload: { outcome, stage_key: "test_design" },
      created_at: "2026-08-01T10:00:00Z",
    });

    const lines = historyLines([legacy("failed"), legacy("passed")]);

    expect(lines).toHaveLength(2);
    expect(lines[0].text).toBe("Gate test_design failed");
    expect(lines[1].text).toBe("Gate test_design passed");
  });
});

function decision(kind: string, reason: string, stage = "implement"): TicketHistoryEvent {
  seq += 1;
  return {
    id: `e${seq}`,
    type: "OrchestratorDecision",
    ticket_id: "t1",
    workspace_id: "w1",
    payload: { decision: kind, stage_key: stage, reason },
    created_at: "2026-09-14T12:33:00Z",
  };
}

// lg-workflow-integrity-734: the orchestrator's own decisions were server log
// lines the UI never read. An unknown type used to render as its raw name.
describe("orchestrator decisions", () => {
  it("renders the reason as a sentence, not the raw event type", () => {
    const [line] = historyLines([
      decision("overruled_stale_gate", "Stored the ui-design-decision → spec handoff over the workspace gate."),
    ]);
    expect(line.text).toContain("Orchestrator:");
    expect(line.text).toContain("ui-design-decision → spec");
    expect(line.text).not.toContain("OrchestratorDecision");
  });

  it("reads a refusal as trouble and an overrule or a plan sign-off as normal", () => {
    const [refused, overruled, signedOff, classified, requeued, repairing, escalated] = historyLines([
      decision("refused_dispatch_terminal_parent", "Did not dispatch 'implement'."),
      decision("overruled_stale_gate", "Stored the handoff."),
      decision("approved_design_plan", "Approved the design plan from 'ui-design'."),
      decision("classified_block", "Block on 'implement' classified as decision."),
      decision("requeued_after_decision", "A person chose 'Try B'; requeued there."),
      decision("dispatched_repair", "Re-armed 'implement' for one repair turn."),
      decision("repair_escalated", "No second repair; a person reads it from here."),
    ]);
    expect(refused.tone).toBe("failed");
    expect(overruled.tone).toBe("normal");
    expect(signedOff.tone).toBe("normal");
    expect(classified.tone).toBe("failed"); // a block is still trouble until it is cleared
    expect(requeued.tone).toBe("normal");
    expect(repairing.tone).toBe("normal"); // the orchestrator is handling it
    expect(escalated.tone).toBe("failed"); // it could not; a person must
  });

  // 801: the sweep that finishes a ticket nothing was left to finish. Unlike
  // every other kind, how it went is not implied by the kind.
  it("tones a parked-terminal finish by the state it reached", () => {
    const finished = decision("finished_parked_terminal_stage", "Retried the finish, which ended done.", "done");
    finished.payload.state = "done";
    const stillBlocked = decision("finished_parked_terminal_stage", "Retried the finish, which ended blocked.", "done");
    stillBlocked.payload.state = "blocked";

    const [landed, blocked] = historyLines([finished, stillBlocked]);

    expect(landed.tone).toBe("normal");
    expect(blocked.tone).toBe("failed");
  });

  it("still says what it was when the reason is missing", () => {
    const [line] = historyLines([decision("settled_orphaned_run", "")]);
    expect(line.text).toContain("settled orphaned run");
  });
});
