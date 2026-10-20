import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import type { TicketState, TicketTreeNode } from "../../api/types";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import { WorkItemsPage } from "../WorkItemsPage";

jest.mock("../../api/client");
const mockApi = api as jest.Mocked<typeof api>;

function node(id: string, overrides: Partial<TicketTreeNode> = {}): TicketTreeNode {
  return {
    id,
    external_id: `lg-${id}`,
    title: `Ticket ${id}`,
    state: "backlog",
    priority: 2,
    work_item_type: "feature",
    workspace_slug: "loregarden",
    workflow_stage_name: "",
    workflow_stage_status: "pending",
    child_count: 0,
    children: [],
    ...overrides,
  };
}

/**
 * The live database's shape on 2026-10-19 (read-only): 1,665 tickets under 78
 * roots; 1,121 backlog, 70 in progress, 15 blocked, 446 done, 13 won't do; 15
 * stages awaiting a person. Deterministic, so a count asserted here is exact.
 */
function prodShapedTree(): TicketTreeNode[] {
  const states: [TicketState, number][] = [
    ["backlog", 1121],
    ["in_progress", 70],
    ["blocked", 15],
    ["done", 446],
    ["wont_do", 13],
  ];
  const workspaces = ["loregarden", "blobert", "lore-eden", "tinkercg", "loremaker"];
  // Live titles are all distinct; numbered copies of one sentence would be
  // a wall of repeated text the real page never draws.
  const verbs = ["Fix", "Add", "Split", "Cache", "Retry", "Surface", "Gate", "Index", "Stream", "Reap", "Migrate"];
  const things = ["the run ledger", "queue estimates", "memory search", "the hook installer", "branch triage", "stage reports", "approval routing", "the capacity ledger", "worktree cleanup", "CI status", "ticket import", "agent handoffs", "the diff view"];
  const why = ["under load", "for blocked runs", "across workspaces", "on restart", "when the gate throws", "for long titles", "at phone width", "after a squash merge", "before the push", "without a terminal", "in the sandbox", "for member tickets", "on a stale checkout"];
  const leaves: TicketTreeNode[] = [];
  let i = 0;
  let awaiting = 0;
  for (const [state, count] of states) {
    for (let k = 0; k < count - (state === "backlog" ? 78 : 0); k += 1, i += 1) {
      const isAwaiting = state === "in_progress" && awaiting < 15;
      if (isAwaiting) awaiting += 1;
      leaves.push(
        node(`t${i}`, {
          title: `${verbs[i % verbs.length]} ${things[i % things.length]} ${why[Math.floor(i / 143) % why.length]}`,
          state,
          priority: (i % 3) + 1,
          workspace_slug: workspaces[i % workspaces.length],
          workflow_stage_name: state === "in_progress" ? "Implement" : "",
          workflow_stage_status: isAwaiting ? "awaiting" : state === "in_progress" ? "running" : "pending",
        }),
      );
    }
  }
  return Array.from({ length: 78 }, (_, r) => {
    const children = leaves.filter((_, idx) => idx % 78 === r);
    return node(`m${r}`, {
      title: `Milestone ${r}`,
      work_item_type: "milestone",
      children,
      child_count: children.length,
    });
  });
}

function renderPage(path = "/work-items") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>
        <WorkItemsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function bodyRows() {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

describe("WorkItemsPage", () => {
  beforeEach(() => {
    mockApi.ticketTree.mockReset();
  });

  it("opens on the tickets that need the operator, each with its reason", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    renderPage();

    await screen.findByRole("table");
    expect(screen.getByRole("button", { name: /Needs me/ })).toHaveAttribute("aria-pressed", "true");
    expect(bodyRows()).toHaveLength(15);
    expect(screen.getByRole("columnheader", { name: "Why it needs you" })).toBeInTheDocument();
    expect(within(bodyRows()[0]).getByText("Your approval or answer")).toBeInTheDocument();
  });

  it("links each title to the ticket in the Console", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    renderPage();

    const link = within((await screen.findAllByRole("row"))[1]).getAllByRole("link")[0];
    expect(link.getAttribute("href")).toMatch(/^\/tickets\/t\d+\/timeline$/);
  });

  it("draws a page of open tickets at a time, not all 1,206", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    renderPage("/work-items?view=open");

    await screen.findByRole("table");
    expect(bodyRows()).toHaveLength(200);
    fireEvent.click(screen.getByRole("button", { name: /Show 200 more of 1006 remaining/ }));
    expect(bodyRows()).toHaveLength(400);
  });

  it("starts again at one page when the sort changes", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    renderPage("/work-items?view=open");
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: /Show 200 more/ }));
    fireEvent.click(within(screen.getByRole("columnheader", { name: /Title/ })).getByRole("button"));
    expect(bodyRows()).toHaveLength(200);
  });

  it("is usable at production volume", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    const { container } = renderPage("/work-items?view=open");

    await screen.findByRole("table");
    expect(findUsabilityProblems(container)).toEqual([]);
  });

  it("sorts by a column and says so to assistive tech", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    renderPage("/work-items?view=open");
    await screen.findByRole("table");

    const header = screen.getByRole("columnheader", { name: /Workspace/ });
    fireEvent.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(within(bodyRows()[0]).getByText("blobert")).toBeInTheDocument();
  });

  it("narrows by search and offers a way back when nothing matches", async () => {
    mockApi.ticketTree.mockResolvedValue(prodShapedTree());
    renderPage("/work-items?view=all");
    await screen.findByRole("table");

    fireEvent.change(screen.getByRole("searchbox", { name: "Search work items" }), {
      target: { value: "no such ticket" },
    });
    expect(screen.getByText("No work items match this search.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("says nothing needs the operator, distinctly from an error, and points at open work", async () => {
    mockApi.ticketTree.mockResolvedValue([node("a"), node("b", { state: "done" })]);
    renderPage();

    expect(await screen.findByText(/Nothing needs you right now/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show open tickets (1)" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("reports a failed load with a retry", async () => {
    mockApi.ticketTree.mockRejectedValueOnce(new Error("server unavailable"));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("server unavailable");
    mockApi.ticketTree.mockResolvedValue([node("a", { workflow_stage_status: "awaiting" })]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("control: the same rows as a list of five-field items are flagged", () => {
    const { container } = render(
      <ul>
        {Array.from({ length: 12 }, (_, i) => (
          <li key={i}>
            <span>lg-{i}</span>
            <span>Title {i}</span>
            <span>Backlog</span>
            <span>Implement · pending</span>
            <span>loregarden</span>
          </li>
        ))}
      </ul>,
    );
    expect(findUsabilityProblems(container).map((p) => p.kind)).toContain("tabular-list");
  });
});
