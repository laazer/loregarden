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
    const [refused, overruled, signedOff, classified, requeued, repairing, resolving, escalated] = historyLines([
      decision("refused_dispatch_terminal_parent", "Did not dispatch 'implement'."),
      decision("overruled_stale_gate", "Stored the handoff."),
      decision("approved_design_plan", "Approved the design plan from 'ui-design'."),
      decision("classified_block", "Block on 'implement' classified as decision."),
      decision("requeued_after_decision", "A person chose 'Try B'; requeued there."),
      decision("dispatched_repair", "Re-armed 'implement' for one repair turn."),
      decision("dispatched_landing_resolver", "Landing conflicted in migrations.py; armed a resolution turn."),
      decision("repair_escalated", "No second repair; a person reads it from here."),
    ]);
    expect(refused.tone).toBe("failed");
    expect(overruled.tone).toBe("normal");
    expect(signedOff.tone).toBe("normal");
    expect(classified.tone).toBe("failed"); // a block is still trouble until it is cleared
    expect(requeued.tone).toBe("normal");
    expect(repairing.tone).toBe("normal"); // the orchestrator is handling it
    expect(resolving.tone).toBe("normal"); // likewise: a resolver is coming, not a person
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

// lg-milestone-that-916: a block that cleared looked like it cleared itself.
// The block, the repair's own account and the requeue reason were recorded,
// but on nothing the rail read.
describe("what a repair or requeue was", () => {
  const block = "'integration/x' cannot take 'main': merge conflicts in land_ticket.py";

  it("carries the block a repair was sent to", () => {
    const sent = decision("dispatched_repair", "Re-armed 'triage' for one repair turn.", "triage");
    sent.payload.block_message = block;
    const escalated = decision("repair_escalated", "The repair turn blocked too.", "triage");
    escalated.payload.block_message = block;

    const lines = historyLines([sent, escalated]);

    expect(lines.map((line) => line.detail)).toEqual([block, block]);
  });

  it("carries the repair agent's account of what it fixed", () => {
    const repaired = decision("repaired", "The repair turn cleared the block.");
    repaired.payload.repair_summary = "Cause: stale lockfile. Fix: regenerated it.";

    const [line] = historyLines([repaired]);

    expect(line.detail).toBe("Cause: stale lockfile. Fix: regenerated it.");
  });

  it("shows a requeue's stage and actor on the line and its reason as the detail", () => {
    const requeued = decision("requeued", "491 landed on main, so the conflict is gone.", "triage");
    requeued.payload.actor = "mcp";

    const [line] = historyLines([requeued]);

    expect(line.text).toContain("triage");
    expect(line.text).toContain("loregarden_requeue_ticket");
    expect(line.detail).toBe("491 landed on main, so the conflict is gone.");
    expect(line.tone).toBe("normal");
  });

  it("has no detail when the event recorded nothing more", () => {
    const [line] = historyLines([decision("classified_block", "Block classified as work.")]);
    expect(line.detail).toBeUndefined();
  });
});

// lg-milestone-that-976: a handoff stamped a run RUNNING, could not prepare the
// tree, and put the stage back to PENDING with no agent ever behind it. The
// failure text is a lead sentence followed by git's merge output, and the rail
// renders `line.text` verbatim with no clamp — so the sentence is the reason and
// the whole of the text travels as `detail`, behind the <details> the rail has.
describe("a dispatch that never attempted the work", () => {
  const reason =
    "Stage review's run run_a1b2c3 never attempted the work — lg-x-1: resolve the conflict " +
    "with integration/lg-x-1. The stage was put back to PENDING, ready to be dispatched again.";
  const detail =
    "lg-x-1: resolve the conflict with integration/lg-x-1, or run the stage supervised.\n" +
    "CONFLICT (content): Merge conflict in shared.txt\n" +
    "Automatic merge failed; fix conflicts and then commit the result.";

  it("puts the sentence on the line and the whole failure text behind it", () => {
    const settled = decision("settled_failed_handoff", reason, "review");
    settled.payload.detail = detail;

    const [line] = historyLines([settled]);

    expect(line.text).toContain("run_a1b2c3");
    expect(line.text).toContain("PENDING");
    expect(line.text).not.toContain("Automatic merge failed");
    expect(line.detail).toBe(detail);
  });

  it("reads as trouble, like the other settles", () => {
    const [line] = historyLines([decision("settled_failed_handoff", reason, "review")]);
    expect(line.tone).toBe("failed");
  });

  // `detail` is the LAST fallback, so the kinds that already had a detail are
  // unchanged: a repair's own account of what it fixed still wins.
  it("still prefers a repair summary over a detail", () => {
    const repaired = decision("repaired", "The repair turn cleared the block.");
    repaired.payload.repair_summary = "Cause: stale lockfile. Fix: regenerated it.";
    repaired.payload.detail = detail;

    const [line] = historyLines([repaired]);

    expect(line.detail).toBe("Cause: stale lockfile. Fix: regenerated it.");
  });

  // ...and the requeued arm, which reads `reason` rather than the fallbacks.
  it("leaves a requeue showing its reason as the detail", () => {
    const requeued = decision("requeued", "491 landed on main, so the conflict is gone.", "triage");
    requeued.payload.actor = "mcp";
    requeued.payload.detail = detail;

    const [line] = historyLines([requeued]);

    expect(line.detail).toBe("491 landed on main, so the conflict is gone.");
  });
});

// The three arms of `decisionDetail` the cases above leave unexercised. AC9 is
// one line, and "last fallback" is the whole of it: an edit that lands the new
// read anywhere else passes every case above.
describe("where a decision's detail comes from", () => {
  const detail =
    "lg-x-1: resolve the conflict with integration/lg-x-1, or run the stage supervised.\n" +
    "CONFLICT (content): Merge conflict in shared.txt";

  // `repair_summary` is pinned above; `block_message` is the other existing
  // read, and it sits BETWEEN the summary and the new one. An implementation
  // reading `repair_summary || detail || block_message` is green up there.
  it("still prefers the block it was sent to over a detail", () => {
    const dispatched = decision("dispatched_repair", "A repair turn was armed.");
    dispatched.payload.block_message = "Harness: the CLI exited 1 with no output.";
    dispatched.payload.detail = detail;

    const [line] = historyLines([dispatched]);

    expect(line.detail).toBe("Harness: the CLI exited 1 with no output.");
  });

  // The requeued arm reads `reason` and nothing else. A requeue recorded with
  // no reason showed no detail before this change and must still show none:
  // adding the new read to both arms is the easy way to write the one line.
  it("leaves a requeue with no reason showing no detail at all", () => {
    const requeued = decision("requeued", "", "triage");
    requeued.payload.actor = "mcp";
    requeued.payload.detail = detail;

    const [line] = historyLines([requeued]);

    expect(line.text).toContain("Requeued triage");
    expect(line.detail).toBeUndefined();
  });

  // AC11: the line a settle renders is its reason, verbatim and prefixed — the
  // fallback that spells the kind out is never the one an operator sees, and a
  // settle that carried no failure text still opens nothing behind the line.
  it("shows the settle's own sentence, and nothing behind it when there is no text", () => {
    const reason =
      "Stage review's run run_a1b2c3 never attempted the work — lg-x-1: resolve the " +
      "conflict with integration/lg-x-1. The stage was put back to PENDING, ready to " +
      "be dispatched again.";

    const [line] = historyLines([decision("settled_failed_handoff", reason, "review")]);

    expect(line.text).toBe(`Orchestrator: ${reason}`);
    expect(line.text).not.toContain("settled failed handoff");
    expect(line.detail).toBeUndefined();
  });
});
