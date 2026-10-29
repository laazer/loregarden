import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { api, type Approval, type TicketDetail } from "../../api/client";
import { renderWithRouter } from "../../test/renderWithRouter";
import { TicketDetailsModal } from "../TicketDetailsModal";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "appr_1",
    title: "Approve Plan completion",
    level: "medium",
    workspace_slug: "loregarden",
    stage_key: "plan",
    stage_name: "Plan",
    impact: "Stage 'Plan' requires human sign-off before completion.",
    checklist: [],
    route_options: [],
    ticket_id: "ticket_1",
    ticket_external_id: "lg-dash-1",
    kind: "workflow_gate",
    status: "pending",
    run_id: "",
    tool_name: "",
    tool_input_json: "{}",
    cli_adapter: "",
    ...overrides,
  };
}

const TICKET = {
  id: "ticket_1",
  external_id: "lg-dash-1",
  title: "Dash movement",
  description: "",
  acceptance_criteria: [],
  checked_acceptance_criteria: [],
  tags: [],
  state: "in_progress",
  priority: 2,
  stages: [],
} as unknown as TicketDetail;

function renderModal(onClose = jest.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithRouter(<TicketDetailsModal ticket={TICKET} isOpen onClose={onClose} />, { queryClient: client });
  return onClose;
}

describe("TicketDetailsModal approvals", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("resolves a pending approval from inside the modal", async () => {
    mockApi.approvals.mockResolvedValue([approval()]);
    mockApi.resolveApproval.mockResolvedValue({ id: "appr_1", status: "approved" });
    renderModal();

    expect(await screen.findByRole("region", { name: "Pending approvals" })).toBeInTheDocument();
    expect(mockApi.approvals).toHaveBeenCalledWith("ticket_1");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(mockApi.resolveApproval).toHaveBeenCalledWith("appr_1", { action: "approve" }));
  });

  it("shows no approvals section when nothing is pending", async () => {
    mockApi.approvals.mockResolvedValue([]);
    renderModal();

    await waitFor(() => expect(mockApi.approvals).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Pending approvals" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says the approvals failed to load instead of showing nothing", async () => {
    mockApi.approvals.mockRejectedValue(new Error("server down"));
    renderModal();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not load this ticket.s approvals: server down/);
  });

  it("Escape in the Reject dialog closes only that dialog, not the ticket", async () => {
    mockApi.approvals.mockResolvedValue([approval()]);
    const onClose = renderModal();

    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const rejectDialog = (await screen.findAllByRole("dialog")).find((d) => d !== screen.getByTestId("modal-content"));
    expect(rejectDialog).toBeDefined();

    fireEvent.keyDown(rejectDialog!, { key: "Escape" });

    await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(1));
    expect(onClose).not.toHaveBeenCalled();
  });
});
