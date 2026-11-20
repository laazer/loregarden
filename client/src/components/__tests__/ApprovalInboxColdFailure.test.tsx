import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";

import { api, type Approval } from "../../api/client";
import { useNotificationStore } from "../../state/notificationStore";
import { useUiStore } from "../../state/uiStore";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalInboxPanel } from "../ApprovalInboxPanel";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const INBOX_ZERO = "Inbox zero — nothing needs your attention";

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

function newClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function errorLines(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".inbox-error-line")).map(
    (n) => n.textContent ?? "",
  );
}

/**
 * AC-13. The drawer's `empty` short-circuit used to be `approvalCount === 0 &&
 * notificationCount === 0`, and `usePendingApprovalCount` answered `0` for a
 * cold fetch failure as readily as for an inbox with nothing in it. So a server
 * that was down produced "Inbox zero — nothing needs your attention": a
 * confident false statement, on the one surface this ticket is named for, that
 * outlived the 9s toast and never mounted the list carrying AC-8's error line.
 *
 * The two branches are tested apart on purpose. One test that only asserts
 * "the error line is there" passes just as happily if the drawer stopped
 * saying Inbox zero *at all*, and the genuinely-empty case is the one that
 * notices.
 */
describe("ApprovalInboxPanel cold approvals failure", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    act(() => useNotificationStore.getState().clear());
    useUiStore.getState().setInboxOpen(true);
  });

  afterEach(() => {
    act(() => useUiStore.getState().setInboxOpen(false));
    act(() => useNotificationStore.getState().clear());
  });

  it("does not claim inbox zero when the approvals fetch failed cold", async () => {
    // Not an Error and not a string, so describeError falls through to the
    // fallback and the criterion's literal wording is what is asserted.
    mockApi.approvals.mockRejectedValue({ status: 500 });

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(client.getQueryState(["approvals"])?.status).toBe("error"));
    await waitFor(() =>
      expect(screen.getByText("Could not load approvals")).toBeInTheDocument(),
    );

    expect(screen.queryByText(INBOX_ZERO)).not.toBeInTheDocument();
    // The list is mounted, and the failure is reported where the cards would be.
    expect(errorLines(container)).toEqual(["Could not load approvals"]);
    // Nothing is claimed to be waiting, either: the empty hint is not the
    // answer to a request that never arrived.
    expect(screen.queryByText("No pending approvals")).not.toBeInTheDocument();
    expect(client.getQueryData(["approvals"])).toBeUndefined();
  });

  it("still says inbox zero when the fetch succeeded and nothing is pending", async () => {
    mockApi.approvals.mockResolvedValue([]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(client.getQueryState(["approvals"])?.status).toBe("success"));
    await waitFor(() => expect(screen.getByText(INBOX_ZERO)).toBeInTheDocument());

    expect(errorLines(container)).toEqual([]);
    expect(screen.queryByText("Could not load approvals")).not.toBeInTheDocument();
    // The branch that renders the sections is the one that is not taken.
    expect(screen.queryByLabelText("Pending approvals")).not.toBeInTheDocument();
  });

  it("keeps the cards and the counts when a background refetch fails over data it has", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm", "blobert"),
      approval("lore-perm", "loregarden"),
    ]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(screen.getByText("blob-perm")).toBeInTheDocument());

    mockApi.approvals.mockRejectedValue(new Error("Request failed (503)"));
    await act(async () => {
      await client.refetchQueries({ queryKey: ["approvals"] });
    });
    await waitFor(() => expect(client.getQueryState(["approvals"])?.status).toBe("error"));

    // `data` survives a failed refetch, so this is not a cold failure and
    // nothing about the drawer changes.
    expect(client.getQueryData(["approvals"])).toHaveLength(2);
    expect(screen.queryByText(INBOX_ZERO)).not.toBeInTheDocument();
    expect(screen.getByText("blob-perm")).toBeInTheDocument();
    expect(screen.getByText("lore-perm")).toBeInTheDocument();
    expect(errorLines(container)).toEqual([]);
    expect(screen.queryByText("Could not load approvals")).not.toBeInTheDocument();
    // The Approvals section still counts what is on screen: header total,
    // approvals pill, then the two group pills.
    expect(
      Array.from(container.querySelectorAll(".count-pill")).map((n) => n.textContent),
    ).toEqual(["2", "2", "1", "1", "0"]);
  });

  it("does not depend on a notification being there to avoid inbox zero", async () => {
    // The pre-fix drawer was only honest about a cold failure by accident,
    // when something unrelated happened to be in the notification log.
    mockApi.approvals.mockRejectedValue({ status: 500 });

    const client = newClient();
    renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(client.getQueryState(["approvals"])?.status).toBe("error"));
    await waitFor(() =>
      expect(screen.getByText("Could not load approvals")).toBeInTheDocument(),
    );

    expect(useNotificationStore.getState().notifications).toHaveLength(0);
    expect(screen.getByText("No notifications yet")).toBeInTheDocument();
    expect(screen.queryByText(INBOX_ZERO)).not.toBeInTheDocument();
  });
});
