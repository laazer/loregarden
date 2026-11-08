import { fireEvent, screen, within } from "@testing-library/react";

import { ApiError } from "../../../api/http";
import { mergeQueueApi, type MergeQueueOverview } from "../../../api/mergeQueueApi";
import { uiActionRegistry } from "../../../lib/agentActions/registry";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import overviewFixture from "../../../test/fixtures/prod-shape/merge-queue-overview.json";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { MergeQueueOverviewSection } from "../MergeQueueOverviewSection";
import { TicketMergeQueueStrip } from "../TicketMergeQueueStrip";

jest.mock("../../../api/mergeQueueApi", () => ({
  ...jest.requireActual("../../../api/mergeQueueApi"),
  mergeQueueApi: { overview: jest.fn(), forTicket: jest.fn(), entry: jest.fn(), retry: jest.fn(), withdraw: jest.fn() },
}));

const mockQueue = mergeQueueApi as jest.Mocked<typeof mergeQueueApi>;
/** The API's response over the prod-shape scenario: a session PR testing, a tree and a session PR behind it. */
const prodShape = overviewFixture as unknown as MergeQueueOverview;
const entries = prodShape.queues[0].entries;

beforeEach(() => {
  jest.resetAllMocks();
  const head = entries.find((entry) => entry.is_head);
  if (!head) throw new Error("the fixture has no head");
  mockQueue.entry.mockResolvedValue({ ...head, checks: [], checks_live: true });
});

describe("MergeQueueOverviewSection", () => {
  it("groups entries by workspace with each one's count in line", async () => {
    mockQueue.overview.mockResolvedValue(prodShape);
    renderWithRouter(<MergeQueueOverviewSection onOpenTicket={jest.fn()} />);

    const group = await screen.findByRole("group", { name: /loregarden/ });
    expect(group.textContent).toContain("3 in line");
    expect(within(group).getByRole("link", { name: "loregarden" }).getAttribute("href")).toBe("/workspaces/loregarden");
  });

  it("opens a tree's ticket from its name, and a session PR from its branch name", async () => {
    mockQueue.overview.mockResolvedValue(prodShape);
    const onOpenTicket = jest.fn();
    renderWithRouter(<MergeQueueOverviewSection onOpenTicket={onOpenTicket} />);
    await screen.findByRole("group", { name: /loregarden/ });

    const tree = entries.find((entry) => entry.ticket_id !== null && entry.state === "queued");
    const session = entries.find((entry) => entry.ticket_id === null && entry.is_head);
    if (!tree || !session) throw new Error("the fixture lacks a queued tree or a session head");
    // The same tree also landed earlier: its folded landing comes after the live row.
    fireEvent.click(screen.getAllByRole("button", { name: tree.ticket_title })[0]);
    expect(onOpenTicket).toHaveBeenCalledWith(tree.ticket_id);
    expect(screen.getByRole("link", { name: new RegExp(session.source_branch) }).getAttribute("href")).toBe(
      session.pr_url,
    );
  });

  it("says what would be here when nothing has been queued", async () => {
    mockQueue.overview.mockResolvedValue({ queues: [] });
    renderWithRouter(<MergeQueueOverviewSection onOpenTicket={jest.fn()} />);

    expect((await screen.findByText(/Nothing has been through the merge queue yet/)).textContent).toContain(
      "loregarden_enqueue_merge",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names a failed read and retries it", async () => {
    mockQueue.overview.mockRejectedValueOnce(new ApiError(500, "database is locked"));
    mockQueue.overview.mockResolvedValueOnce(prodShape);
    renderWithRouter(<MergeQueueOverviewSection onOpenTicket={jest.fn()} />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not read the merge queue");
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("group", { name: /loregarden/ })).toBeTruthy();
  });

  it("routes an agent's retry to whichever mounted surface shows the entry", async () => {
    mockQueue.overview.mockResolvedValue(prodShape);
    mockQueue.forTicket.mockResolvedValue({ root_ticket_id: "r", root_external_id: "lg-r", entry: null, ahead: 0 });
    const failed = entries.find((entry) => entry.state === "failed");
    if (!failed) throw new Error("the fixture has no failed entry");
    mockQueue.retry.mockResolvedValue(failed);
    renderWithRouter(
      <>
        <MergeQueueOverviewSection onOpenTicket={jest.fn()} />
        <TicketMergeQueueStrip ticketId="r" />
      </>,
    );
    await screen.findByRole("group", { name: /loregarden/ });

    // The strip mounted last answers first, shows nothing, and passes the request on.
    await uiActionRegistry.run("merge_queue.retry", { entry_id: failed.id });
    expect(mockQueue.retry).toHaveBeenCalledWith(failed.id);
  });

  it("renders the prod-shape overview without usability problems", async () => {
    mockQueue.overview.mockResolvedValue(prodShape);
    const { container } = renderWithRouter(<MergeQueueOverviewSection onOpenTicket={jest.fn()} />);
    await screen.findByRole("group", { name: /loregarden/ });

    expect(findUsabilityProblems(container)).toEqual([]);
  });
});
