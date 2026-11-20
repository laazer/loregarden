/**
 * AC-3 / AC-5 / AC-7 / AC-8 / AC-9 / AC-10 (lg-inbox-organized-2738):
 * the grouped drawer under the conditions `ApprovalsListGrouping.test.tsx`
 * does not reach.
 *
 * That file pins AC-11's eight clauses on a query that resolves once and then
 * holds still. Every defect below survives all of them:
 *
 *  - an error branch missing `&& approvals.data === undefined`, which blanks a
 *    full drawer on one failed 5s poll (the posture
 *    `test/__tests__/polledReadFailurePosture.test.ts` exists to stop);
 *  - `isSubmitting` matched by index, which disables the wrong card;
 *  - a reorder that re-parents cards, which blurs whatever the operator was
 *    pointing at;
 *  - a per-group cap or a prettified heading, neither of which the fixtures
 *    above can see at two cards and two lowercase slugs.
 */

import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";

import { api, type Approval } from "../../../api/client";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { ApprovalsList } from "../ApprovalsList";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const EM_DASH = "—";

function approval(
  id: string,
  workspace_slug: string,
  { gate = false }: { gate?: boolean } = {},
): Approval {
  return {
    id,
    title: id,
    level: "medium",
    workspace_slug,
    stage_key: "implement",
    stage_name: "Implement",
    impact: "",
    checklist: [],
    route_options: [],
    ticket_id: "ticket_1",
    ticket_external_id: "lg-1",
    kind: gate ? "workflow_gate" : "cli_permission",
    status: "pending",
    run_id: "",
    tool_name: "Bash",
    tool_input_json: "{}",
    cli_adapter: "",
  };
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

const sections = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("section.inbox-section"));

const labels = (container: HTMLElement) =>
  sections(container).map((section) => section.getAttribute("aria-label"));

describe("ApprovalsList grouping — a poll that fails over good data", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.resolveApproval.mockResolvedValue({});
  });

  /**
   * AC-8 gates the failure line on `approvals.isError && approvals.data ===
   * undefined`. TanStack v5 moves a query to `status: "error"` on a failed
   * BACKGROUND refetch while keeping the data it already has, so an error
   * branch written as `approvals.isError` alone is indistinguishable from the
   * correct one until a second fetch fails — at which point the operator's
   * fifteen pending approvals are replaced by one line of red, every five
   * seconds, until the server comes back.
   */
  it("keeps every group on screen when a refetch fails over cached data", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-gate", "blobert", { gate: true }),
      approval("lore-perm", "loregarden"),
    ]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: client,
    });
    await waitFor(() => expect(sections(container)).toHaveLength(2));

    mockApi.approvals.mockRejectedValue(new Error("Request failed (503)"));
    await act(async () => {
      await client.refetchQueries({ queryKey: ["approvals"] });
    });
    await waitFor(() =>
      expect(client.getQueryState(["approvals"])?.status).toBe("error"),
    );

    // The data is still there, so it is still what the drawer shows.
    expect(client.getQueryData(["approvals"])).toHaveLength(2);
    expect(sections(container)).toHaveLength(2);
    expect(screen.getByText("blob-gate")).toBeInTheDocument();
    expect(screen.getByText("lore-perm")).toBeInTheDocument();
    expect(screen.queryByText("Request failed (503)")).not.toBeInTheDocument();
    expect(screen.queryByText("Could not load approvals")).not.toBeInTheDocument();
    expect(screen.queryByText("No pending approvals")).not.toBeInTheDocument();
  });

  // AC-8 says nothing about scope: the pane fails the same way the drawer does.
  it("reports a cold-cache failure on the scoped surface too", async () => {
    mockApi.approvals.mockRejectedValue({ status: 500 });

    const { container } = renderWithRouter(
      <ApprovalsList workspaceSlug="blobert" isActive />,
      { queryClient: newClient() },
    );

    expect(await screen.findByText("Could not load approvals")).toBeInTheDocument();
    expect(screen.queryByText("No pending approvals in blobert")).not.toBeInTheDocument();
    expect(container.querySelector(".inbox-empty-hint")).toBeNull();
  });

  // AC-7's loading rule is not scoped to the drawer either.
  it("renders no empty hint on the scoped surface before the first response", () => {
    mockApi.approvals.mockReturnValue(new Promise<Approval[]>(() => {}));

    const { container } = renderWithRouter(
      <ApprovalsList workspaceSlug="blobert" isActive />,
      { queryClient: newClient() },
    );

    expect(container.querySelector(".inbox-empty-hint")).toBeNull();
    expect(screen.queryByText("No pending approvals in blobert")).not.toBeInTheDocument();
  });
});

describe("ApprovalsList grouping — what a poll may move", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.resolveApproval.mockResolvedValue({});
  });

  /**
   * AC-7: "Resolving the last card in a workspace removes that heading in the
   * same render." An implementation that built its groups from the slugs it
   * saw on a previous poll, or that kept a `Map` across renders, would leave
   * an empty `blobert — 0 pending approvals` heading behind.
   */
  it("drops a workspace's heading the moment its last card is resolved", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-gate", "blobert", { gate: true }),
      approval("lore-perm", "loregarden"),
    ]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: client,
    });
    await waitFor(() => expect(sections(container)).toHaveLength(2));

    act(() => {
      client.setQueryData<Approval[]>(["approvals"], [approval("lore-perm", "loregarden")]);
    });

    await waitFor(() => expect(sections(container)).toHaveLength(1));
    expect(labels(container)).toEqual([`loregarden ${EM_DASH} 1 pending approval`]);
    expect(screen.queryByText("blobert")).not.toBeInTheDocument();
    expect(screen.queryByText("No pending approvals")).not.toBeInTheDocument();
  });

  it("falls back to the empty hint when the last approval anywhere is resolved", async () => {
    mockApi.approvals.mockResolvedValue([approval("blob-gate", "blobert", { gate: true })]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: client,
    });
    await waitFor(() => expect(sections(container)).toHaveLength(1));

    act(() => {
      client.setQueryData<Approval[]>(["approvals"], []);
    });

    await waitFor(() => expect(sections(container)).toHaveLength(0));
    expect(screen.getByText("No pending approvals")).toBeInTheDocument();
  });

  /**
   * AC-9: "a focused control is not blurred ... by a reorder". The sibling
   * file asserts the card's DOM NODE survives, which React guarantees from the
   * card key alone — but a `<section>` keyed by index, or by a count, re-parents
   * the node and takes the focus with it. Only reading `document.activeElement`
   * across the reorder can tell those two apart.
   */
  it("holds focus on a card's control across a reorder that moves its group", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm", "blobert"),
      approval("lore-perm", "loregarden"),
    ]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: client,
    });
    await waitFor(() => expect(sections(container)).toHaveLength(2));
    expect(labels(container)[0]).toBe(`blobert ${EM_DASH} 1 pending approval`);

    const trailingCard = screen.getByText("lore-perm").closest("section.inbox-section");
    const deny = Array.from(
      (trailingCard as HTMLElement).querySelectorAll("button"),
    ).find((button) => button.textContent === "Deny") as HTMLButtonElement;
    expect(deny).toBeDefined();
    deny.focus();
    expect(document.activeElement).toBe(deny);

    // loregarden's prompt becomes a gate and its group moves to the front,
    // which is AC-4's accepted trade. The operator's finger must not move.
    act(() => {
      client.setQueryData<Approval[]>(
        ["approvals"],
        [
          approval("blob-perm", "blobert"),
          approval("lore-perm", "loregarden", { gate: true }),
        ],
      );
    });

    await waitFor(() =>
      expect(labels(container)[0]).toBe(`loregarden ${EM_DASH} 1 pending approval`),
    );
    expect(document.activeElement).toBe(deny);
    expect(deny.isConnected).toBe(true);
  });
});

describe("ApprovalsList grouping — the headings and nothing else", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.resolveApproval.mockResolvedValue({});
  });

  /**
   * AC-5: the heading prints `approval.workspace_slug` VERBATIM, never
   * `Workspace.name` and never title-cased. Every fixture elsewhere is a
   * single lowercase word, which a hyphen-stripping or capitalising
   * "prettifier" renders identically. `lore-eden` is a real slug whose display
   * name is `Lore Eden`, so it separates the two.
   */
  it("prints a hyphenated slug exactly as stored", async () => {
    mockApi.approvals.mockResolvedValue([approval("perm-1", "lore-eden")]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(1));
    expect(container.querySelector("h3.inbox-section-title")?.textContent).toBe("lore-eden");
    expect(labels(container)).toEqual([`lore-eden ${EM_DASH} 1 pending approval`]);
  });

  // AC-5: no second query. A display name would need one, and the module's
  // stated rule is that it reads almost no state.
  it("resolves no workspace names, so it issues no second query", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm", "blobert"),
      approval("lore-perm", "lore-eden"),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));
    expect(mockApi.workspaces).not.toHaveBeenCalled();
  });

  /**
   * AC-10: no per-group cap, no 'show more', no pagination — the drawer's own
   * scroll carries the 24-card all-time peak. A `.slice(0, 10)` added "so the
   * drawer stays readable" passes every other test in both files.
   */
  it("renders all 24 cards of the historical peak, with no cap and no 'show more'", async () => {
    const WORKSPACES = ["blobert", "loregarden", "tinkercg"];
    mockApi.approvals.mockResolvedValue(
      Array.from({ length: 24 }, (_, index) =>
        approval(`card-${index}`, WORKSPACES[index % 3], { gate: index === 7 }),
      ),
    );

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(3));
    expect(screen.getAllByText(/^card-\d+$/)).toHaveLength(24);

    const pills = Array.from(container.querySelectorAll("section.inbox-section .count-pill"));
    expect(pills.map((pill) => pill.textContent)).toEqual(["8", "8", "8"]);
    expect(labels(container)[0]).toBe(`loregarden ${EM_DASH} 8 pending approvals`);

    for (const text of ["Show more", "Show all", "Load more"]) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
  });

  // AC-10: nothing was added beside the headings. A filter, a collapse toggle
  // or a 'show more' would be an extra child of the section — the one place a
  // group-level control can go.
  it("gives each section exactly a header and its cards, and nothing else", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-gate", "blobert", { gate: true }),
      approval("lore-perm-1", "loregarden"),
      approval("lore-perm-2", "loregarden"),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));

    const shape = sections(container).map((section) => ({
      header: section.children[0]?.className,
      children: section.children.length,
    }));
    expect(shape).toEqual([
      { header: "inbox-section-header", children: 2 },
      { header: "inbox-section-header", children: 3 },
    ]);
  });

  /**
   * An approval whose `workspace_slug` is empty is still a blocked run, and
   * the drawer's stated reason for existing across workspaces is that no
   * blocked run is hidden. The heading it gets is empty — a real UI gap, filed
   * rather than fixed here — but the CARD must be reachable.
   */
  it("still renders the card of an approval that carries no workspace slug", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-gate", "blobert", { gate: true }),
      approval("orphan", ""),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));
    expect(screen.getByText("orphan")).toBeInTheDocument();
    expect(screen.getByText("blob-gate")).toBeInTheDocument();
  });
});

describe("ApprovalsList grouping — in flight", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  /**
   * AC-9: `isSubmitting` is matched by approval id, "never by index or
   * position". Grouping breaks the one-to-one between a card's position in
   * `orderedApprovals` and its position on screen, so an index-matched
   * implementation disables a card in the OTHER workspace while the one the
   * operator clicked stays live and can be double-submitted.
   */
  it("disables only the card that was submitted, not its neighbour", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm", "blobert"),
      approval("lore-perm", "loregarden"),
    ]);
    // Never settles: the mutation stays pending for the whole assertion.
    mockApi.resolveApproval.mockReturnValue(new Promise<never>(() => {}));

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });
    await waitFor(() => expect(sections(container)).toHaveLength(2));

    const cardOf = (id: string) =>
      screen.getByText(id).closest("section.inbox-section") as HTMLElement;
    const buttonIn = (id: string, text: string) =>
      Array.from(cardOf(id).querySelectorAll("button")).find(
        (button) => button.textContent === text,
      ) as HTMLButtonElement;

    fireEvent.click(buttonIn("lore-perm", "Allow"));

    await waitFor(() => expect(buttonIn("lore-perm", "Allow").disabled).toBe(true));
    expect(buttonIn("lore-perm", "Deny").disabled).toBe(true);
    expect(buttonIn("blob-perm", "Allow").disabled).toBe(false);
    expect(buttonIn("blob-perm", "Deny").disabled).toBe(false);
    expect(mockApi.resolveApproval).toHaveBeenCalledTimes(1);
    expect(mockApi.resolveApproval).toHaveBeenCalledWith(
      "lore-perm",
      expect.objectContaining({ action: "approve" }),
    );
  });
});
