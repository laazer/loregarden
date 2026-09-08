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
