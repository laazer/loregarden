/**
 * AC-13 (lg-inbox-organized-2738): the drawer's cold-failure branch under the
 * conditions `ApprovalInboxColdFailure.test.tsx` cannot see.
 *
 * That file renders the branch and reads the final paint. Every defect below
 * survives all four of its tests:
 *
 *  - **The branch that unmounts its own witness.** `ApprovalsList` is a second
 *    observer of `["approvals"]`, and mounting an observer on an errored query
 *    refetches it; in TanStack v5 a fetch on a query holding no data resets
 *    `status` to `pending`. So a predicate written as `isError && data ===
 *    undefined` — the one the ticket description pins — goes false the instant
 *    the mount it caused begins, the list unmounts, the fetch rejects, and it
 *    goes true again. Test design measured 269 fetches in 300ms of wall clock
 *    against a one-fetch control. The four inherited tests poll with `waitFor`,
 *    so each of them can land on a frame where the error line happens to be up:
 *    an oscillating drawer passes them by luck, and the operator gets a
 *    flickering panel and a request every millisecond while the server is down.
 *    Nothing is asserted about how many times the fetch ran, which is the only
 *    place that defect is visible.
 *  - **A count that lies to make `empty` false.** The cheapest way to pass the
 *    inherited suite is to have `usePendingApprovalCount` answer something
 *    other than `0` on failure. `empty` is then false, the list mounts, AC-8's
 *    line renders, all four pass — and the drawer's header reads `1` with
 *    nothing behind it. AC-13 says the pill still prints `0`.
 *  - **A latched failure.** `useState` set once on error never clears, so the
 *    drawer keeps the error line after the server comes back. Every inherited
 *    test ends while the failure is still current.
 *  - **`data === undefined` dropped from the predicate.** Over cached *cards* a
 *    bare `isError` is caught by the third inherited test. Over a cached EMPTY
 *    array it is not: `0 approvals` is a real answer the drawer already has, and
 *    a failed poll on top of it must not turn "nothing is waiting" into "could
 *    not load". That is the boundary between no-response and a response of
 *    none, and no test stands on it.
 *  - **A seventh observer.** Lifting a fresh `useQuery(["approvals"])` into the
 *    panel satisfies AC-13 and adds another copy of the duplication
 *    lg-frontend-quality-2747 records, which the repository-wide census
 *    (`test/__tests__/approvalsQueryMeta.test.ts`) passes as a floor.
 */

import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { api, type Approval } from "../../api/client";
import { useNotificationStore } from "../../state/notificationStore";
import { useUiStore } from "../../state/uiStore";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalInboxPanel } from "../ApprovalInboxPanel";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const INBOX_ZERO = "Inbox zero — nothing needs your attention";
const ERROR_LINE = "Could not load approvals";

/**
 * Long enough that an oscillating drawer runs hundreds of fetches through it
 * (269 were measured over 300ms), and far short of the 5s poll, so a healthy
 * implementation runs none.
 */
const SETTLE_MS = 300;

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

function pills(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".count-pill")).map((n) => n.textContent ?? "");
}

async function settle(ms: number): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

describe("ApprovalInboxPanel cold failure, adversarially", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    act(() => useNotificationStore.getState().clear());
    useUiStore.getState().setInboxOpen(true);
  });

  afterEach(() => {
    act(() => useUiStore.getState().setInboxOpen(false));
    act(() => useNotificationStore.getState().clear());
  });

  it("reaches a resting state instead of refetching forever", async () => {
    mockApi.approvals.mockRejectedValue({ status: 500 });

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    // The fetch count is read before the paint on purpose: a looping branch
    // fails here, naming the loop, rather than further down on whichever side
    // of the flicker the sampling happened to land.
    await settle(SETTLE_MS);
    const afterSettle = mockApi.approvals.mock.calls.length;
    await settle(SETTLE_MS);

    // Stability, not a threshold: an oscillating branch is still fetching in
    // the second window, and 5s has not elapsed so a healthy one is not.
    expect(mockApi.approvals.mock.calls.length).toBe(afterSettle);
    // The mount of `ApprovalsList` refetches the errored query once. Anything
    // beyond a handful is the branch feeding itself.
    expect(afterSettle).toBeLessThanOrEqual(4);
    // And the resting paint is the failure, not whichever side of the flicker
    // the last frame happened to land on.
    expect(errorLines(container)).toEqual([ERROR_LINE]);
    expect(screen.queryByText(INBOX_ZERO)).not.toBeInTheDocument();
  });

  it("keeps the header and approvals counts at zero while reporting the failure", async () => {
    mockApi.approvals.mockRejectedValue({ status: 500 });

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(screen.getByText(ERROR_LINE)).toBeInTheDocument());

    // Inbox total, Approvals, Notifications. Nothing is waiting that anyone
    // knows of, so the drawer must not imply something is.
    expect(pills(container)).toEqual(["0", "0", "0"]);
  });

  it("stops reporting the failure once the fetch succeeds again", async () => {
    mockApi.approvals.mockRejectedValue({ status: 500 });

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(screen.getByText(ERROR_LINE)).toBeInTheDocument());

    mockApi.approvals.mockResolvedValue([approval("blob-perm", "blobert")]);
    await act(async () => {
      await client.refetchQueries({ queryKey: ["approvals"] });
    });

    await waitFor(() => expect(screen.getByText("blob-perm")).toBeInTheDocument());
    expect(errorLines(container)).toEqual([]);
    expect(screen.queryByText(INBOX_ZERO)).not.toBeInTheDocument();
    expect(pills(container)).toEqual(["1", "1", "1", "0"]);

    // And the recovered drawer can go legitimately empty again: a latched
    // failure flag would put the error line back instead of this.
    mockApi.approvals.mockResolvedValue([]);
    await act(async () => {
      await client.refetchQueries({ queryKey: ["approvals"] });
    });

    await waitFor(() => expect(screen.getByText(INBOX_ZERO)).toBeInTheDocument());
    expect(errorLines(container)).toEqual([]);
  });

  it("does not turn a known-empty inbox into a load failure on a failed poll", async () => {
    // The boundary the predicate is written on: `[]` is an answer, `undefined`
    // is the absence of one. The inherited refetch test holds cards, where a
    // bare `isError` is caught; here it is not.
    mockApi.approvals.mockResolvedValue([]);

    const client = newClient();
    const { container } = renderWithRouter(<ApprovalInboxPanel />, { queryClient: client });

    await waitFor(() => expect(screen.getByText(INBOX_ZERO)).toBeInTheDocument());

    mockApi.approvals.mockRejectedValue(new Error("Request failed (503)"));
    await act(async () => {
      await client.refetchQueries({ queryKey: ["approvals"] });
    });
    await waitFor(() => expect(client.getQueryState(["approvals"])?.status).toBe("error"));

    expect(client.getQueryData(["approvals"])).toEqual([]);
    expect(screen.getByText(INBOX_ZERO)).toBeInTheDocument();
    expect(errorLines(container)).toEqual([]);
    expect(screen.queryByText(ERROR_LINE)).not.toBeInTheDocument();
  });

  it("adds no observer of its own to the shared approvals query", () => {
    // The panel learns about the failure from the hook it already calls. A
    // fresh `useQuery(["approvals"])` here would satisfy AC-13 and pass the
    // repository census, which counts a floor and not a list.
    const path = join(__dirname, "..", "ApprovalInboxPanel.tsx");
    const source = readFileSync(path, "utf8");

    // Textual, and deliberately narrow: the exact shared key, which is the one
    // whose observers `test/__tests__/approvalsQueryMeta.test.ts` censuses. A
    // key built from a variable is out of its reach and out of this one's.
    expect(source).toMatch(/usePendingApprovalCount/);
    expect(source).not.toMatch(/queryKey:\s*\[\s*["'`]approvals["'`]\s*\]/);
  });
});
