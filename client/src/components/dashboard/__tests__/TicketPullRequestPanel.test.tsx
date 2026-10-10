import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import type { PullRequestStatus, TicketPullRequest } from "../../../api/ticketPullRequestApi";
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
