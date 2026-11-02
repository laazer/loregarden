import {
  agentFix,
  blockedOnStateLabel,
  blockHeadline,
  blockKindLabel,
  blockKindMeaning,
  stuckTicketChip,
} from "../blockKinds";
import type { BlockKind } from "../../api/types";

const KINDS: BlockKind[] = ["harness", "work", "decision", "human_action"];

describe("block kinds", () => {
  it("tells the person whether they need to act, for every kind", () => {
    for (const kind of KINDS) {
      expect(blockKindLabel(kind)).not.toBe("");
      expect(blockKindMeaning(kind)).not.toBe("");
    }
    // The two a person need not touch say so; the two they must, say what.
    expect(blockKindMeaning("harness")).toMatch(/No action needed/);
    expect(blockKindMeaning("work")).toMatch(/No action needed/);
    expect(blockKindMeaning("decision")).toMatch(/Answer the question/);
    expect(blockKindMeaning("human_action")).toMatch(/your hands/);
  });
});

describe("an agent's block", () => {
  const stuck = { state: "blocked" as const, blocked_on: "agent" as const };

  it("names who starts the fix instead of saying blocked", () => {
    expect(stuckTicketChip({ ...stuck, agent_retry: "automatic" })).toMatchObject({ tone: "wait", label: "Agent fixing" });
    expect(stuckTicketChip({ ...stuck, agent_retry: "on_request" })).toMatchObject({ tone: "wait", label: "Agent can fix" });
    expect(blockedOnStateLabel("blocked", "agent", "automatic")).toBe("Blocked · Agent fixing");
  });

  it("promises nothing automatic from a server that predates agent_retry", () => {
    expect(agentFix(stuck)).toBe("on_request");
  });

  it("leaves a person's block red and named for them", () => {
    expect(stuckTicketChip({ state: "blocked", blocked_on: "person" })).toMatchObject({ tone: "bad", label: "Blocked" });
    expect(agentFix({ state: "blocked", blocked_on: "tickets", agent_retry: "on_request" })).toBeNull();
  });

  it("does not promise a repair on an agent's kind with no retry left", () => {
    const spent = blockHeadline({ state: "blocked", blocked_on: "person", block_kind: "work" });
    expect(spent.label).toBe("Blocked · needs you");
    expect(spent.meaning).not.toMatch(/No action needed/);
    expect(blockHeadline({ ...stuck, block_kind: "work", agent_retry: "on_request" }).label).toBe("Blocked · agent can fix");
  });
});
