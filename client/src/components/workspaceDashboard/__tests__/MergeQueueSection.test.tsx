import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import { ApiError } from "../../../api/http";
import { mergeQueueApi, type MergeQueueEntry, type MergeQueueView } from "../../../api/mergeQueueApi";
import { uiActionRegistry } from "../../../lib/agentActions/registry";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import queueFixture from "../../../test/fixtures/prod-shape/merge-queue-loregarden.json";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { MergeQueueSection } from "../MergeQueueSection";

jest.mock("../../../api/mergeQueueApi", () => ({
  ...jest.requireActual("../../../api/mergeQueueApi"),
  mergeQueueApi: { queue: jest.fn(), entry: jest.fn(), retry: jest.fn(), withdraw: jest.fn() },
}));

const mockQueue = mergeQueueApi as jest.Mocked<typeof mergeQueueApi>;
/** The API's own response over the prod-shape scenario: a testing head, two behind, three stopped, eight merged. */
const prodShape = queueFixture as unknown as MergeQueueView;
const NOW = new Date("2026-09-28T00:10:00Z");

function render() {
  return renderWithRouter(<MergeQueueSection workspaceSlug="loregarden" now={NOW} />);
}

function head(): MergeQueueEntry {
  const found = prodShape.entries.find((entry) => entry.is_head);
  if (!found) throw new Error("the fixture has no head");
  return found;
}

beforeEach(() => {
  jest.resetAllMocks();
  mockQueue.entry.mockResolvedValue({
    ...head(),
    checks: [
      { name: "Server (Python)", outcome: "pending", url: "https://github.com/laazer/loregarden/actions/1" },
      { name: "PR UX section", outcome: "passing", url: "" },
    ],
    checks_live: true,
  });
});

describe("MergeQueueSection", () => {
  it("says what would be here and how to get one when nothing is landing", async () => {
    mockQueue.queue.mockResolvedValue({ workspace_slug: "loregarden", entries: [], head_id: null });
    render();

    const empty = await screen.findByText(/Nothing is landing on main/);
    expect(empty.textContent).toContain("loregarden_enqueue_merge");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("draws a skeleton while the queue loads", () => {
    mockQueue.queue.mockReturnValue(new Promise(() => {}));
    render();
    expect(screen.getByText("Loading merge queue…")).toBeTruthy();
  });

  it("names the failure and retries on request, never an empty queue", async () => {
    mockQueue.queue.mockRejectedValueOnce(new ApiError(502, "Could not read the entry's checks from GitHub: gh timed out"));
    mockQueue.queue.mockResolvedValueOnce(prodShape);
    render();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not read the merge queue");
    expect(screen.queryByText(/Nothing is landing/)).toBeNull();
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Waiting on/)).toBeTruthy();
  });

  it("opens each entry's PR from its name, and says what the head waits on", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    render();

    const waiting = await screen.findByText(/Waiting on/);
    expect(within(waiting).getByRole("link", { name: "Server (Python)" })).toBeTruthy();
    expect(waiting.textContent).not.toContain("PR UX section");
    const links = screen.getAllByRole("link").filter((link) => link.getAttribute("href")?.includes("/pull/"));
    expect(links[0].getAttribute("href")).toBe(head().pr_url);
    expect(screen.getByText("waiting for required checks to finish")).toBeTruthy();
  });

  it("shows a failed entry's reason and log tail, and keeps merged history folded", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    render();

    await screen.findByText(/required check failed on the staged commit/);
    expect(screen.getByText("Failing log tail")).toBeTruthy();
    const landed = screen.getByText(/Landed recently \(8\)/).closest("details");
    expect(landed?.open).toBe(false);
  });

  it("disables Retry while it is in flight so a second click cannot fire it twice", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    let resolve: (entry: MergeQueueEntry) => void = () => {};
    mockQueue.retry.mockReturnValue(new Promise((done) => (resolve = done)));
    render();

    const [retry] = await screen.findAllByRole("button", { name: /^Retry PR #/ });
    fireEvent.click(retry);
    expect((retry as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(retry);
    expect(mockQueue.retry).toHaveBeenCalledTimes(1);
    await act(async () => resolve(head()));
    await waitFor(() => expect((retry as HTMLButtonElement).disabled).toBe(false));
  });

  it("offers retry and withdraw to an agent while the section is on screen", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    const view = render();
    await screen.findByText(/Waiting on/);

    expect(uiActionRegistry.available()).toEqual(expect.arrayContaining(["merge_queue.retry", "merge_queue.withdraw"]));
    view.unmount();
    expect(uiActionRegistry.available()).not.toContain("merge_queue.retry");
  });

  it("renders the prod-shape queue without usability problems", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    const { container } = render();
    await screen.findByText(/Waiting on/);

    expect(findUsabilityProblems(container)).toEqual([]);
  });
});
