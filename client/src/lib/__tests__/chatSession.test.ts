import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  isChatThinking,
  isRunStatusBusy,
  showsChatWalker,
  type ChatSession,
  type ChatSessionKind,
} from "../chatSession";

function waitingSession(): ChatSession {
  return {
    kind: "baxter-home",
    id: "s1",
    messages: [],
    isBusy: true,
    activeTurnId: null,
    canAct: true,
    isLoading: false,
    error: null,
    loadError: false,
    send: async () => undefined,
    stop: async () => undefined,
    isStopping: false,
    runStatus: "waiting",
    wait: {
      command: "git push origin HEAD",
      log_path: "/tmp/lg-wait/push.log",
      registered_at: "2026-10-27T21:00:00Z",
      deadline: "2026-10-27T23:00:00Z",
    },
  };
}

describe("isRunStatusBusy", () => {
  it("treats waiting as busy so the composer stays locked", () => {
    expect(isRunStatusBusy("waiting")).toBe(true);
    expect(isRunStatusBusy("running")).toBe(true);
    expect(isRunStatusBusy("awaiting_input")).toBe(true);
    expect(isRunStatusBusy("idle")).toBe(false);
    expect(isRunStatusBusy(null)).toBe(false);
    expect(isRunStatusBusy(undefined)).toBe(false);
  });
});

describe("isChatThinking", () => {
  it("is only the in-flight turn, never a wait or an ask", () => {
    expect(isChatThinking("running")).toBe(true);
    expect(isChatThinking("waiting")).toBe(false);
    expect(isChatThinking("awaiting_input")).toBe(false);
    expect(isChatThinking("idle")).toBe(false);
    expect(isChatThinking(null)).toBe(false);
    expect(isChatThinking(undefined)).toBe(false);
  });
});

describe("showsChatWalker", () => {
  it("excludes waiting and awaiting_input the way Home already excluded the ask", () => {
    expect(showsChatWalker(true, "running")).toBe(true);
    expect(showsChatWalker(true, "waiting")).toBe(false);
    expect(showsChatWalker(true, "awaiting_input")).toBe(false);
    expect(showsChatWalker(true, "idle")).toBe(true);
    expect(showsChatWalker(true, undefined)).toBe(true);
    expect(showsChatWalker(false, "running")).toBe(true);
    expect(showsChatWalker(false, "idle")).toBe(false);
  });
});

it("carries runStatus and wait on ChatSession", () => {
  const session = waitingSession();
  expect(session.runStatus).toBe("waiting");
  expect(session.wait?.command).toBe("git push origin HEAD");
  expect(session.activeTurnId).toBeNull();
});

it("does not grow ChatSessionKind for a wait", () => {
  const kinds: ChatSessionKind[] = [
    "ticket-triage",
    "branch-triage",
    "ticket-studio",
    "baxter-home",
    "initiative-planner",
  ];
  expect(kinds).toHaveLength(5);
  const src = readFileSync(join(__dirname, "../chatSession.ts"), "utf8");
  const match = src.match(/export type ChatSessionKind =([^;]+);/);
  expect(match?.[1]).toBeDefined();
  expect(match?.[1]).not.toMatch(/waiting/);
  expect(match?.[1]?.match(/"[^"]+"/g)).toEqual([
    '"ticket-triage"',
    '"branch-triage"',
    '"ticket-studio"',
    '"baxter-home"',
    '"initiative-planner"',
  ]);
});
