import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import { ApiError } from "../../../api/http";
import {
  mergeQueueApi,
  type MergeQueueEntry,
  type MergeQueueOverview,
  type TicketMergeQueueView,
} from "../../../api/mergeQueueApi";
import overviewFixture from "../../../test/fixtures/prod-shape/merge-queue-overview.json";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { TicketMergeQueueStrip } from "../TicketMergeQueueStrip";

jest.mock("../../../api/mergeQueueApi", () => ({
  ...jest.requireActual("../../../api/mergeQueueApi"),
  mergeQueueApi: { forTicket: jest.fn(), entry: jest.fn(), retry: jest.fn(), withdraw: jest.fn() },
}));

const mockQueue = mergeQueueApi as jest.Mocked<typeof mergeQueueApi>;
const entries = (overviewFixture as unknown as MergeQueueOverview).queues[0].entries;

function treeEntry(state: MergeQueueEntry["state"]): MergeQueueEntry {
  const found = entries.find((entry) => entry.ticket_id !== null && entry.state === state);
  if (!found) throw new Error(`the fixture has no tree entry in ${state}`);
  return found;
}

function view(entry: MergeQueueEntry | null, ahead = 0, rootId = entry?.ticket_id ?? "root"): TicketMergeQueueView {
  return { root_ticket_id: rootId, root_external_id: entry?.ticket_external_id ?? "lg-root-1", entry, ahead };
}

function render(ticketId: string) {
  return renderWithRouter(<TicketMergeQueueStrip ticketId={ticketId} />);
}

beforeEach(() => {
  jest.resetAllMocks();
  mockQueue.entry.mockResolvedValue({ ...treeEntry("queued"), checks: [], checks_live: true });
});

describe("TicketMergeQueueStrip", () => {
  it("renders nothing for a tree that was never queued", async () => {
    mockQueue.forTicket.mockResolvedValue(view(null));
    const { container } = render("t-1");

    await waitFor(() => expect(mockQueue.forTicket).toHaveBeenCalledWith("t-1"));
    await act(async () => {});
    expect(container.textContent).toBe("");
  });

  it("says a queued tree's place in line and links its PR", async () => {
    const queued = treeEntry("queued");
    mockQueue.forTicket.mockResolvedValue(view(queued, 2));
    render(queued.ticket_id ?? "");

    const strip = await screen.findByRole("region", { name: "Merge queue" });
    expect(strip.textContent).toContain("Landing on main");
    expect(within(strip).getByText("2 ahead in line")).toBeTruthy();
    const pr = within(strip).getByRole("link", { name: `PR #${queued.pr_number}` });
    expect(pr.getAttribute("href")).toBe(queued.pr_url);
  });

  it("names the root a child ticket lands with", async () => {
    const queued = treeEntry("queued");
    mockQueue.forTicket.mockResolvedValue(view(queued, 0));
    render("a-child-of-the-root");

    const strip = await screen.findByRole("region", { name: "Merge queue" });
    expect(within(strip).getByText("next in line")).toBeTruthy();
    expect(within(strip).getByRole("link", { name: queued.ticket_external_id })).toBeTruthy();
  });

  it("shows why a failed entry stopped, and retries it once while in flight", async () => {
    const failed = treeEntry("failed");
    mockQueue.forTicket.mockResolvedValue(view(failed));
    let resolve: (entry: MergeQueueEntry) => void = () => {};
    mockQueue.retry.mockReturnValue(new Promise((done) => (resolve = done)));
    render(failed.ticket_id ?? "");

    const strip = await screen.findByRole("region", { name: "Merge queue" });
    expect(strip.textContent).toContain("Left the merge queue");
    expect(strip.textContent).toContain(failed.detail.split("\n")[0]);
    const retry = within(strip).getByRole("button", { name: `Retry PR #${failed.pr_number} in the merge queue` });
    fireEvent.click(retry);
    fireEvent.click(retry);
    expect(mockQueue.retry).toHaveBeenCalledTimes(1);
    expect((retry as HTMLButtonElement).disabled).toBe(true);
    await act(async () => resolve(failed));
  });

  it("says a landed tree landed", async () => {
    const merged = treeEntry("merged");
    mockQueue.forTicket.mockResolvedValue(view(merged));
    render(merged.ticket_id ?? "");

    const strip = await screen.findByRole("region", { name: "Merge queue" });
    expect(strip.textContent).toContain("Landed on main");
    expect(within(strip).queryByRole("button")).toBeNull();
  });

  it("names a failed first read and retries it, rather than showing nothing", async () => {
    const queued = treeEntry("queued");
    mockQueue.forTicket.mockRejectedValueOnce(new ApiError(500, "database is locked"));
    mockQueue.forTicket.mockResolvedValueOnce(view(queued, 1));
    render(queued.ticket_id ?? "");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not read where this ticket stands in the merge queue");
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("1 ahead in line")).toBeTruthy();
  });
});
