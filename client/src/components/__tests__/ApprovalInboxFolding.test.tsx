import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";

import { api, type Approval } from "../../api/client";
import { useNotificationStore } from "../../state/notificationStore";
import { useUiStore } from "../../state/uiStore";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalInboxPanel } from "../ApprovalInboxPanel";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function approval(id: string, workspaceSlug: string): Approval {
  return {
    id,
    title: id,
    level: "medium",
    workspace_slug: workspaceSlug,
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
  };
}

function renderInbox() {
  return renderWithRouter(<ApprovalInboxPanel />, {
    queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  });
}

const toggle = (name: string) => screen.getByRole("button", { name });

describe("ApprovalInboxPanel folding", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useNotificationStore.getState().clear();
    useUiStore.setState({ inboxOpen: true, inboxCollapsed: [] });
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm", "blobert"),
      approval("lore-perm", "loregarden"),
    ]);
  });

  afterEach(() => {
    act(() => useUiStore.setState({ inboxOpen: false, inboxCollapsed: [] }));
  });

  it("folds one workspace's approvals and leaves the other's, with the count still showing", async () => {
    renderInbox();
    await screen.findByText("blob-perm");

    fireEvent.click(toggle("blobert"));

    expect(toggle("blobert")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("blob-perm")).not.toBeVisible();
    expect(screen.getByText("lore-perm")).toBeVisible();
    expect(
      screen.getByRole("region", { name: "blobert — 1 pending approval" }).querySelector(".count-pill"),
    ).toBeVisible();
  });

  it("folds the approvals section with every workspace in it, and opens it as it was", async () => {
    renderInbox();
    await screen.findByText("blob-perm");
    fireEvent.click(toggle("blobert"));

    fireEvent.click(toggle("Approvals"));
    expect(screen.getByText("lore-perm")).not.toBeVisible();

    fireEvent.click(toggle("Approvals"));
    expect(screen.getByText("lore-perm")).toBeVisible();
    expect(screen.getByText("blob-perm")).not.toBeVisible();
  });

  it("keeps the folded card mounted, so a half-typed answer survives", async () => {
    renderInbox();
    const card = await screen.findByText("blob-perm");

    fireEvent.click(toggle("blobert"));
    fireEvent.click(toggle("blobert"));

    expect(screen.getByText("blob-perm")).toBe(card);
  });

  it("remembers the folded sections when the drawer closes and opens", async () => {
    renderInbox();
    await screen.findByText("blob-perm");
    fireEvent.click(toggle("blobert"));

    act(() => useUiStore.getState().setInboxOpen(false));
    act(() => useUiStore.getState().setInboxOpen(true));

    await waitFor(() => expect(toggle("blobert")).toHaveAttribute("aria-expanded", "false"));
    expect(JSON.parse(localStorage.getItem("loregarden-ui") ?? "{}").state.inboxCollapsed).toEqual([
      "approvals/blobert",
    ]);
  });

  it("clears notifications from a folded section without opening it", async () => {
    useNotificationStore.setState({
      notifications: [
        { id: "n1", tone: "error", title: "Run failed", message: "", createdAt: "2026-11-23T00:00:00Z" },
      ],
    } as Partial<ReturnType<typeof useNotificationStore.getState>>);
    renderInbox();
    await screen.findByText("blob-perm");

    fireEvent.click(toggle("Notifications"));
    expect(screen.getByText("Run failed")).not.toBeVisible();
    fireEvent.click(toggle("Clear all"));

    expect(useNotificationStore.getState().notifications).toEqual([]);
    expect(toggle("Notifications")).toHaveAttribute("aria-expanded", "false");
  });

  it("says a failed load in the folded heading, where the count alone would read as nothing waiting", async () => {
    useUiStore.setState({ inboxCollapsed: ["approvals"] });
    mockApi.approvals.mockRejectedValue(new Error("down"));
    renderInbox();

    expect(await screen.findByText("Could not load")).toBeVisible();
  });
});
