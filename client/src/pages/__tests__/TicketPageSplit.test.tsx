import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import * as apiClient from "../../api/client";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import { useUiStore } from "../../state/uiStore";
import { TicketPage } from "../TicketPage";

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const PARENT_ID = "00000000-0000-4000-8000-000000000101";
const PART_IDS = [
  "00000000-0000-4000-8000-000000000111",
  "00000000-0000-4000-8000-000000000112",
  "00000000-0000-4000-8000-000000000113",
  "00000000-0000-4000-8000-000000000114",
];

function mockTicket(overrides: Partial<apiClient.TicketDetail> = {}): apiClient.TicketDetail {
  return {
    id: PARENT_ID,
    external_id: "lg-verdict-outcome-1631",
    title: "A tree member's PR tab reports no pull request",
    description: "",
    acceptance_criteria: [],
    checked_acceptance_criteria: [],
    state: "in_progress",
    priority: 1,
    workspace_slug: "loregarden",
    workflow_stage_key: "spec",
    workflow_stage_status: "done",
    workflow_stage_name: "Specification",
    run_code: "",
    work_item_type: "bug",
    parent_ticket_id: null,
    milestone: "",
    branch: "main",
    workflow_template_slug: "default",
    workflow_template_name: "Loregarden TDD V3",
    child_count: 4,
    revision: 1,
    last_updated_by: "",
    current_stage_agent: "",
    next_status: "",
    blocking_issues: "",
    state_locked: false,
    stages: [
      {
        key: "spec",
        name: "Specification",
        status: "done",
        agent_id: "spec_writer",
        skill_name: "",
        optional: false,
        note: "",
        stage_type: "agent",
        agents: [],
      },
    ],
    split: {
      stage_key: "spec",
      split_at: "2026-10-28T19:23:00",
      over_limits: ["23,749-character description, over the 15,000 limit"],
    },
    artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
    ...overrides,
  };
}

function part(index: number, state: apiClient.TicketTreeNode["state"]): apiClient.TicketTreeNode {
  return {
    id: PART_IDS[index],
    external_id: `lg-verdict-outcome-${1643 + index}`,
    title: `Part ${index + 1} title`,
    state,
    priority: 1,
    work_item_type: "task",
    workflow_stage_name: "Triage",
    workflow_stage_status: "pending",
    child_count: 0,
    children: [],
  };
}

const PARTS = [part(0, "done"), part(1, "in_progress"), part(2, "backlog"), part(3, "backlog")];
const TREE: apiClient.TicketTreeNode[] = [
  {
    id: PARENT_ID,
    external_id: "lg-verdict-outcome-1631",
    title: "A tree member's PR tab reports no pull request",
    state: "in_progress",
    priority: 1,
    work_item_type: "bug",
    workflow_stage_name: "Specification",
    workflow_stage_status: "done",
    child_count: 4,
    was_split: true,
    children: PARTS,
  },
];

function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <RouterBridgeSync />
        <Location />
        <Routes>
          <Route path="/tickets/:ticketId/:artifactTab" element={<TicketPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("a ticket split into parts", () => {
  beforeEach(() => {
    useUiStore.setState({ expandedTicketIds: [] });
    jest.clearAllMocks();
    jest.mocked(apiClient.api.workspaces).mockResolvedValue([]);
    jest.mocked(apiClient.api.ticketTree).mockResolvedValue(TREE);
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
    jest.mocked(apiClient.api.ticket).mockImplementation(async (id: string) =>
      id === PARENT_ID
        ? mockTicket()
        : mockTicket({
            id,
            external_id: "lg-verdict-outcome-1644",
            title: "Part 2 title",
            work_item_type: "task",
            parent_ticket_id: PARENT_ID,
            child_count: 0,
            stages: [],
            split: null,
          }),
    );
  });

  it("says who split it, at which stage, why, and how far its parts are", async () => {
    renderAt(`/tickets/${PARENT_ID}/timeline`);

    const line = await screen.findByRole("group", { name: "Split into 4 parts" });
    expect(line).toHaveTextContent("by the orchestrator at Specification");
    expect(line).toHaveTextContent("23,749-character description, over the 15,000 limit.");
    expect(await within(line).findByText("1 of 4 done")).toBeInTheDocument();
    expect(screen.getByTitle("Split into parts")).toHaveTextContent("4 parts");
  });

  it("leads its column with the parts, each a link with its standing in words", async () => {
    renderAt(`/tickets/${PARENT_ID}/timeline`);

    const parts = await screen.findByRole("region", { name: "Parts" });
    const links = within(parts).getAllByRole("button");
    expect(links.map((b) => b.textContent)).toEqual([
      "Part 1 title, done",
      "Part 2 title, running",
      "Part 3 title, waiting",
      "Part 4 title, waiting",
    ]);
    expect(screen.queryByText("Child tickets")).not.toBeInTheDocument();
  });

  it("makes opening the running part its forward action", async () => {
    renderAt(`/tickets/${PARENT_ID}/timeline`);

    fireEvent.click(await screen.findByRole("button", { name: "Open running part" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/tickets/${PART_IDS[1]}/timeline`));
  });

  it("tells a part which ticket it belongs to and where it sits among its siblings", async () => {
    renderAt(`/tickets/${PART_IDS[1]}/timeline`);

    expect(await screen.findByText(/Part 2 of 4 of/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Previous part" })).toHaveAttribute("href", `/tickets/${PART_IDS[0]}/timeline`);
    expect(screen.getByRole("link", { name: "Next part" })).toHaveAttribute("href", `/tickets/${PART_IDS[2]}/timeline`);
  });

  it("keeps saying it was split when its parts fail to load, and offers a retry", async () => {
    jest.mocked(apiClient.api.ticketTree).mockRejectedValue(new Error("tree offline"));

    renderAt(`/tickets/${PARENT_ID}/timeline`);

    const line = await screen.findByRole("group", { name: "Split into 4 parts" });
    expect(await within(line).findByText(/Couldn.t load the parts/)).toBeInTheDocument();
    expect(within(line).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
