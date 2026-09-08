import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";

import { api, type Approval } from "../../api/client";
import { useUiStore } from "../../state/uiStore";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalInboxPanel } from "../ApprovalInboxPanel";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function approval(id: string, overrides: Partial<Approval> = {}): Approval {
  return {
    id,
    title: id,
    level: "medium",
    workspace_slug: "loregarden",
    stage_key: "implement",
    stage_name: "Implement",
    impact: "",
    checklist: [],
    route_options: [],
    ticket_id: "ticket_1",
    ticket_external_id: "lg-1",
    kind: "cli_permission",
    status: "pending",
    run_id: "",
    tool_name: "Bash",
    tool_input_json: "{}",
    cli_adapter: "",
    ...overrides,
  };
}

/**
 * lg-workflow-integrity-107. The rail rendered whatever order the API returned,
 * so a stage sign-off could sit below a stack of permission prompts — and the
 * inbox data says which of those is worth the reader's attention: permission
 * prompts are rejected 0.4% of the time, gates 13.3%.
 */
describe("ApprovalInboxPanel ordering", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useUiStore.getState().setInboxOpen(true);
  });

  afterEach(() => {
    act(() => useUiStore.getState().setInboxOpen(false));
  });

  it("puts a stage sign-off above permission prompts that arrived first", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("perm-1"),
      approval("perm-2"),
      approval("gate-1", { kind: "workflow_gate", stage_key: "gate", stage_name: "Gate" }),
    ]);

    renderWithRouter(<ApprovalInboxPanel />, {
      queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    });

    await waitFor(() => expect(screen.getByText("gate-1")).toBeInTheDocument());
    const rendered = screen.getAllByText(/^(perm-1|perm-2|gate-1)$/).map((n) => n.textContent);
    expect(rendered[0]).toBe("gate-1");
  });

  it("keeps the API's order within a group, so it is a promotion and not a reshuffle", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("perm-1"),
      approval("perm-2"),
      approval("perm-3"),
    ]);

    renderWithRouter(<ApprovalInboxPanel />, {
      queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    });

    await waitFor(() => expect(screen.getByText("perm-1")).toBeInTheDocument());
    const rendered = screen.getAllByText(/^perm-\d$/).map((n) => n.textContent);
    expect(rendered).toEqual(["perm-1", "perm-2", "perm-3"]);
  });
});
