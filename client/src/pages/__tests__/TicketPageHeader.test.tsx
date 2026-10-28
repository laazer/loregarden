import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import * as apiClient from "../../api/client";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import { useUiStore } from "../../state/uiStore";
import { TicketPage } from "../TicketPage";

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const TICKET_ID = "00000000-0000-4000-8000-000000000001";
const PARENT_ID = "00000000-0000-4000-8000-000000000002";

function mockTicket(overrides: Partial<apiClient.TicketDetail> = {}): apiClient.TicketDetail {
  return {
    id: TICKET_ID,
    external_id: "lg-modal-feature-16",
    title: "Modal Feature",
    description: "This is a test feature",
    acceptance_criteria: [],
    checked_acceptance_criteria: [],
    state: "in_progress",
    priority: 1,
    workspace_slug: "loregarden",
    workflow_stage_key: "test_design",
    workflow_stage_status: "pending",
    workflow_stage_name: "Test Design",
    run_code: "",
    work_item_type: "feature",
    parent_ticket_id: null,
    milestone: "",
    branch: "main",
    workflow_template_slug: "default",
    workflow_template_name: "Default",
    child_count: 0,
    revision: 1,
    last_updated_by: "",
    current_stage_agent: "",
    next_status: "",
    blocking_issues: "",
    state_locked: false,
    stages: [],
    artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
    ...overrides,
  };
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <RouterBridgeSync />
        <Routes>
          <Route path="/console" element={<div>Console page</div>} />
          <Route path="/tickets/:ticketId/:artifactTab" element={<TicketPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TicketPage header", () => {
  beforeEach(() => {
    useUiStore.setState({ expandedTicketIds: [] });
    jest.clearAllMocks();
    jest.mocked(apiClient.api.workspaces).mockResolvedValue([]);
    jest.mocked(apiClient.api.ticketTree).mockResolvedValue([]);
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
  });

  it("names the ticket, leads back to the Console and its parent, and copies its id", async () => {
    jest.mocked(apiClient.api.ticket).mockImplementation(async (id: string) =>
      id === PARENT_ID
        ? mockTicket({ id: PARENT_ID, title: "Modal milestone", work_item_type: "milestone" })
        : mockTicket({ parent_ticket_id: PARENT_ID }),
    );

    renderAt(`/tickets/${TICKET_ID}/timeline`);

    expect(await screen.findByRole("heading", { level: 1, name: "Modal Feature" })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Console" })).toHaveAttribute("href", "/console");
    expect(await within(crumbs).findByRole("link", { name: "Modal milestone" })).toHaveAttribute(
      "href",
      `/tickets/${PARENT_ID}/timeline`,
    );
    expect(within(crumbs).getByText("lg-modal-feature-16")).toBeInTheDocument();
  });

  it("switches between the Ticket, Diff and Hive views, and Diff fills the page", async () => {
    jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket());

    renderAt(`/tickets/${TICKET_ID}/timeline`);

    const views = await screen.findByRole("tablist", { name: "Ticket page views" });
    expect(within(views).getByRole("tab", { name: "Ticket" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("complementary", { name: "Workflow" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Ticket views" })).toBeInTheDocument();

    fireEvent.click(within(views).getByRole("tab", { name: "Diff" }));

    await waitFor(() => expect(within(views).getByRole("tab", { name: "Diff" })).toHaveAttribute("aria-selected", "true"));
    expect(screen.queryByRole("complementary", { name: "Workflow" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "Ticket views" })).not.toBeInTheDocument();
  });

  it("keeps Diff and Hive out of the reading tabs below", async () => {
    jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket());

    renderAt(`/tickets/${TICKET_ID}/outputs`);

    const reading = await screen.findByRole("tablist", { name: "Ticket views" });
    const names = within(reading).getAllByRole("tab").map((tab) => tab.textContent);
    expect(names).toEqual(["Timeline", "Outputs", "PR", "Approvals"]);
  });

  it("opens the ticket's details from the header", async () => {
    jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket());

    renderAt(`/tickets/${TICKET_ID}/timeline`);

    fireEvent.click(await screen.findByRole("button", { name: /view ticket details/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Modal Feature" })).toBeInTheDocument();
    expect(within(dialog).getByText("This is a test feature")).toBeInTheDocument();
  });

  it("says what failed when the ticket cannot load, and retries", async () => {
    jest.mocked(apiClient.api.ticket).mockRejectedValueOnce(new Error("Network error"));

    renderAt(`/tickets/${TICKET_ID}/timeline`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Network error");
    expect(within(alert).getByRole("link", { name: "Back to the Console" })).toHaveAttribute("href", "/console");

    jest.mocked(apiClient.api.ticket).mockResolvedValue(mockTicket());
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Modal Feature" })).toBeInTheDocument();
  });
});
