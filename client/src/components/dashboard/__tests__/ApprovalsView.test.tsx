import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { api, type Approval, type TicketDetail } from "../../../api/client";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { ApprovalsView } from "../ApprovalsView";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "appr_1",
    title: "Approve Dash movement and cooldown",
    level: "medium",
    workspace_slug: "blobert-tdd",
    stage_key: "playtest",
    stage_name: "Playtest",
    impact: "Stage 'Playtest' requires human sign-off before completion.",
    checklist: ["Dash cancels on wall contact"],
    route_options: [],
    ticket_id: "ticket_1",
    ticket_external_id: "01-blobert-dash",
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
  external_id: "01-blobert-dash",
  title: "Dash movement",
  acceptance_criteria: ["Dash has a cooldown", "Dash cancels on wall contact"],
} as unknown as TicketDetail;

function renderView(ticket: TicketDetail | undefined = TICKET) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithRouter(<ApprovalsView ticket={ticket} />, { queryClient: client });
}

describe("ApprovalsView", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.approvals.mockResolvedValue([]);
  });

  it("lists the ticket's acceptance criteria", async () => {
    renderView();

    expect(await screen.findByText("Dash has a cooldown")).toBeInTheDocument();
    expect(screen.getByText("Dash cancels on wall contact")).toBeInTheDocument();
  });

  it("shows each criterion once when the gate brief restates them", async () => {
    mockApi.approvals.mockResolvedValue([
      approval({
        // The checklist repeats a criterion too — this test is about the brief.
        checklist: [],
        impact: [
          "Stage 'Playtest' requires human sign-off before completion.",
          "Acceptance criteria:",
          "- Dash has a cooldown",
          "- Dash cancels on wall contact",
        ].join("\n"),
      }),
    ]);
    renderView();

    expect(await screen.findByText("Dash has a cooldown")).toBeInTheDocument();
    expect(screen.getAllByText(/Dash has a cooldown/)).toHaveLength(1);
    expect(screen.getAllByText(/Dash cancels on wall contact/)).toHaveLength(1);
  });

  it("drops the criteria list when the checklist already walks them, keeping the items whole", async () => {
    mockApi.approvals.mockResolvedValue([
      approval({
        impact: "Sign-off needed.",
        checklist: [
          "Play-test by hand — Dash has a cooldown",
          "Play-test by hand — Dash cancels on wall contact",
          "Confirm no console errors appear during play",
        ],
      }),
    ]);
    renderView();

    // The checklist item keeps the criterion in full — it is what you test from.
    expect(await screen.findByText("Play-test by hand — Dash has a cooldown")).toBeInTheDocument();
    expect(screen.getAllByText(/Dash has a cooldown/)).toHaveLength(1);
    expect(screen.queryByText("Acceptance criteria")).toBeNull();
  });

  it("keeps the criteria list when the checklist covers only some of them", async () => {
    mockApi.approvals.mockResolvedValue([
      approval({
        impact: "Sign-off needed.",
        checklist: ["Play-test by hand — Dash has a cooldown"],
      }),
    ]);
    renderView();

    expect(await screen.findByText("Acceptance criteria")).toBeInTheDocument();
    expect(screen.getByText("Dash cancels on wall contact")).toBeInTheDocument();
  });

  it("keeps the brief's criteria when the ticket records none", async () => {
    mockApi.approvals.mockResolvedValue([
      approval({ impact: "Sign-off needed.\nAcceptance criteria:\n- Dash has a cooldown" }),
    ]);
    renderView({ ...TICKET, acceptance_criteria: [] } as TicketDetail);

    expect(await screen.findByText(/Dash has a cooldown/)).toBeInTheDocument();
  });

  it("separates human sign-offs from tool permissions", async () => {
    mockApi.approvals.mockResolvedValue([
      approval(),
      approval({ id: "appr_2", kind: "cli_permission", tool_name: "Bash", checklist: [] }),
    ]);
    renderView();

    expect(await screen.findByText("Awaiting your sign-off (1)")).toBeInTheDocument();
    expect(screen.getByText("Other pending approvals (1)")).toBeInTheDocument();
  });

  it("resolves an approval through the inbox endpoint", async () => {
    mockApi.approvals.mockResolvedValue([approval()]);
    mockApi.resolveApproval.mockResolvedValue({ id: "appr_1", status: "approved" });
    renderView();

    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(mockApi.resolveApproval).toHaveBeenCalledWith("appr_1", { action: "approve" }),
    );
  });

  it("scopes the fetch to the open ticket", async () => {
    renderView();

    await waitFor(() => expect(mockApi.approvals).toHaveBeenCalledWith("ticket_1"));
  });
});
