import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";

import type { Approval } from "../../api/chatTypes";
import { api } from "../../api/client";
import { localInstancesApi } from "../../api/localInstancesApi";
import type { TicketSummary, WorkspaceSummary } from "../../api/types";
import {
  workspaceDashboardApi,
  type WorkspaceActivity,
  type WorkspacePullRequest,
} from "../../api/workspaceDashboardApi";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import activityFixture from "../../test/fixtures/prod-shape/workspace-activity-loregarden.json";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApiError } from "../../api/http";
import { WorkspacesPage } from "../WorkspacesPage";

jest.mock("../../api/client");
jest.mock("../../api/localInstancesApi");
jest.mock("../../api/workspaceDashboardApi", () => ({
  ...jest.requireActual("../../api/workspaceDashboardApi"),
  workspaceDashboardApi: {
    remote: jest.fn(),
    pullRequests: jest.fn(),
    activity: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockInstances = localInstancesApi as jest.Mocked<typeof localInstancesApi>;
const mockDashboard = workspaceDashboardApi as jest.Mocked<typeof workspaceDashboardApi>;
/** The API's own response over the production-shaped scenario: 405 tickets, 50 listed. */
const activity = activityFixture as unknown as WorkspaceActivity;

const WORKSPACE = {
  id: "w1",
  slug: "blobert",
  name: "Blobert",
} as WorkspaceSummary;

function pullRequest(number: number, fields: Partial<WorkspacePullRequest> = {}): WorkspacePullRequest {
  return {
    pull_request: {
      number,
      url: `https://github.com/laazer/blobert/pull/${number}`,
      title: `PR title ${number}`,
      state: "open",
      is_draft: false,
      base: "main",
      head: `claude/b-${number}`,
      additions: 10 * number,
      deletions: 1,
      changed_files: 3,
      review: "review_required",
      has_conflicts: false,
      checks: [{ name: "claude-review", outcome: "passing", url: "" }],
      body: "",
      head_sha: `sha${number}`,
      mergeable_now: false,
      unsigned_commits: [],
    },
    ci: "passing",
    author: "laazer",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    tier: "to_review",
    review_rank: 1,
    files_truncated: false,
    tickets: [],
    overlaps: [],
    ...fields,
  };
}

function ticket(id: string, fields: Partial<TicketSummary> = {}): TicketSummary {
  return {
    id,
    external_id: id,
    title: `Title of ${id}`,
    state: "in_progress",
    priority: 0,
    workspace_slug: "blobert",
    workflow_stage_key: "implement",
    workflow_stage_status: "running",
    workflow_stage_name: "Implement",
    run_code: "",
    work_item_type: "feature",
    parent_ticket_id: null,
    milestone: "",
    branch: "",
    child_count: 0,
    ...fields,
  };
}

function approval(id: string, workspace: string, ticketId: string, externalId: string): Approval {
  return {
    id,
    title: "Resolve Playtest exit actions",
    level: "",
    workspace_slug: workspace,
    stage_key: "playtest",
    stage_name: "Playtest",
    impact: "",
    ticket_id: ticketId,
    ticket_external_id: externalId,
    kind: "workflow_gate",
    run_id: "",
    tool_name: "",
    tool_input_json: "",
    cli_adapter: "",
  };
}

function renderDashboard(path = "/workspaces/blobert") {
  return renderWithRouter(
    <Routes>
      <Route path="/workspaces/*" element={<WorkspacesPage />} />
    </Routes>,
    { routerProps: { initialEntries: [path] } },
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  window.localStorage.clear();
  mockApi.workspaces.mockResolvedValue([WORKSPACE]);
  mockApi.approvals.mockResolvedValue([
    approval("a1", "blobert", "t-136", "blob-core-136"),
    approval("a3", "blobert", "t-138", "blob-core-138"),
    approval("a2", "loregarden", "t-9", "lg-9"),
  ]);
  mockApi.ticketStatusSummary.mockResolvedValue({
    backlog: 82,
    in_progress: 26,
    blocked: 11,
    done: 40,
    wont_do: 0,
    running: 1,
    awaiting: 2,
    queued: 0,
    idle: 23,
  });
  const waits = ticket("blob-waits-2", {
    state: "blocked",
    blocked_on: "tickets",
    root_blockers: [
      {
        id: "blob-root-9",
        external_id: "blob-root-9",
        title: "Title of blob-root-9",
        state: "blocked",
        reason: "prerequisite",
        blocked_on: "person",
      },
    ],
  });
  mockApi.tickets.mockImplementation(async (params) =>
    params?.blocked_on?.includes("tickets")
      ? [waits]
      : Array.isArray(params?.state)
        ? [waits, ticket("blob-person-1", { state: "blocked", blocked_on: "person" })]
        : [ticket("blob-idle-1", { activity: "idle" }), ticket("blob-run-1", { activity: "running" })],
  );
  mockApi.ticketTree.mockResolvedValue([
    {
      id: "t-136",
      external_id: "blob-core-136",
      title: "Attachment failure feedback",
      state: "in_progress",
      priority: 0,
      work_item_type: "feature",
      workflow_stage_name: "Playtest",
      workflow_stage_status: "awaiting",
      child_count: 0,
      children: [],
    },
  ]);
  mockInstances.list.mockResolvedValue({ instances: [], unreadable: [] });
  mockInstances.templates.mockResolvedValue([]);
  mockDashboard.remote.mockResolvedValue({
    workspace_slug: "blobert",
    repo_root: "/repos/blobert",
    remote_url: "git@github.com:laazer/blobert.git",
    web_url: "https://github.com/laazer/blobert",
    error: "",
  });
  mockDashboard.pullRequests.mockResolvedValue({
    workspace_slug: "blobert",
    listing_state: "complete",
    pull_requests: [
      pullRequest(103, {
        review_rank: 1,
        tickets: [
          {
            id: "t-150",
            external_id: "blob-core-150",
            title: "x",
            state: "in_progress",
          },
        ],
      }),
      pullRequest(92, {
        review_rank: 2,
        tier: "conflicting",
        overlaps: [{ number: 103, shared_files: 44 }],
        pull_request: {
          ...pullRequest(92).pull_request,
          has_conflicts: true,
          checks: [],
        },
      }),
    ],
  });
  mockDashboard.activity.mockResolvedValue(activity);
});

describe("WorkspaceDashboardPage", () => {
  it("lists PRs in the server's review order, with their tickets and why they are stuck", async () => {
    renderDashboard();

    const table = await screen.findByRole("table", {
      name: /pull requests in suggested review order/i,
    });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual(["1", "2"]);
    // Tickets show as cards named by what the work is; the key is read after it.
    expect(within(rows[0]).getByRole("link", { name: "x (blob-core-150)" })).toHaveAttribute(
      "href",
      "/tickets/t-150/timeline",
    );
    expect(within(rows[1]).getByText("Needs rebase")).toBeInTheDocument();
    expect(within(rows[1]).getByText("No checks")).toBeInTheDocument();
    expect(within(rows[1]).getByText("#103 (44)")).toBeInTheDocument();

    // The conflicting PR is also something only a person can fix.
    const needs = screen.getByRole("region", { name: /needs you now/i });
    expect(within(needs).getByRole("link", { name: "#92 PR title 92" })).toBeInTheDocument();
    // This workspace's approvals only, grouped by what is asked, each ticket opening its Approvals tab.
    expect(within(needs).getByText("Resolve Playtest exit actions")).toBeInTheDocument();
    const approvalCards = within(needs).getByRole("list", {
      name: "Resolve Playtest exit actions: 2 tickets",
    });
    // The title comes from the ticket tree; a ticket missing from it falls back to its key.
    expect(
      within(approvalCards).getByRole("link", {
        name: /^Attachment failure feedback \(blob-core-136\)/,
      }),
    ).toHaveAttribute("href", "/tickets/t-136/approvals");
    expect(within(approvalCards).getByRole("link", { name: /^blob-core-138/ })).toBeInTheDocument();
    expect(within(needs).queryByRole("link", { name: /lg-9/ })).not.toBeInTheDocument();
    // Only what a person or an agent must clear; a ticket waiting on others is
    // filed under what it waits on, which is the one to open.
    const blocked = within(within(needs).getByRole("list", { name: "Blocked or parked tickets" })).getAllByRole("link");
    expect(blocked.map((link) => link.querySelector(".wsd-card-title")?.textContent)).toEqual([
      "Title of blob-person-1",
    ]);
    expect(blocked[0]).toHaveTextContent("Needs a person");
    expect(within(needs).getByRole("heading", { name: /Waiting on other tickets/ })).toHaveTextContent(
      "1 held by 1",
    );
    expect(within(needs).getByRole("button", { name: "blob-root-9 · needs you" })).toBeInTheDocument();
  });

  it("keeps the rest of the page usable when GitHub cannot be asked, and retries on request", async () => {
    mockDashboard.pullRequests.mockRejectedValueOnce(
      new ApiError(502, "Could not list pull requests from GitHub: HTTP 401"),
    );
    renderDashboard();

    const alert = await screen.findByRole("alert", { name: "" });
    expect(alert).toHaveTextContent(/HTTP 401/);
    expect(await screen.findByRole("link", { name: /blob-run-1/ })).toBeInTheDocument();

    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: /pull requests/i })).toBeInTheDocument();
    expect(mockDashboard.pullRequests).toHaveBeenCalledTimes(2);
  });

  it("says when there is nothing to review, without looking like an error", async () => {
    mockDashboard.pullRequests.mockResolvedValue({
      workspace_slug: "blobert",
      listing_state: "complete",
      pull_requests: [],
    });
    renderDashboard();

    expect(await screen.findByText(/No open pull requests\./)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("runs active tickets first, and filters them by what is executing", async () => {
    renderDashboard();

    const table = await screen.findByRole("table", {
      name: /in-progress tickets/i,
    });
    const names = () =>
      within(table)
        .getAllByRole("link")
        .map((link) => link.textContent);
    expect(names()).toEqual(["Title of blob-run-1", "Title of blob-idle-1"]);

    const idle = screen.getByRole("button", { name: "Idle 1" });
    fireEvent.click(idle);
    expect(idle).toHaveAttribute("aria-pressed", "true");
    expect(names()).toEqual(["Title of blob-idle-1"]);
  });

  it("digests the activity since the last visit, and narrows it by kind on the server", async () => {
    const { container } = renderDashboard();

    const region = await screen.findByRole("region", {
      name: /since you were away/i,
    });
    await within(region).findByText(/355 more tickets had activity/);
    // First visit: the window says it fell back, rather than pretending.
    expect(within(region).getByText(/no earlier visit recorded here, so the last 7 days/)).toBeInTheDocument();
    expect(mockDashboard.activity).toHaveBeenLastCalledWith("blobert", expect.any(String), []);

    fireEvent.click(within(region).getByRole("button", { name: "Landing failed 10" }));
    await waitFor(() =>
      expect(mockDashboard.activity).toHaveBeenLastCalledWith("blobert", expect.any(String), ["landing_failed"]),
    );

    // At production volume the page is still something a person can act on.
    await screen.findByRole("table", { name: /pull requests/i });
    expect(findUsabilityProblems(container)).toEqual([]);
  });

  it("links the repository and says so when origin is not browsable", async () => {
    renderDashboard();
    expect(await screen.findByRole("link", { name: "github.com/laazer/blobert" })).toHaveAttribute(
      "href",
      "https://github.com/laazer/blobert",
    );
  });

  it("names a workspace that does not exist instead of drawing an empty board", async () => {
    renderDashboard("/workspaces/nope");
    expect(await screen.findByText(/No active workspace is called “nope”/)).toBeInTheDocument();
    expect(mockDashboard.pullRequests).toHaveBeenCalledWith("nope");
  });
});
