import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import { api, type Approval } from "../../api/client";
import { setRouterNavigate } from "../../lib/routerBridge";
import { useUiStore } from "../../state/uiStore";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalInboxPanel } from "../ApprovalInboxPanel";

jest.mock("../../api/client");

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
    checklist: [],
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

function renderInbox() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });
}

describe("ApprovalInboxPanel inspect target", () => {
  const navigate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    setRouterNavigate(navigate as never);
    useUiStore.getState().setInboxOpen(true);
  });

  afterEach(() => {
    setRouterNavigate(null);
    act(() => useUiStore.getState().setInboxOpen(false));
  });

  it.each([
    ["a human sign-off", approval()],
    [
      "a checklist-bearing permission",
      approval({ kind: "cli_permission", tool_name: "Bash", checklist: ["Play the level"] }),
    ],
    // These two used to land on the Diff tab, where the card is nowhere on screen.
    ["a plain permission", approval({ kind: "cli_permission", tool_name: "Bash" })],
    ["a human-action card", approval({ kind: "human_action", stage_key: "", stage_name: "" })],
  ])("sends %s to the Approvals tab", async (_label, card) => {
    mockApi.approvals.mockResolvedValue([card]);
    renderInbox();

    fireEvent.click(await screen.findByRole("button", { name: "Inspect" }));

    expect(navigate).toHaveBeenCalledWith("/tickets/ticket_1/approvals", { replace: false });
  });

  it("ignores a card with the chosen reason", async () => {
    mockApi.approvals.mockResolvedValue([approval({ kind: "human_action" })]);
    mockApi.resolveApproval.mockResolvedValue({ id: "appr_1", status: "ignored" });
    renderInbox();

    fireEvent.click(await screen.findByRole("button", { name: "Ignore" }));
    const dialog = await screen.findByRole("dialog", { name: /ignore this approval/i });
    const confirm = within(dialog).getByRole("button", { name: "Ignore" });
    expect(confirm).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Duplicate" }));
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(mockApi.resolveApproval).toHaveBeenCalledWith("appr_1", {
        action: "ignore",
        response: "Duplicate",
      }),
    );
  });

  it("opens the clamped card in a modal", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      configurable: true,
      get: () => 900,
    });
    try {
      mockApi.approvals.mockResolvedValue([approval()]);
      renderInbox();

      fireEvent.click(await screen.findByRole("button", { name: /show the full approval/i }));

      await waitFor(() =>
        expect(screen.getByRole("dialog", { name: /approval details/i })).toBeInTheDocument(),
      );
    } finally {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, "scrollHeight", descriptor);
    }
  });
});
