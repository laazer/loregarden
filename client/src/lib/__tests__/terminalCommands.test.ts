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
      expect(toasts().some((toast) => toast.duration === 0)).toBe(false);
    });

    // lg-milestone-that-917, test-break: the sticky toast is the one piece of
    // state that can outlive the call. AC15 puts its dismissal in a `finally`,
    // and the tests above only prove that on the two paths that reach the
    // server's answer — a `dismissToast` placed at the end of the success
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

      expect(toasts().filter((toast) => toast.duration === 0)).toHaveLength(0);
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
  });
});
