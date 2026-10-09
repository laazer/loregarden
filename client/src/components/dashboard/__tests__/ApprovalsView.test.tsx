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
  state: "in_progress",
  stages: [],
} as unknown as TicketDetail;

function renderView(ticket: TicketDetail | undefined = TICKET) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithRouter(<ApprovalsView ticket={ticket} />, { queryClient: client });
}

describe("ApprovalsView", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.approvals.mockResolvedValue([]);
    mockApi.approvalHistory.mockResolvedValue([]);
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

  it("folds the criteria behind their count", async () => {
    mockApi.approvals.mockResolvedValue([approval({ checklist: [] })]);
    const { container } = renderView();

    await screen.findByRole("button", { name: "Approve" });
    const criteria = container.querySelector("details.approvals-view-criteria-block");
    expect(criteria).not.toBeNull();
    expect((criteria as HTMLDetailsElement).open).toBe(false);
  });

  it("says nothing needs you, and where the ticket is, when nothing is pending", async () => {
    renderView({
      ...TICKET,
      state: "in_progress",
      stages: [
        { key: "plan", name: "Plan", status: "done" },
        { key: "implement", name: "Implement", status: "running" },
      ],
    } as unknown as TicketDetail);

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Nothing needs you right now");
    expect(status).toHaveTextContent("Implement is running.");
    expect(screen.queryByText(/Awaiting your sign-off/)).toBeNull();
  });

  it("lists past decisions, grouping repeats and linking each to the Timeline", async () => {
    const decided = (id: string, title: string, overrides = {}) => ({
      id,
      title,
      kind: "cli_permission" as const,
      stage_key: "implement",
      stage_name: "Implement",
      status: "approved" as const,
      resolved_by: "",
      resolved_at: "2026-10-01T10:00:00Z",
      ticket_id: "ticket_1",
      ticket_external_id: "01-blobert-dash",
      ...overrides,
    });
    mockApi.approvalHistory.mockResolvedValue([
      decided("h1", "Allow Bash"),
      decided("h2", "Approve Plan completion", { kind: "workflow_gate", resolved_by: "automation" }),
      decided("h3", "Allow Bash"),
      decided("h4", "Allow Bash"),
    ]);
    renderView();

    const rows = await screen.findAllByRole("button", { name: /Allow Bash|Approve Plan completion/ });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Allow Bash");
    expect(rows[0]).toHaveTextContent("×3");
    expect(rows[1]).toHaveTextContent("Auto-approved");
    expect(mockApi.approvalHistory).toHaveBeenCalledWith("ticket_1");
  });

  it("says when past decisions fail to load instead of showing none", async () => {
    mockApi.approvalHistory.mockRejectedValue(new Error("history down"));
    renderView();

    expect(await screen.findByText(/Could not load past decisions: history down/)).toBeInTheDocument();
    expect(screen.queryByText(/No approvals have been decided/)).toBeNull();
  });

  it("says the approvals failed to load instead of claiming nothing is waiting", async () => {
    mockApi.approvals.mockRejectedValue(new Error("boom"));
    renderView();

    expect(await screen.findByText(/Could not load this ticket.s approvals: boom/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing needs you/)).toBeNull();
  });

  it("opens a long checklist item in place without ticking it", async () => {
    const long = `Play it and judge whether it delivers — ${"x".repeat(300)}`;
    mockApi.approvals.mockResolvedValue([approval({ checklist: [long] })]);
    renderView();

    const toggle = await screen.findByRole("button", { name: "Show all" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);

    expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("checkbox", { name: /Play it and judge/ })).not.toBeChecked();
  });

  it("scopes the fetch to the open ticket", async () => {
    renderView();

    await waitFor(() => expect(mockApi.approvals).toHaveBeenCalledWith("ticket_1"));
  });
});
