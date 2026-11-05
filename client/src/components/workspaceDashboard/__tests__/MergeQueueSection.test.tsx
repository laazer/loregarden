import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import { ApiError } from "../../../api/http";
import {
  mergeQueueApi,
  mergeQueueKeys,
  type MergeQueueEntry,
  type MergeQueueView,
} from "../../../api/mergeQueueApi";
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

/**
 * lg-milestone-that-2187 R4 (AC3, AC5, UX1(b), UX2) — a failed re-read of the
 * head entry's checks, over checks already on screen.
 *
 * AC3 asked whether this is a real instance of the shape. It is, and the
 * evidence is read-only: `HeadChecks`'s query sets only `queryKey`, `queryFn`
 * and `refetchInterval`, with no `staleTime`/`gcTime`/`retry`/`placeholderData`
 * override, and `createQueryClient()` sets no query `defaultOptions`. So a
 * failed refetch keeps `data` and sets `status: "error"`, and today's branch is
 * reached with `detail.data` present — discarding the checks the operator was
 * reading.
 *
 * KNOWN GAP, stated not fixed (spec D7): `MergeQueueSection:105` passes
 * `queries={[queue]}` — the outer LIST query — to `DashboardSection`, whose
 * `:54` `queries.find(q => q.isError)` still unmounts this row and its notice
 * when the LIST read fails. Filed as lg-milestone-that-2246. Every test below
 * therefore keeps `mockQueue.queue` succeeding, or it would pass for the wrong
 * reason: the section's own error pane would have rendered and the assertion
 * about the row's notice would be vacuous.
 */

/** Make every head-checks re-read from here on reject (see failNextRead's note in the board suite). */
function failNextChecksRead() {
  mockQueue.entry.mockRejectedValue(new ApiError(502, CHECKS_REASON));
}

const CHECKS_REASON = "GitHub did not answer: gh timed out";
const CHECKS_LEAD = "These checks may be out of date — the last re-read failed.";

/** The head row's own re-read, with no timer. `refetchInterval` fires exactly this. */
function pollHeadChecks(client: QueryClient) {
  return act(async () => {
    await client.refetchQueries({ queryKey: mergeQueueKeys.entry(head().id) });
    // query-core writes the error into the cache synchronously, but the
    // subscribed component's render lands a tick later, so a poll that
    // returned as soon as the refetch settled would let a SYNCHRONOUS
    // assertion read the pre-failure DOM and pass under an implementation
    // that never renders the notice at all. Flushing a macrotask inside
    // `act` makes the poll deterministic, which is what lets the negative
    // assertions below ("no alert", "no empty state") be trustworthy.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    client,
    ...renderWithRouter(<MergeQueueSection workspaceSlug="loregarden" now={NOW} />, {
      queryClient: client,
    }),
  };
}

describe("a failed re-read of the head entry's checks", () => {
  it("R4-1 — keeps the check names and reports the failed re-read as one polite notice", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockResolvedValue({
      ...head(),
      checks: [
        { name: "Server (Python)", outcome: "pending", url: "https://github.com/laazer/loregarden/actions/1" },
      ],
      checks_live: true,
    });

    const { client } = renderWithClient();
    await screen.findByText(/Waiting on/);

    failNextChecksRead();

    await pollHeadChecks(client);

    expect(screen.getByText(/Waiting on/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Server (Python)" })).toBeTruthy();
    const notice = screen.getByTestId("head-checks-refresh-failed");
    expect(screen.getAllByTestId("head-checks-refresh-failed")).toHaveLength(1);
    expect(notice).toHaveAttribute("role", "status");
    expect(notice).toHaveTextContent(CHECKS_LEAD);
    expect(notice).toHaveTextContent(`${CHECKS_REASON}.`);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/Could not read its checks/)).toBeNull();
  });

  it("R4-2 — keeps each check's GitHub link and its href (UX1(b))", async () => {
    // The per-check link is the only action this row offers. A fix that keeps
    // the check names and drops the links keeps the question and removes the
    // answer.
    const url = "https://github.com/laazer/loregarden/actions/1";
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockResolvedValue({
      ...head(),
      checks: [{ name: "Server (Python)", outcome: "pending", url }],
      checks_live: true,
    });

    const { client } = renderWithClient();
    await screen.findByText(/Waiting on/);

    failNextChecksRead();

    await pollHeadChecks(client);

    expect(screen.getByRole("link", { name: "Server (Python)" })).toHaveAttribute("href", url);
  });

  it("R4-6 — leaves focus where it was across the failed re-read (UX3(d))", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockResolvedValue({
      ...head(),
      checks: [{ name: "Server (Python)", outcome: "pending", url: "" }],
      checks_live: true,
    });

    const { client } = renderWithClient();
    const link = (await screen.findAllByRole("link"))[0];
    link.focus();
    const before = document.activeElement;

    failNextChecksRead();

    await pollHeadChecks(client);

    expect(screen.getByTestId("head-checks-refresh-failed")).toBeInTheDocument();
    expect(document.activeElement).toBe(before);
  });

  it("UX2(e) — the notice sits after the checks line, where today's danger line is", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockResolvedValue({
      ...head(),
      checks: [{ name: "Server (Python)", outcome: "pending", url: "" }],
      checks_live: true,
    });

    const { client } = renderWithClient();
    const checks = await screen.findByText(/Waiting on/);

    failNextChecksRead();

    await pollHeadChecks(client);

    const notice = screen.getByTestId("head-checks-refresh-failed");
    expect(checks.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("a head-checks read that had nothing to lose", () => {
  it("R4-4 — takes the danger line when the first read succeeded with zero checks", async () => {
    // "Every required check passed; landing next cycle." is a health claim the
    // row cannot make while its read is failing (UX2(a)).
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockResolvedValue({ ...head(), checks: [], checks_live: true });

    const { client } = renderWithClient();
    await screen.findByText("Every required check passed; landing next cycle.");

    failNextChecksRead();

    await pollHeadChecks(client);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not read its checks");
    expect(screen.getByRole("alert")).toHaveTextContent(CHECKS_REASON);
    expect(screen.queryByText("Every required check passed; landing next cycle.")).toBeNull();
    expect(screen.queryByTestId("head-checks-refresh-failed")).toBeNull();
  });

  it("R4-3 — a first-load failure keeps today's danger line and its reason", async () => {
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockRejectedValue(new ApiError(502, CHECKS_REASON));

    renderWithClient();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read its checks");
    expect(alert).toHaveTextContent(CHECKS_REASON);
    expect(screen.queryByTestId("head-checks-refresh-failed")).toBeNull();
  });

  it("R4-5 — zero non-passing checks and no failure still reads as landing next cycle", async () => {
    // `detail.data.checks` becomes `detail.data?.checks ?? []` because branch 2
    // no longer narrows `data` for TypeScript. This is the assertion that the
    // nullish default did not change the success path.
    mockQueue.queue.mockResolvedValue(prodShape);
    mockQueue.entry.mockResolvedValue({
      ...head(),
      checks: [{ name: "PR UX section", outcome: "passing", url: "" }],
      checks_live: true,
    });

    renderWithClient();

    expect(
      await screen.findByText("Every required check passed; landing next cycle."),
    ).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("head-checks-refresh-failed")).toBeNull();
  });
});
