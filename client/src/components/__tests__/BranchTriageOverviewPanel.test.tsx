import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { BranchTriageOverviewPanel } from "../BranchTriageOverviewPanel";
import { fetchBranchActivity, type BranchTriageEntry } from "../../lib/branchTriageApi";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../lib/branchTriageApi", () => ({
  ...jest.requireActual("../../lib/branchTriageApi"),
  fetchBranchActivity: jest.fn(),
}));

jest.mock("../../hooks/useBranchChatSession", () => ({
  useBranchChatSession: () => ({
    kind: "branch-triage",
    id: "demo#feature/x",
    messages: [],
    isBusy: false,
    isLoading: false,
    loadError: false,
    error: null,
    send: jest.fn(),
    snapshot: undefined,
    isFetching: false,
  }),
}));

jest.mock("../../api/client", () => ({
  api: { runtimeOptions: jest.fn().mockResolvedValue({}), setTriageRuntime: jest.fn(), setWorkspaceRuntime: jest.fn() },
}));

const mockActivity = fetchBranchActivity as jest.MockedFunction<typeof fetchBranchActivity>;

function entry(overrides: Partial<BranchTriageEntry> = {}): BranchTriageEntry {
  return {
    name: "feature/x",
    is_current: false,
    is_base: false,
    ahead: 2,
    behind: 0,
    dirty: false,
    upstream: "origin/feature/x",
    diff_options: [],
    worktrees: [{ path: "/repo", label: "repo", dirty: false, is_primary: true }],
    linked_tickets: [],
    last_commit: { date: "", message: "" },
    issues: [],
    pr: null,
    ...overrides,
  };
}

function renderPanel(props: Partial<Parameters<typeof BranchTriageOverviewPanel>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BranchTriageOverviewPanel
          workspaceSlug="demo"
          branch="feature/x"
          baseBranch="main"
          branchEntry={entry()}
          onReviewDiff={jest.fn()}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ copilotOpen: false });
  mockActivity.mockResolvedValue({ branch: "feature/x", upstream: null, commits: [] });
});

it("shows the branch's counts as stats", async () => {
  renderPanel();

  expect(await screen.findByText("Commits ahead")).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
  expect(screen.getByText("Clean")).toBeInTheDocument();
});

it("leads with the worst issue and keeps the rest", async () => {
  renderPanel({
    branchEntry: entry({
      dirty: true,
      issues: [
        { code: "no_ticket", severity: "low", message: "No work item linked to this branch" },
        { code: "dirty", severity: "high", message: "Uncommitted changes in a worktree" },
      ],
    }),
  });

  // High severity wins the headline regardless of array order.
  expect(
    await screen.findByRole("heading", { name: "Uncommitted changes in a worktree" }),
  ).toBeInTheDocument();
  expect(screen.getByText("No work item linked to this branch")).toBeInTheDocument();
});

it("separates pushed commits from local-only ones", async () => {
  mockActivity.mockResolvedValue({
    branch: "feature/x",
    upstream: "origin/feature/x",
    commits: [
      {
        sha: "a".repeat(40),
        short_sha: "aaaaaaa",
        date: new Date().toISOString(),
        author: "Test",
        message: "local work",
        pushed: false,
      },
      {
        sha: "b".repeat(40),
        short_sha: "bbbbbbb",
        date: new Date().toISOString(),
        author: "Test",
        message: "pushed work",
        pushed: true,
      },
    ],
  });

  renderPanel();

  expect(await screen.findByText("local work")).toBeInTheDocument();
  expect(screen.getByText(/local only/)).toBeInTheDocument();
  expect(screen.getByText(/pushed$/)).toBeInTheDocument();
});

it("opens the dock rather than hosting its own chat", async () => {
  renderPanel();

  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: /ask in chat/i }));
  expect(useUiStore.getState().copilotOpen).toBe(true);
});
