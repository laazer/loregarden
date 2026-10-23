import type { TicketDetail, WorkflowStageView } from "../../api/client";
import {
  buildOrchestrateTerminalCommand,
  buildStageRunTerminalCommand,
  copyStageTerminalHandoffCommand,
  isAgentWorkflowTicket,
} from "../terminalCommands";
import { isAgentStage } from "../stageDisplay";
import { useToastStore } from "../../state/toastStore";

// terminalCommands.ts imports the real `api` object (not just types) so it can call
// buildTerminalHandoffCommand. Mock it here rather than let Jest load the real client.ts,
// which reads import.meta.env — a syntax this project's swc+CJS Jest transform can't parse.
jest.mock("../../api/client", () => ({
  api: { buildTerminalHandoffCommand: jest.fn() },
}));
import { api } from "../../api/client";

const mockBuildTerminalHandoffCommand = api.buildTerminalHandoffCommand as jest.Mock;

const COMMAND = 'claude --add-dir . --append-system-prompt-file "$f"';
const REPO_PATH = "/Users/dev/workspace/.worktrees/ticket-loregarden-lg-917-e7b1752d";
const TARGET = "integration/lg-milestone-that-491";

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
    branch: "loregarden/03-wire-cli-agent-runner",
    child_count: 0,
    description: "",
    acceptance_criteria: [],
    checked_acceptance_criteria: [],
    revision: 1,
    last_updated_by: "",
    current_stage_agent: "",
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

function handoffResponse(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "run-1",
    adapter: "claude",
    command: COMMAND,
    repo_path: REPO_PATH,
    target_branch: TARGET,
    tree_behind: 0,
    tree_warning: "",
    ...overrides,
  };
}

/** A promise the test resolves itself, so the in-flight state can be observed. */
function deferred<T>() {
  let settle!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    settle = res;
    reject = rej;
  });
  return { promise, settle, reject };
}

const toasts = () => useToastStore.getState().toasts;

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

  // The action this ticket made slow. The server now merges up to 134 commits
  // (measured: 33 of 39 worktrees behind, median 40) inside this one call, and
  // `OverflowMenu` closes on select — so the toast is the only place left to
  // say anything at all about what happened.
  describe("copyStageTerminalHandoffCommand", () => {
    const writeText = jest.fn().mockResolvedValue(undefined);

    beforeEach(() => {
      jest.clearAllMocks();
      useToastStore.getState().clear();
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
      });
    });

    afterEach(() => {
      mockBuildTerminalHandoffCommand.mockReset();
    });

    it("says it is working while the server merges, and takes that back when it settles", async () => {
      // AC15. A multi-second action with no feedback reads as a dead click;
      // the sticky toast is dismissed in a `finally`, so it cannot outlive
      // either branch of the call.
      const pending = deferred<ReturnType<typeof handoffResponse>>();
      mockBuildTerminalHandoffCommand.mockReturnValueOnce(pending.promise);

      const copying = copyStageTerminalHandoffCommand(
        baseTicket(),
        baseStage({ key: "spec", name: "Specification" }),
      );

      const inFlight = toasts().find((toast) => toast.tone === "info");
      expect(inFlight).toBeDefined();
      expect(inFlight!.duration).toBe(0);
      expect(inFlight!.message).toContain("Specification");
      expect(inFlight!.message).toContain("loregarden/03-wire-cli-agent-runner");
      expect(inFlight!.message).toContain("target");

      pending.settle(handoffResponse());
      await copying;

      expect(toasts().some((toast) => toast.id === inFlight!.id)).toBe(false);
    });

    it("S11/UX1 — two overlapping copies keep the in-flight toast until the LAST one settles", async () => {
      // The defect this ticket was filed for, through the real surface. Both
      // calls push a byte-identical in-flight toast, so lg-external-harness-
      // 1203's dedupe refreshes one toast in place — and before the claim
      // split, both callers were handed the SAME string, so the first
      // `finally` took the indicator away while the second was still awaiting
      // the server's merge.
      //
      // The in-flight toast is found by TONE, never by `duration === 0`: an
      // error toast is sticky by default since 1203, so a duration-based filter
      // would match the failure toast as well.
      const first = deferred<ReturnType<typeof handoffResponse>>();
      const second = deferred<ReturnType<typeof handoffResponse>>();
      mockBuildTerminalHandoffCommand
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise);

      const ticket = baseTicket();
      const stage = baseStage({ key: "spec", name: "Specification" });
      const copyingFirst = copyStageTerminalHandoffCommand(ticket, stage);
      const copyingSecond = copyStageTerminalHandoffCommand(ticket, stage);

      const inFlight = toasts().filter((toast) => toast.tone === "info");
      expect(inFlight).toHaveLength(1);

      first.settle(handoffResponse());
      await copyingFirst;

      // UX1: the second caller is still awaiting the server, and the in-flight
      // toast is the only thing on screen answering "is it still working?".
      const stillUp = toasts().filter((toast) => toast.tone === "info");
      expect(stillUp).toHaveLength(1);
      expect(stillUp[0].id).toBe(inFlight[0].id);

      second.settle(handoffResponse());
      await copyingSecond;

      expect(toasts().filter((toast) => toast.tone === "info")).toHaveLength(0);
    });

    it("test-break — the SECOND copy settling first does not take the first's indicator", async () => {
      // S11/UX1 in the other settle order, which the designed pair cannot see.
      // A dedupe branch that resets the claim set keeps only the LAST claim, so
      // the second caller's `finally` empties it and the indicator goes while
      // the first caller is still awaiting the server's merge — this ticket's
      // defect, with the two callers swapped.
      const first = deferred<ReturnType<typeof handoffResponse>>();
      const second = deferred<ReturnType<typeof handoffResponse>>();
      mockBuildTerminalHandoffCommand
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise);

      const ticket = baseTicket();
      const stage = baseStage({ key: "spec", name: "Specification" });
      const copyingFirst = copyStageTerminalHandoffCommand(ticket, stage);
      const copyingSecond = copyStageTerminalHandoffCommand(ticket, stage);
      const inFlightId = toasts().find((toast) => toast.tone === "info")!.id;

      second.settle(handoffResponse());
      await copyingSecond;

      const stillUp = toasts().filter((toast) => toast.tone === "info");
      expect(stillUp).toHaveLength(1);
      expect(stillUp[0].id).toBe(inFlightId);

      first.settle(handoffResponse());
      await copyingFirst;

      expect(toasts().filter((toast) => toast.tone === "info")).toHaveLength(0);
    });

    it("test-break — one caller's failure does not retract the other's indicator", async () => {
      // The `finally` runs on the refusal branch too (S10), so a failing caller
      // releases its claim while its twin is still in flight. UX1 is about the
      // span, not about the happy path: the operator who still has a command
      // being prepared must keep the only thing on screen that says so, even
      // once the other attempt has reported its own failure.
      const failing = deferred<ReturnType<typeof handoffResponse>>();
      const succeeding = deferred<ReturnType<typeof handoffResponse>>();
      mockBuildTerminalHandoffCommand
        .mockReturnValueOnce(failing.promise)
        .mockReturnValueOnce(succeeding.promise);

      const ticket = baseTicket();
      const stage = baseStage({ key: "spec", name: "Specification" });
      const copyingFailing = copyStageTerminalHandoffCommand(ticket, stage);
      const copyingSucceeding = copyStageTerminalHandoffCommand(ticket, stage);
      const inFlightId = toasts().find((toast) => toast.tone === "info")!.id;

      failing.reject(new Error("lg-917: the worktree at /tmp/w could not take integration/x"));
      expect(await copyingFailing).toBe(false);

      expect(toasts().filter((toast) => toast.tone === "error")).toHaveLength(1);
      const stillUp = toasts().filter((toast) => toast.tone === "info");
      expect(stillUp).toHaveLength(1);
      expect(stillUp[0].id).toBe(inFlightId);

      succeeding.settle(handoffResponse());
      expect(await copyingSucceeding).toBe(true);

      expect(toasts().filter((toast) => toast.tone === "info")).toHaveLength(0);
      expect(toasts().map((toast) => toast.tone).sort()).toEqual(["error", "success"]);
    });

    it("reports the tree the copied command will run in", async () => {
      // AC16. The operator's question is "can I paste this and trust the tree
      // it will run in?" — so the success toast names the tree and the branch
      // it was brought up to, not just that something was copied.
      mockBuildTerminalHandoffCommand.mockResolvedValueOnce(handoffResponse({ tree_behind: 0 }));

      const copied = await copyStageTerminalHandoffCommand(
        baseTicket(),
        baseStage({ key: "spec", name: "Specification" }),
      );

      expect(copied).toBe(true);
      expect(mockBuildTerminalHandoffCommand).toHaveBeenCalledWith("ticket-uuid-1", "spec");
      expect(writeText).toHaveBeenCalledWith(expect.stringContaining(COMMAND));
      const outcomes = toasts().filter((toast) => toast.tone !== "info");
      expect(outcomes).toHaveLength(1);
      expect(outcomes[0].tone).toBe("success");
      expect(`${outcomes[0].title} ${outcomes[0].message}`).toContain("Specification");
      expect(outcomes[0].message).toContain(REPO_PATH);
      expect(outcomes[0].message).toContain(TARGET);
    });

    it("copies the command but warns when the tree was left behind", async () => {
      // AC17. One handoff in five (7 of 39 worktrees measured dirty). The
      // command is still copied — a handoff is not blocked by an un-refreshable
      // tree — and the server's sentence is shown verbatim, because it is the
      // one that carries both counts and the action that fixes them.
      const warning =
        `${REPO_PATH} has 3 uncommitted changes, so it was not brought up to ` +
        `${TARGET} (40 commits behind); commit or stash, then copy again.`;
      mockBuildTerminalHandoffCommand.mockResolvedValueOnce(
        handoffResponse({ tree_behind: 40, tree_warning: warning }),
      );

      const copied = await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

      expect(copied).toBe(true);
      expect(writeText).toHaveBeenCalledWith(expect.stringContaining(COMMAND));
      const outcomes = toasts().filter((toast) => toast.tone !== "info");
      expect(outcomes).toHaveLength(1);
      expect(outcomes[0].tone).toBe("warning");
      expect(outcomes[0].message).toBe(warning);
      expect(outcomes.some((toast) => toast.tone === "success")).toBe(false);
    });

    it("reports a refusal from the server instead of returning quietly", async () => {
      // AC18. `describeError` keeps the 400 `detail`, which is the only place
      // the conflict is named. The old handler's clipboard fallback swallowed
      // its own failure and the copy handler returned silently.
      mockBuildTerminalHandoffCommand.mockRejectedValueOnce(
        new Error("lg-917: the worktree at /tmp/w could not take integration/x; resolve it there."),
      );

      const copied = await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

      expect(copied).toBe(false);
      const outcomes = toasts().filter((toast) => toast.tone !== "info");
      expect(outcomes).toHaveLength(1);
      expect(outcomes[0].tone).toBe("error");
      expect(outcomes[0].title).toContain("Copy terminal command");
      expect(outcomes[0].message).toContain("could not take integration/x");
      // The in-flight toast is identified by its TONE, not by `duration === 0`.
      // lg-external-harness-1203 makes an error toast sticky by default, so
      // `duration === 0` is now also the signature of the failure toast this
      // test wants to keep — the old assertion would reject the fix.
      expect(toasts().filter((toast) => toast.tone === "info")).toHaveLength(0);
    });

    // lg-milestone-that-917, test-break: the sticky toast is the one piece of
    // state that can outlive the call. AC15 puts its dismissal in a `finally`,
    // and the tests above only prove that on the two paths that reach the
    // server's answer — a `releaseToast` placed at the end of the success
    // branch, or in the `catch` of the API call alone, passes all of them and
    // leaves a toast on screen forever on the path where the request succeeded
    // and the clipboard refused.
    it("takes the in-flight toast back when the clipboard is what refused", async () => {
      mockBuildTerminalHandoffCommand.mockResolvedValueOnce(handoffResponse());
      writeText.mockRejectedValueOnce(new Error("Document is not focused"));
      Object.defineProperty(document, "execCommand", {
        value: () => {
          throw new Error("copy is not supported");
        },
        configurable: true,
      });

      await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

      // Tone, not duration: see the note above. The sticky error toast this
      // path now raises is the one the operator needs, and it is the one the
      // `duration === 0` spelling of this assertion used to reject.
      expect(toasts().filter((toast) => toast.tone === "info")).toHaveLength(0);
    });

    it("reports only the failure when a degraded tree is also a failed copy", async () => {
      // AC17 and AC18 at once, which is the combination the live numbers make
      // ordinary: 7 of 39 trees are dirty, and the clipboard refuses whenever
      // the document is not focused — a menu click. Pushing the warning first
      // and the error second tells the operator, in the toast that stays
      // longest, that a command they do not have is waiting for them.
      const warning =
        `${REPO_PATH} has 1 uncommitted changes, so it was not brought up to ` +
        `${TARGET} (40 commits behind); commit or stash, then copy again.`;
      mockBuildTerminalHandoffCommand.mockResolvedValueOnce(
        handoffResponse({ tree_behind: 40, tree_warning: warning }),
      );
      writeText.mockRejectedValueOnce(new Error("Document is not focused"));
      Object.defineProperty(document, "execCommand", {
        value: () => {
          throw new Error("copy is not supported");
        },
        configurable: true,
      });

      const copied = await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

      expect(copied).toBe(false);
      const outcomes = toasts().filter((toast) => toast.tone !== "info");
      expect(outcomes).toHaveLength(1);
      expect(outcomes[0].tone).toBe("error");
    });

    it("reports a clipboard that refused the write rather than claiming a copy", async () => {
      // AC18. `copyText` throws when neither the async API nor the textarea
      // fallback worked; a `true` return there tells the operator a command is
      // on their clipboard when nothing is.
      mockBuildTerminalHandoffCommand.mockResolvedValueOnce(handoffResponse());
      writeText.mockRejectedValueOnce(new Error("Document is not focused"));
      // jsdom has no `execCommand`, so define the refusal explicitly rather
      // than lean on an absence that a future jsdom may fill in.
      Object.defineProperty(document, "execCommand", {
        value: () => {
          throw new Error("copy is not supported");
        },
        configurable: true,
      });

      const copied = await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

      expect(copied).toBe(false);
      const outcomes = toasts().filter((toast) => toast.tone !== "info");
      expect(outcomes).toHaveLength(1);
      expect(outcomes[0].tone).toBe("error");
    });

    it("S7.4 — reports a refusal that came back as false rather than as a throw", async () => {
      // The sibling the test above was written for and could not express. A
      // real browser that declines the write returns `false` from
      // `execCommand`; only jsdom's absence of it throws. Returning `false`
      // used to be indistinguishable from a copy, so the operator was told a
      // command was on their clipboard when nothing was.
      mockBuildTerminalHandoffCommand.mockResolvedValueOnce(handoffResponse());
      writeText.mockRejectedValueOnce(new Error("Document is not focused"));
      Object.defineProperty(document, "execCommand", {
        value: () => false,
        configurable: true,
      });

      const copied = await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

      expect(copied).toBe(false);
      const outcomes = toasts().filter((toast) => toast.tone !== "info");
      expect(outcomes).toHaveLength(1);
      expect(outcomes[0].tone).toBe("error");
    });

    it("test-break — a session's worth of copies leaves nothing holding the stack", async () => {
      // The one production function that uses BOTH routes: its in-flight toast
      // is claimed, its success and warning toasts are fire-and-forget. A
      // repeated copy of the same stage produces a byte-identical success
      // toast, so the fire-and-forget route takes the dedupe branch every time
      // after the first — the growth path this ticket bounds, reached through
      // the real caller rather than through the store.
      //
      // Twelve rounds, three outcomes each, so the failure and warning arms
      // dedupe too: the `finally` releases on the refusal branch as well, and a
      // claim leaked there is invisible to a happy-path loop.
      for (let round = 0; round < 12; round += 1) {
        if (round % 3 === 0) {
          mockBuildTerminalHandoffCommand.mockResolvedValueOnce(handoffResponse());
        } else if (round % 3 === 1) {
          mockBuildTerminalHandoffCommand.mockResolvedValueOnce(
            handoffResponse({ tree_warning: "Worktree left behind" }),
          );
        } else {
          mockBuildTerminalHandoffCommand.mockRejectedValueOnce(new Error("no slots"));
        }

        await copyStageTerminalHandoffCommand(baseTicket(), baseStage());

        // Checked every round, not only at the end: a leak that the next round
        // dedupes onto is one the final state cannot distinguish from a bound.
        expect(toasts().flatMap((toast) => toast.claims)).toEqual([]);
        expect(toasts().some((toast) => toast.tone === "info")).toBe(false);
      }

      expect(toasts().length).toBeLessThanOrEqual(4);
    });

    it("test-break — overlapping copies hold exactly one claim each, and no more", async () => {
      // The claimed route's own bound. Four concurrent callers dedupe onto one
      // in-flight toast, so the set is as long as the number of callers still
      // awaiting the server — never longer, and empty once they settle. An
      // implementation that appended on the fire-and-forget route too would be
      // counted here as well, because each caller's success toast lands while
      // the others are still open.
      const pending = [0, 1, 2, 3].map(() => deferred<ReturnType<typeof handoffResponse>>());
      for (const one of pending) mockBuildTerminalHandoffCommand.mockReturnValueOnce(one.promise);

      const ticket = baseTicket();
      const stage = baseStage({ key: "spec", name: "Specification" });
      const copying = pending.map(() => copyStageTerminalHandoffCommand(ticket, stage));

      const inFlight = toasts().filter((toast) => toast.tone === "info");
      expect(inFlight).toHaveLength(1);
      expect(inFlight[0].claims).toHaveLength(4);

      for (const [at, one] of pending.entries()) {
        one.settle(handoffResponse());
        await copying[at];

        const still = toasts().filter((toast) => toast.tone === "info");
        if (at < pending.length - 1) {
          expect(still).toHaveLength(1);
          expect(still[0].id).toBe(inFlight[0].id);
          expect(still[0].claims).toHaveLength(pending.length - 1 - at);
        } else {
          expect(still).toHaveLength(0);
        }
      }

      expect(toasts().flatMap((toast) => toast.claims)).toEqual([]);
    });
  });
});
