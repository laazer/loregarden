import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { mergeQueueApi, type MergeQueueEntry, type PrCycleView } from "../../../../api/mergeQueueApi";
import type { PullRequestCheck, PullRequestStatus } from "../../../../api/ticketPullRequestApi";
import { PrCyclePrimitive } from "../PrCyclePrimitive";
import { planPrCycle } from "../prCycle";
import type { PrCyclePart } from "../types";

jest.mock("../../../../api/mergeQueueApi", () => {
  const actual = jest.requireActual("../../../../api/mergeQueueApi");
  return {
    ...actual,
    mergeQueueApi: { ...actual.mergeQueueApi, prCycle: jest.fn(), enqueuePullRequest: jest.fn() },
  };
});

const mockApi = mergeQueueApi as jest.Mocked<typeof mergeQueueApi>;

const PART: PrCyclePart = {
  primitive: "pr_cycle",
  workspace_slug: "loregarden",
  branch: "chat/rename-abcd",
  pre_push: "passed",
};

function check(name: string, outcome: PullRequestCheck["outcome"]): PullRequestCheck {
  return { name, outcome, url: `https://ci/${name}` };
}

function pr(extra: Partial<PullRequestStatus> = {}): PullRequestStatus {
  return {
    number: 42, url: "https://github.com/o/r/pull/42", title: "Rename the editor", state: "open",
    is_draft: false, base: "main", head: PART.branch, additions: 1, deletions: 1, changed_files: 1,
    review: "not_required", has_conflicts: false, checks: [check("server", "passing"), check("client", "passing")],
    body: "", head_sha: "a".repeat(40), mergeable_now: true, unsigned_commits: [], ...extra,
  };  // prettier-ignore
}

function entry(state: MergeQueueEntry["state"], extra: Partial<MergeQueueEntry> = {}): MergeQueueEntry {
  return {
    id: "e1", ticket_id: null, ticket_external_id: "", ticket_title: "", position: 3, ahead: 0, state,
    wait: "", is_head: false, pr_number: 42, pr_url: "", source_branch: PART.branch, head_sha: "", staged_sha: "",
    staged_tree: "", merge_commit: "", landed_tree: "", attempts: 1, detail: "", log_tail: "",
    conflicted_files: [], artifact_ref: "", can_withdraw: false, can_retry: false,
    enqueued_at: "2026-11-12T10:00:00Z", state_changed_at: "2026-11-12T10:00:00Z", ...extra,
  };  // prettier-ignore
}

function view(pull: PullRequestStatus | null, queued: MergeQueueEntry | null = null): PrCycleView {
  return {
    branch: PART.branch,
    pull_request: { lookup: pull ? "found" : "none", pull_request: pull, error: "" },
    entry: queued,
  };
}

describe("planPrCycle", () => {
  it.each([
    ["a failed pre-push", { ...PART, pre_push: "failed" as const }, undefined, "repair", ["bud-blocked", "bud-closed", "bud-closed"]],
    ["no PR yet", PART, view(null), "wait", ["leaf", "bud-closed", "bud-closed"]],
    ["checks still running", PART, view(pr({ checks: [check("server", "pending"), check("client", "passing")] })), "wait", ["leaf", "bud-open", "bud-closed"]],
    ["no checks reported yet", PART, view(pr({ checks: [] })), "wait", ["leaf", "bud-open", "bud-closed"]],
    ["a failing check", PART, view(pr({ checks: [check("server", "failing")] })), "repair", ["leaf", "bud-blocked", "bud-closed"]],
    ["conflicts with the base", PART, view(pr({ has_conflicts: true })), "repair", ["leaf", "bud-blocked", "bud-closed"]],
    ["a green draft", PART, view(pr({ is_draft: true })), "wait", ["leaf", "bud-held", "bud-closed"]],
    ["a green PR not queued", PART, view(pr()), "enqueue", ["leaf", "leaf", "bud-closed"]],
    ["a green PR withdrawn from the queue", PART, view(pr(), entry("withdrawn")), "enqueue", ["leaf", "leaf", "bud-closed"]],
    ["a PR in the queue", PART, view(pr(), entry("testing")), "wait", ["leaf", "leaf", "bud-open"]],
    ["a PR the queue stopped", PART, view(pr(), entry("failed", { detail: "Server (Python) failed" })), "repair", ["leaf", "leaf", "bud-blocked"]],
    ["a merged PR", PART, view(pr({ state: "merged" })), "wait", ["leaf", "leaf", "leaf"]],
    ["a closed PR", PART, view(pr({ state: "closed" })), "wait", ["leaf", "pruned", "pruned"]],
  ] as const)("%s", (_name, part, data, action, marks) => {
    const plan = planPrCycle(part, data);

    expect(plan.action.kind).toBe(action);
    expect(plan.stages.map((stage) => stage.mark)).toEqual(marks);
  });

  it("hands the repair turn the tail of the failing push", () => {
    const detail = Array.from({ length: 60 }, (_, i) => `line ${i}`).join("\n");
    const plan = planPrCycle({ ...PART, pre_push: "failed", pre_push_detail: detail }, undefined);

    expect(plan.action.kind).toBe("repair");
    const prompt = plan.action.kind === "repair" ? plan.action.prompt : "";
    expect(prompt).toContain(PART.branch);
    expect(prompt).toContain("line 59");
    expect(prompt).not.toContain("line 19\n");
  });

  it("names each failing check and where its log is", () => {
    const plan = planPrCycle(PART, view(pr({ checks: [check("server", "failing"), check("client", "passing")] })));

    const prompt = plan.action.kind === "repair" ? plan.action.prompt : "";
    expect(prompt).toContain("- server: https://ci/server");
    expect(prompt).not.toContain("client");
  });

  it("keeps a failed GitHub read apart from no PR", () => {
    const failed = planPrCycle(PART, {
      ...view(null),
      pull_request: { lookup: "failed", pull_request: null, error: "HTTP 502" },
    });

    expect(failed.action).toMatchObject({ kind: "wait", label: "Can't read PR" });
    expect(planPrCycle(PART, view(null)).action).toMatchObject({ kind: "wait", label: "No PR yet" });
  });
});

function renderCard(part: PrCyclePart, onSubmit?: (content: string) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PrCyclePrimitive part={part} onSubmit={onSubmit} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PrCyclePrimitive", () => {
  beforeEach(() => jest.resetAllMocks());

  it("asks the thread's agent to repair a failed pre-push, once, without asking GitHub", () => {
    const onSubmit = jest.fn();
    renderCard({ ...PART, pre_push: "failed", pre_push_detail: "client-tests: 3 failed" }, onSubmit);

    fireEvent.click(screen.getByRole("button", { name: "Repair" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toContain("client-tests: 3 failed");
    expect(screen.getByRole("button", { name: "Repair requested" })).toBeDisabled();
    expect(mockApi.prCycle).not.toHaveBeenCalled();
  });

  it("offers no repair outside a thread", () => {
    renderCard({ ...PART, pre_push: "failed" });

    expect(screen.getByRole("button", { name: "Repair" })).toBeDisabled();
  });

  it("enqueues a green PR once and disables the button while it is in flight", async () => {
    mockApi.prCycle.mockResolvedValue(view(pr()));
    mockApi.enqueuePullRequest.mockReturnValue(new Promise(() => {}));
    renderCard(PART);

    const button = await screen.findByRole("button", { name: "Enqueue PR" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(await screen.findByRole("button", { name: "Enqueuing…" })).toBeDisabled();
    expect(mockApi.enqueuePullRequest).toHaveBeenCalledTimes(1);
    expect(mockApi.enqueuePullRequest).toHaveBeenCalledWith("loregarden", 42);
  });

  it("shows the queue's refusal beside the button", async () => {
    mockApi.prCycle.mockResolvedValue(view(pr()));
    mockApi.enqueuePullRequest.mockRejectedValue(new Error("PR #42 is not mergeable: review required"));
    renderCard(PART);

    fireEvent.click(await screen.findByRole("button", { name: "Enqueue PR" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("review required");
  });

  it("disables the button once there is nothing left to do", async () => {
    mockApi.prCycle.mockResolvedValue(view(pr({ state: "merged" })));
    renderCard(PART);

    expect(await screen.findByRole("button", { name: "Merged" })).toBeDisabled();
  });

  it("says when GitHub could not be asked", async () => {
    mockApi.prCycle.mockResolvedValue({
      ...view(null),
      pull_request: { lookup: "failed", pull_request: null, error: "HTTP 502: bad gateway" },
    });
    renderCard(PART);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("HTTP 502: bad gateway"));
    expect(screen.getByRole("button", { name: "Can't read PR" })).toBeDisabled();
  });
});
