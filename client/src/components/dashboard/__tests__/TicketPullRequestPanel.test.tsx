import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import type { PullRequestStatus, TicketPullRequest } from "../../../api/ticketPullRequestApi";
import { uiActionRegistry } from "../../../lib/agentActions/registry";
import { TicketPullRequestPanel } from "../TicketPullRequestPanel";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const PR: PullRequestStatus = {
  number: 555,
  url: "https://github.com/o/r/pull/555",
  title: "feat(runs): detach agent processes",
  state: "open",
  is_draft: false,
  base: "main",
  head: "loregarden/lg-durable-remote-336",
  additions: 7522,
  deletions: 297,
  changed_files: 49,
  review: "not_required",
  has_conflicts: false,
  checks: [
    { name: "PR UX section", outcome: "passing", url: "https://ci/2" },
    { name: "Server (Python)", outcome: "failing", url: "https://ci/1" },
  ],
  body: "Detaches print-mode agent runs.",
  head_sha: "a".repeat(40),
  mergeable_now: false,
  unsigned_commits: [],
};

const READY: PullRequestStatus = {
  ...PR,
  checks: PR.checks.map((check) => ({ ...check, outcome: "passing" as const })),
  mergeable_now: true,
};

function lookup(overrides: Partial<TicketPullRequest>): TicketPullRequest {
  return { lookup: "found", branch: PR.head, pull_request: PR, error: "", recorded: null, ships_with: "", ...overrides };
}

function renderPanel(data: TicketPullRequest, handlers: { onOpenPr?: () => void; onCommitPush?: () => void } = {}) {
  mockApi.ticketPullRequest.mockResolvedValue(data);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TicketPullRequestPanel ticketId="t1" isOpeningPr={false} isCommittingPush={false} {...handlers} />
    </QueryClientProvider>,
  );
}

describe("TicketPullRequestPanel", () => {
  beforeEach(() => jest.clearAllMocks());

  it("shows a PR GitHub has even when Loregarden never recorded one, failing checks first", async () => {
    renderPanel(lookup({}));

    expect(await screen.findByRole("link", { name: PR.title })).toHaveAttribute("href", PR.url);
    expect(screen.getByRole("status")).toHaveTextContent("Server (Python) is failing.");
    const checks = screen.getAllByRole("listitem");
    expect(checks[0]).toHaveTextContent("Server (Python)");
    expect(screen.getByRole("link", { name: "Server (Python)" })).toHaveAttribute("href", "https://ci/1");
    expect(screen.getByRole("link", { name: "Open on GitHub" })).toHaveAttribute("href", PR.url);
  });

  it("hands failing checks to the ticket's triage chat", async () => {
    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    renderPanel(lookup({}));

    fireEvent.click(await screen.findByRole("button", { name: "Ask triage to fix" }));

    await waitFor(() =>
      expect(mockApi.sendTriageMessage).toHaveBeenCalledWith("t1", expect.stringContaining("Server (Python): https://ci/1")),
    );
  });

  it("offers no fix for a merged PR and hides its checks", async () => {
    renderPanel(lookup({ pull_request: { ...PR, state: "merged" } }));

    expect(await screen.findByText("Merged into main.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask triage to fix" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Checks" })).not.toBeInTheDocument();
  });

  it("names the branch and offers to open a PR when GitHub has none", async () => {
    const onOpenPr = jest.fn();
    renderPanel(lookup({ lookup: "none", pull_request: null }), { onOpenPr, onCommitPush: jest.fn() });

    expect(await screen.findByText(PR.head)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open PR" }));
    expect(onOpenPr).toHaveBeenCalled();
  });

  it("offers no PR of its own to a ticket that ships with its tree", async () => {
    renderPanel(lookup({ lookup: "none", pull_request: null, ships_with: "integration/lg-ms-1" }), {
      onOpenPr: jest.fn(),
      onCommitPush: jest.fn(),
    });

    expect(await screen.findByText("integration/lg-ms-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open PR" })).not.toBeInTheDocument();
  });

  it("asks before merging, merges at the head on screen, and lists the cleanup", async () => {
    mockApi.mergeTicketPullRequest.mockResolvedValue({
      number: 555,
      merge_commit: "b".repeat(40),
      steps: [
        { step: "local branch", ok: true, detail: "deleted" },
        { step: "worktree /w/336", ok: false, detail: "kept: it has uncommitted changes" },
      ],
    });
    renderPanel(lookup({ pull_request: READY }));

    fireEvent.click(await screen.findByRole("button", { name: "Merge and clean up" }));
    expect(mockApi.mergeTicketPullRequest).not.toHaveBeenCalled();
    const confirm = screen.getByRole("button", { name: "Merge #555" });
    expect(confirm).toHaveFocus();
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(mockApi.mergeTicketPullRequest).toHaveBeenCalledWith("t1", { number: 555, head_sha: "a".repeat(40) }),
    );
    expect(await screen.findByText(/1 cleanup step need you/)).toBeInTheDocument();
    expect(screen.getByText("kept: it has uncommitted changes")).toBeInTheDocument();
  });

  it("offers agents the same merge, only while the button is offered", async () => {
    mockApi.mergeTicketPullRequest.mockResolvedValue({ number: 555, merge_commit: "", steps: [] });
    renderPanel(lookup({ pull_request: READY }));
    await screen.findByRole("button", { name: "Merge and clean up" });
    expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request");

    await expect(
      uiActionRegistry.run("ticket.merge_pull_request", { ticket_id: "other", number: 555, head_sha: "c".repeat(40) }),
    ).rejects.toThrow("open the ticket first");
    expect(mockApi.mergeTicketPullRequest).not.toHaveBeenCalled();

    await uiActionRegistry.run("ticket.merge_pull_request", { ticket_id: "t1", number: 555, head_sha: "c".repeat(40) });
    expect(mockApi.mergeTicketPullRequest).toHaveBeenCalledWith("t1", { number: 555, head_sha: "c".repeat(40) });
    await waitFor(() => expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request"));
  });

  it("does not offer the merge to agents while GitHub blocks it", async () => {
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false } }));
    await screen.findByText(/still block the merge/);
    expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request");
  });

  it("cancels the merge with Escape", async () => {
    renderPanel(lookup({ pull_request: READY }));
    fireEvent.click(await screen.findByRole("button", { name: "Merge and clean up" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Merge #555" }), { key: "Escape" });
    expect(screen.getByRole("button", { name: "Merge and clean up" })).toBeInTheDocument();
    expect(mockApi.mergeTicketPullRequest).not.toHaveBeenCalled();
  });

  it("offers no merge, and says so, while GitHub's rules still block it", async () => {
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false } }));
    expect(await screen.findByText(/rules for main still block the merge/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument();
  });

  it("names the unsigned commits when they are what blocks the merge", async () => {
    renderPanel(lookup({ pull_request: { ...READY, mergeable_now: false, unsigned_commits: ["20a44d8c test: x"] } }));
    expect(await screen.findByText(/1 commit is unsigned, and main requires signed commits/)).toBeInTheDocument();
    expect(screen.getByText("20a44d8c test: x")).toBeInTheDocument();
  });

  it("does not call a failed lookup 'no pull request', and links the recorded one", async () => {
    renderPanel(
      lookup({
        lookup: "failed",
        pull_request: null,
        error: "HTTP 401: Bad credentials",
        recorded: { url: "https://github.com/o/r/pull/9", number: "9", title: "older" },
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Bad credentials");
    expect(screen.queryByText(/No pull request/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#9 older" })).toHaveAttribute("href", "https://github.com/o/r/pull/9");
  });
});
