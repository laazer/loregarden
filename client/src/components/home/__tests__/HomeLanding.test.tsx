import { fireEvent, screen } from "@testing-library/react";

import { ApiError } from "../../../api/http";
import { mergeQueueApi, type MergeQueueOverview } from "../../../api/mergeQueueApi";
import overviewFixture from "../../../test/fixtures/prod-shape/merge-queue-overview.json";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { HomeLanding } from "../HomeLanding";

jest.mock("../../../api/mergeQueueApi", () => ({
  ...jest.requireActual("../../../api/mergeQueueApi"),
  mergeQueueApi: { overview: jest.fn(), entry: jest.fn(), retry: jest.fn(), withdraw: jest.fn() },
}));

const mockQueue = mergeQueueApi as jest.Mocked<typeof mergeQueueApi>;
const prodShape = overviewFixture as unknown as MergeQueueOverview;
const entries = prodShape.queues[0].entries;
/** The fixture's timestamps are pinned to 2026-09-28T00:00Z. */
const NOW = new Date("2026-09-28T00:10:00Z");

beforeEach(() => {
  jest.resetAllMocks();
  const head = entries.find((entry) => entry.is_head);
  if (!head) throw new Error("the fixture has no head");
  mockQueue.entry.mockResolvedValue({ ...head, checks: [], checks_live: true });
});

function render(onOpenTicket = jest.fn()) {
  return renderWithRouter(<HomeLanding now={NOW} onOpenTicket={onOpenTicket} />);
}

describe("HomeLanding", () => {
  it("lists what is in line, then what failed, and leaves withdrawals and landings off", async () => {
    mockQueue.overview.mockResolvedValue(prodShape);
    render();

    const section = await screen.findByRole("region", { name: "Landing on main" });
    await screen.findAllByRole("listitem");
    expect(section.textContent).toContain("3 in line");
    const shown = screen.getAllByRole("listitem").length;
    const expected = entries.filter((entry) => ["queued", "staging", "testing", "merging", "failed", "timed_out"].includes(entry.state));
    expect(shown).toBe(expected.length);
  });

  it("opens a tree's ticket from its name", async () => {
    mockQueue.overview.mockResolvedValue(prodShape);
    const onOpenTicket = jest.fn();
    render(onOpenTicket);
    const tree = entries.find((entry) => entry.ticket_id !== null && entry.state === "queued");
    if (!tree) throw new Error("the fixture has no queued tree");

    fireEvent.click(await screen.findByRole("button", { name: tree.ticket_title }));
    expect(onOpenTicket).toHaveBeenCalledWith(tree.ticket_id);
  });

  it("says what landed when nothing is in line", async () => {
    const landedOnly = { queues: [{ ...prodShape.queues[0], entries: entries.filter((entry) => entry.state === "merged") }] };
    mockQueue.overview.mockResolvedValue(landedOnly);
    render();

    const empty = await screen.findByText(/Nothing is landing on main right now/);
    expect(empty.textContent).toContain(`${landedOnly.queues[0].entries.length} pull requests landed`);
  });

  it("names a failed read instead of an empty card", async () => {
    mockQueue.overview.mockRejectedValue(new ApiError(500, "database is locked"));
    render();

    expect(await screen.findByText(/Couldn't read the merge queue: .*database is locked/)).toBeTruthy();
    expect(screen.queryByText(/Nothing is landing/)).toBeNull();
  });
});
