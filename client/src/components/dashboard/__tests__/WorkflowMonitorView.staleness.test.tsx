import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "../../../api/http";
import type { MonitorFinding } from "../../../api/types";
import {
  deferred,
  dropFocus,
  finding,
  reasonElement,
  renderView,
} from "./workflowMonitorFixtures";

/**
 * The data-present regime of WorkflowMonitorView — a read that fails over
 * findings already on screen, and the staleness notice that reports it.
 *
 * Split out of WorkflowMonitorView.test.tsx when the suite reached the
 * 1200-line cap (lg-milestone-that-1464). The error pane itself, and where
 * focus goes around its Retry, stay there: AC1, AC2, AC6 and AC8 of 1435, 1454
 * and 1464 all name that file for them.
 */

const monitorFindings = jest.fn();

jest.mock("../../../api/client", () => ({
  api: { monitorFindings: (ticketId?: string) => monitorFindings(ticketId) },
}));

beforeEach(() => monitorFindings.mockReset());

/**
 * lg-milestone-that-1454. The regime the block above deliberately leaves alone:
 * a read that fails when `findings.data` is ALREADY present.
 *
 * query-core keeps `data` and sets `status: "error"` when a refetch fails over a
 * query that has loaded once, and `useHeldFailure` sets its hold unconditionally
 * on `isError`, so `failure` is truthy here too. The defect is that the failure
 * branch sat above the list render with no `!all.length` qualifier, so one failed
 * background poll replaced the findings the operator was reading — and the
 * header's own control said "Refreshing…" while it happened.
 *
 * Both tests below fail the SECOND read, which is the only way to reach the
 * data-present regime. Each asserts the notice AND the absence of the pane (or
 * the reverse), because either half alone passes under an implementation that
 * got the regime backwards.
 */
describe("a failed re-read over findings already on screen", () => {
  it("keeps the list and reports the failed read as one polite notice", async () => {
    // AC1 + AC2 + AC4(a). The second read is started by the header's own
    // Refresh, which the component cannot distinguish from the 30s poll: both
    // go through `findings.refetch()` and land on the same held failure.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();
    await screen.findByText("Stage thrash");

    await userEvent.click(
      screen.getByRole("button", { name: "Refresh monitor findings" }),
    );

    const notice = await screen.findByTestId("wm-refresh-failed");
    // All three together: the list survived, the operator was told, and they
    // were told without the alert that replaces everything.
    expect(screen.getByText("Stage thrash")).toBeInTheDocument();
    expect(screen.getByText(/attempted 12x/)).toBeInTheDocument();
    expect(notice).toHaveTextContent(
      "This list may be out of date — the last refresh failed.",
    );
    expect(notice).toHaveTextContent("monitor endpoint is down.");
    expect(notice).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/could not load monitor findings/i),
    ).not.toBeInTheDocument();
  });

  it("still shows the full error pane when the read that failed had nothing to lose", async () => {
    // AC3 + AC4(b). A successful EMPTY read followed by a failure is NOT the
    // data-present regime: nothing was lost, so "Nothing to report" would be a
    // health claim the monitor cannot make, and the pane's own sentence is the
    // true one. The second read is the poll rather than a click because the
    // `!all.length` branch renders no header and therefore no Refresh control.
    monitorFindings
      .mockResolvedValueOnce([])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    const { poll } = renderView();
    await screen.findByText("Nothing to report");

    await poll();

    expect(
      await screen.findByText(/could not load monitor findings/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(reasonElement().textContent).toBe("monitor endpoint is down.");
    expect(screen.queryByTestId("wm-refresh-failed")).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing to report")).not.toBeInTheDocument();
  });

  it('shows the notice beside "Nothing on live tickets", because that block is about the scope filter', async () => {
    // AC3's coexistence case. `nothingShown` and `failure` can be true at the
    // same moment: every finding is on a finished ticket AND the last read
    // failed. `all.length > 0`, so this is still the data-present regime, and
    // the two statements are about different things — one about the scope
    // filter, one about the read. Neither may be tidied into an either/or.
    monitorFindings
      .mockResolvedValueOnce([finding({ ticket_state: "done" })])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();
    await screen.findByText("Nothing on live tickets");

    await userEvent.click(
      screen.getByRole("button", { name: "Refresh monitor findings" }),
    );

    const notice = await screen.findByTestId("wm-refresh-failed");
    expect(screen.getByText("Nothing on live tickets")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Notice first: it qualifies everything below it, including that block.
    expect(
      notice.compareDocumentPosition(
        screen.getByText("Nothing on live tickets"),
      ),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });
});

/**
 * lg-milestone-that-1454, adversarially. The block above proves the notice can
 * appear and the pane can stay; this one proves the notice cannot appear for
 * any other reason, cannot say the wrong thing, and cannot grow into a second
 * error surface.
 *
 * Every test here is written against a specific wrong implementation that the
 * three tests above pass unchanged:
 *   - a notice rendered unconditionally in the list return (nothing above
 *     asserts its ABSENCE on a healthy read, and the one test that asserts no
 *     `role="status"` is in the pane regime, which returns earlier);
 *   - a hand-written `err instanceof Error ? err.message : …` ternary in place
 *     of `describeError`, which is identical on `new Error("…")` — the only
 *     error shape the tests above use — and loses an `ApiError` status line and
 *     the fallback;
 *   - the notice placed inside `.wm-header`, which is still before "Nothing on
 *     live tickets" and so still satisfies the ordering assertion above;
 *   - a notice guarded on `!findings.isFetching`, which is true at every moment
 *     the tests above assert on, and false for the whole of the next read;
 *   - a notice carrying its own Retry or dismiss, which changes no role and no
 *     text content.
 */
describe("the staleness notice, adversarially", () => {
  const notice = () => screen.getByTestId("wm-refresh-failed");
  const refresh = () =>
    screen.getByRole("button", { name: "Refresh monitor findings" });

  it("is absent on a healthy read, and leaves again when a read succeeds", async () => {
    // AC2's render condition is `failure`, and `useHeldFailure` clears only on a
    // read that settles without failing. A notice with no condition, or one
    // that never clears, tells the operator their current list is stale — the
    // same lie as the pane, in a smaller font.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockResolvedValue([finding()]);

    renderView();
    await screen.findByText("Stage thrash");
    expect(screen.queryByTestId("wm-refresh-failed")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await userEvent.click(refresh());
    await screen.findByTestId("wm-refresh-failed");
    // Exactly one, however many places could have rendered it.
    expect(screen.getAllByTestId("wm-refresh-failed")).toHaveLength(1);

    await userEvent.click(refresh());

    await waitFor(() =>
      expect(screen.queryByTestId("wm-refresh-failed")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Stage thrash")).toBeInTheDocument();
  });

  it("recovers an ApiError's status line, which a hand-written ternary throws away", async () => {
    // AC2 names `describeError` for this reason, and the pane has the same test
    // at `new ApiError(502, "")`. A 503 with an empty body reaches the ternary
    // as `err.message === ""`, so the operator is told a refresh failed and
    // nothing at all about why.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue(new ApiError(503, "", undefined));

    renderView();
    await screen.findByText("Stage thrash");

    await userEvent.click(refresh());

    await waitFor(() =>
      expect(notice()).toHaveTextContent("Request failed (503)."),
    );
    expect(notice()).toHaveTextContent(
      "This list may be out of date — the last refresh failed.",
    );
    expect(screen.getByText("Stage thrash")).toBeInTheDocument();
  });

  it("falls back to the monitor's own sentence when the rejection is not an Error", async () => {
    // A rejected promise carries whatever was thrown. `describeError`'s third
    // branch and the fallback AC2 names are what stands between that and a
    // notice whose second sentence is empty or "[object Object]".
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue({ upstream: "ECONNRESET" });

    renderView();
    await screen.findByText("Stage thrash");

    await userEvent.click(refresh());

    await waitFor(() =>
      expect(notice()).toHaveTextContent(
        "The monitor endpoint did not answer.",
      ),
    );
    expect(notice().textContent).not.toContain("object Object");
  });

  it("sits in the column under the header, not inside the header's own row", async () => {
    // AC2 is explicit about the parent: `.wm-header` is a space-between,
    // wrapping row holding the scope group and Refresh, and a sentence placed
    // in it fights both at every width. The ordering assertion in the block
    // above cannot see the difference, because a child of the header is also
    // before everything that follows it.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();
    await screen.findByText("Stage thrash");

    await userEvent.click(refresh());
    const stale = await screen.findByTestId("wm-refresh-failed");

    expect(stale.closest(".wm-header")).toBeNull();
    expect(stale.parentElement).toHaveClass("wm-root");
    expect(stale.tagName).toBe("P");
    expect(stale).toHaveClass("stale-read-notice");
    const header = refresh().closest("header");
    expect(header).not.toBeNull();
    expect(header!.compareDocumentPosition(stale)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(stale.compareDocumentPosition(screen.getByText("By ticket"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("stays on screen for the whole of the next read, beside the header saying Refreshing…", async () => {
    // UI-DESIGN / IN-FLIGHT. `useHeldFailure` clears on a read that SETTLES
    // without failing, so the list is still stale while the read that might fix
    // it is in flight — which is exactly what the header was already promising
    // and could not deliver. A notice guarded on `!findings.isFetching` passes
    // every other test in this file and blinks out for the length of each read.
    const third = deferred<MonitorFinding[]>();
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(third.promise)
      .mockResolvedValue([finding()]);

    renderView();
    await screen.findByText("Stage thrash");

    await userEvent.click(refresh());
    await screen.findByTestId("wm-refresh-failed");

    await userEvent.click(refresh());

    expect(refresh()).toBeDisabled();
    expect(refresh()).toHaveTextContent("Refreshing…");
    expect(notice()).toBeInTheDocument();
    expect(notice()).toHaveTextContent("monitor endpoint is down.");
    expect(screen.getByText("Stage thrash")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    third.resolve([finding()]);
    await waitFor(() =>
      expect(screen.queryByTestId("wm-refresh-failed")).not.toBeInTheDocument(),
    );
  });

  it("adds no control of its own: Refresh stays the one action and nothing dismisses it", async () => {
    // UI-DESIGN / ACTION. A second Retry duplicates the header's control; a
    // dismiss asserts the list is current, which only a successful read can
    // make true. Neither changes the notice's role or its text, so nothing
    // above this line would notice either arriving.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();
    await screen.findByText("Stage thrash");

    await userEvent.click(refresh());
    const stale = await screen.findByTestId("wm-refresh-failed");

    expect(
      stale.querySelector("button, a, input, [role='button'], [tabindex]"),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /retry/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /dismiss|close|hide/i }),
    ).not.toBeInTheDocument();
    // Scope group's two buttons and Refresh, and nothing new.
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("keeps the operator's selected scope, and takes no focus when the read settles", async () => {
    // AC1 names the scope as well as the list, and UI-DESIGN / FOCUS says
    // nothing in this change moves focus: the `retryRef` return belongs to the
    // pane and must not be extended here. Focus is dropped to <body> first,
    // standing in for the browser disabling the control the operator pressed —
    // the one state in which that effect fires.
    monitorFindings
      .mockResolvedValueOnce([
        finding(),
        finding({
          ticket_id: "t2",
          ticket_external_id: "lor-x-2",
          ticket_state: "done",
          ticket_title: "Already finished",
        }),
      ])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();
    await screen.findByText("Stage thrash");
    await userEvent.click(screen.getByRole("button", { name: /^Everything/ }));
    expect(screen.getAllByText("Already finished").length).toBeGreaterThan(0);

    await userEvent.click(refresh());
    dropFocus();
    const stale = await screen.findByTestId("wm-refresh-failed");

    expect(screen.getByRole("button", { name: /^Everything/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("button", { name: /^Needs attention/ }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(screen.getAllByText("Already finished").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fix the flaky review").length).toBeGreaterThan(
      0,
    );
    await waitFor(() => expect(refresh()).toBeEnabled());
    expect(document.activeElement).toBe(document.body);
    expect(stale).not.toHaveAttribute("tabindex");
  });

  it("stays one notice across repeated failed polls, and never escalates to the pane", async () => {
    // Determinism, and the regime's own stickiness: the defect is on a 30s
    // timer, so the second, third and fourth consecutive failure are the
    // normal case, not an edge one. The list must survive all of them and the
    // notice must not accumulate.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue(new Error("monitor endpoint is down"));

    const { poll } = renderView();
    await screen.findByText("Stage thrash");

    for (let i = 0; i < 3; i += 1) {
      await poll();
      await waitFor(() =>
        expect(screen.getAllByTestId("wm-refresh-failed")).toHaveLength(1),
      );
      expect(monitorFindings).toHaveBeenCalledTimes(i + 2);
      expect(screen.getByText("Stage thrash")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    }
    expect(monitorFindings).toHaveBeenCalledTimes(4);
  });
});
