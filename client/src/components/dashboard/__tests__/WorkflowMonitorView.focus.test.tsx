import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { MonitorFinding } from "../../../api/types";
import {
  deferred,
  dropFocus,
  finding,
  reasonElement,
  renderView,
} from "./workflowMonitorFixtures";

/**
 * Where focus goes around the monitor error pane's Retry, and — since
 * lg-milestone-that-2196 — which reads are allowed to wear the operator's own
 * pending state at all. The panes and their five states live in
 * WorkflowMonitorView.test.tsx, over the same harness.
 *
 * The file exists because the suite outgrew the 1200-line `.tsx` cap
 * `ts_organization_check.cjs` applies to a net-growing file, which does not
 * exempt test files. Both this harness's docstring and the sibling suite's
 * header already named this filename before it existed.
 *
 * Every test here starts from a FIRST-LOAD failure, which is this pane's only
 * regime: it is guarded `failure && !all.length`, and live holds 324 persisted
 * monitor findings, so `all.length` is 324+ the moment any read succeeds. The
 * cache is therefore cold — `data === undefined` — in every test below, which
 * is also why query-core cannot cancel one of these fetches with another.
 */

const monitorFindings = jest.fn();

jest.mock("../../../api/client", () => ({
  api: { monitorFindings: (ticketId?: string) => monitorFindings(ticketId) },
}));

beforeEach(() => monitorFindings.mockReset());

/**
 * Synchronize on a background poll having *started*, without reading the one
 * control the pane offers.
 *
 * lg-milestone-that-1464 landed the four tests below with
 * `await waitFor(() => expect(getByRole("button", { name: /retrying/i })).toBeDisabled())`
 * as this barrier, which asserts the exact state lg-milestone-that-2196 AC1
 * forbids: a read the operator never asked for must not disable or relabel
 * Retry. That barrier was the only thing in those tests that read the control's
 * state at all — every focus oracle in all four is untouched — so replacing it
 * is what makes them carry AC1 instead of contradicting it.
 *
 * The queryFn call count is the oracle instead. It says the fetch is in flight
 * without asking the DOM anything, and the deferred promise each caller holds
 * keeps it there, so the in-flight window the focus assertions straddle is
 * exactly as wide as it was. The two expectations here are the positive form of
 * what was removed: throughout that window the control is enabled and still
 * named `Retry`.
 */
async function pollInFlight(
  startPoll: () => Promise<unknown>,
  reads: number,
) {
  // Wrapped, not returned bare: `await` unwraps a promise returned from an
  // async function recursively, so handing `started` back directly would make
  // every caller block until the read it is about to assert on has settled.
  const started = startPoll();
  await waitFor(() => expect(monitorFindings).toHaveBeenCalledTimes(reads));
  // One flush, so the render the in-flight fetch produces has committed before
  // anything is read off it. The barrier this replaced waited on a *render*,
  // and the call count is satisfied synchronously by `startPoll` itself, so
  // without this the assertions below would run a tick earlier than they did.
  await act(async () => {});
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /retrying/i })).toBeNull();
  return { started };
}

/**
 * A first load that failed, with the error pane up and the cache cold.
 *
 * At module scope rather than inside one describe, because the mid-poll orders
 * at the bottom of the file start from the same pane.
 */
async function coldFailedPane() {
  const retry = await screen.findByRole("button", { name: "Retry" });
  // `!all.length` holds: no read has succeeded, so this is the only pane the
  // component can be showing, and query-core has no `data` to cancel against.
  expect(screen.getByRole("alert")).toContainElement(retry);
  expect(screen.queryByText("Nothing to report")).not.toBeInTheDocument();
  expect(monitorFindings).toHaveBeenCalledTimes(1);
  return retry;
}

/**
 * lg-milestone-that-1435 AC10 — focus, which native `disabled` takes away.
 *
 * Disabling a focused <button> blurs it in a real browser, so a keyboard
 * operator who pressed Enter on Retry is dumped on <body> for the length of the
 * read. jsdom does not do that, so the first test blurs explicitly: the blur is
 * standing in for the browser, not inventing a case.
 */
describe("focus around the retry", () => {
  it("returns focus to Retry when a retry settles with the failure still held", async () => {
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    const retry = await screen.findByRole("button", { name: /retry/i });
    await userEvent.click(retry);
    expect(retry).toBeDisabled();

    dropFocus();
    expect(document.activeElement).toBe(document.body);

    second.reject(new Error("monitor endpoint is down"));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled(),
    );
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /retry/i }),
    );
  });

  it("does not steal focus the operator moved elsewhere", async () => {
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView(null, <a href="#elsewhere">Elsewhere</a>);
    const retry = await screen.findByRole("button", { name: /retry/i });
    await userEvent.click(retry);

    const elsewhere = screen.getByRole("link", { name: "Elsewhere" });
    elsewhere.focus();
    expect(document.activeElement).toBe(elsewhere);

    second.reject(new Error("monitor endpoint is down"));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled(),
    );
    expect(document.activeElement).toBe(elsewhere);
  });

  it("does not take focus on a first load that failed without a retry", async () => {
    // The condition is a retry *settling* (isFetching true → false), not the
    // effect's first run. An effect that fires on mount would grab focus from
    // wherever the operator was the moment the monitor tab's first read failed.
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();

    await screen.findByText(/could not load monitor findings/i);
    expect(document.activeElement).toBe(document.body);
  });

  it("does not take focus when a background poll fails", async () => {
    // lg-milestone-that-1464 AC1. The trigger must be the operator's
    // activation, not the isFetching transition: `refetchInterval: 30_000`
    // produces the identical true → false transition with the failure held,
    // and query-core gates the tick on `focusManager.isFocused()`, so it
    // fires precisely when a stolen focus is felt. There is no click and no
    // keypress anywhere in this test.
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    await screen.findByText(/could not load monitor findings/i);
    // Nobody has clicked, so <body> is active by default — which is exactly
    // why the <body> guard cannot distinguish this case on its own.
    expect(document.activeElement).toBe(document.body);

    // Deferred, so the in-flight render actually commits. A synchronously
    // rejecting mock settles inside one act() flush, React never commits an
    // `isFetching: true` render, and the case passes vacuously.
    const { started } = await pollInFlight(startPoll, 2);

    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^retry$/i })).toBeEnabled(),
    );

    expect(document.activeElement).toBe(document.body);
  });

  it("does not take focus, or accumulate, across repeated failed polls", async () => {
    // lg-milestone-that-1464 AC7. Volume reaches this pane as cadence, not
    // rows: a sustained outage drives one failed poll every 30s, 20 across a
    // ten-minute gateway outage. "Correct on the first poll" is a different
    // claim from "invariant in the number of polls", so two consecutive
    // deferred failures are driven and both settles asserted.
    const polls = [deferred<MonitorFinding[]>(), deferred<MonitorFinding[]>()];
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(polls[0].promise)
      .mockReturnValueOnce(polls[1].promise);

    const { startPoll } = renderView();
    await screen.findByText(/could not load monitor findings/i);

    for (const [index, poll] of polls.entries()) {
      const { started } = await pollInFlight(startPoll, index + 2);

      await act(async () => {
        poll.reject(new Error(`the gateway refused the connection (${index})`));
        await started;
      });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /^retry$/i })).toBeEnabled(),
      );

      expect(document.activeElement).toBe(document.body);
      // Nothing accrues per poll: one alert container, one reason node, one
      // control — not an appended reason or a second announcement.
      expect(screen.getAllByRole("alert")).toHaveLength(1);
      expect(
        screen.getByRole("alert").querySelectorAll(".wm-error-reason"),
      ).toHaveLength(1);
      expect(
        screen.getAllByRole("button", { name: /^retry$/i }),
      ).toHaveLength(1);
    }
  });
});

/**
 * lg-milestone-that-1464, test-break — the arming itself, which the designed
 * oracles above never observe outliving the activation that set it.
 *
 * Both tests below hold against the three implementations that satisfy AC1,
 * AC7 and all four of AC2's focus tests and are still wrong:
 *
 * 1. Arm in `onClick`, never clear. Every designed test either never clicks
 *    (AC1, AC7, `:564`) or settles immediately after a click (`:518`, `:542`,
 *    `:679`), so a stale arm is invisible to all six — and it turns the very
 *    next background poll back into the defect this ticket exists to remove.
 * 2. Clear the arm only when the restore actually fires, i.e. inside the
 *    `document.activeElement === document.body` branch. `:542` settles with
 *    focus elsewhere and asserts only that the pressed control did not take
 *    focus; it cannot see that the arm survived the settle that declined.
 * 3. Clear it only on a settle that restored *and* re-arm per node. Same
 *    blindness, same consequence.
 *
 * AC3 asks for the arming to be cleared "unconditionally at the top of every
 * settled run", which is `useRestoreFocusOnSettle`'s own shape. These are the
 * oracles that make that word load-bearing rather than decorative.
 */
describe("the arming, adversarially", () => {
  it("does not take focus on a background poll that follows the operator's own retry", async () => {
    // The outage case in full: the operator presses Retry, it fails, focus is
    // returned to them — and 30 seconds later the poll fires with nobody
    // touching anything. An arm that is never cleared passes every designed
    // test and steals focus here, on every poll for the rest of the outage.
    const own = deferred<MonitorFinding[]>();
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(own.promise)
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    await userEvent.click(
      await screen.findByRole("button", { name: /retry/i }),
    );
    dropFocus();
    own.reject(new Error("monitor endpoint is down"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^retry$/i })).toBeEnabled(),
    );
    // The operator's own retry still gets focus back — asserted here so this
    // test cannot pass by the restore having been removed altogether.
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^retry$/i }),
    );

    // The browser blurs the control it disables, so the poll starts from the
    // same <body> the guard reads as "nobody has moved focus".
    dropFocus();
    expect(document.activeElement).toBe(document.body);

    const { started } = await pollInFlight(startPoll, 3);
    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^retry$/i })).toBeEnabled(),
    );

    expect(document.activeElement).toBe(document.body);
  });

  it("does not carry the arming past a retry that settled with focus elsewhere", async () => {
    // `:542`'s sequence, continued one poll further. The operator pressed
    // Retry and then moved focus away, so the settle declines — and an
    // implementation that clears the arm only when it fires is still armed
    // when the operator's focus later returns to <body> and the poll fails.
    const own = deferred<MonitorFinding[]>();
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(own.promise)
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView(null, <a href="#elsewhere">Elsewhere</a>);
    await userEvent.click(
      await screen.findByRole("button", { name: /retry/i }),
    );
    const elsewhere = screen.getByRole("link", { name: "Elsewhere" });
    elsewhere.focus();
    own.reject(new Error("monitor endpoint is down"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^retry$/i })).toBeEnabled(),
    );
    expect(document.activeElement).toBe(elsewhere);

    // The operator leaves that control (a dialog closing, a blur) and touches
    // nothing further; the next poll is the pane's own 30s timer.
    dropFocus();
    expect(document.activeElement).toBe(document.body);

    const { started } = await pollInFlight(startPoll, 3);
    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^retry$/i })).toBeEnabled(),
    );

    expect(document.activeElement).toBe(document.body);
  });
});

/**
 * lg-milestone-that-2196 — whose read is in flight, and which of the pane's
 * three operator-facing bindings is allowed to say so.
 *
 * `findings.isFetching` is true for the operator's own `refetch()` and for the
 * query's own `refetchInterval: 30_000` tick alike, so binding the Retry
 * control's `disabled` attribute and its `Retry`/`Retrying…` label to it made
 * the pane's only action go dead and announce a retry nobody asked for, once
 * every 30 seconds, for the length of an outage — and query-core gates the tick
 * on `focusManager.isFocused()`, so precisely while the operator is looking at
 * it.
 *
 * The flag these tests pin is therefore not a query state at all: it is the
 * lifetime of the promise the operator's own activation started. That matters
 * beyond tidiness. `armed && findings.isFetching` is reachable-looking and
 * wrong in both directions — between the click and the next render `isFetching`
 * can already be true from a poll, and when a retired fetch resolves it can
 * blip false and re-enable the control mid-retry.
 *
 * Every test here is a cold cache (`data === undefined`): the first load fails
 * and nothing ever succeeds. That is the pane's only regime, since it is
 * guarded `failure && !all.length` and live holds 324 persisted monitor
 * findings — and it is also why no fetch below can cancel another.
 */
describe("the operator's pending flag, and the poll that must not wear it", () => {
  it("leaves Retry enabled and named Retry for the whole of a failed background poll", async () => {
    // AC1. No click and no keypress anywhere: the second failure is driven
    // through the query itself, which is what `refetchInterval` does when it
    // elapses. Deferred, because a synchronously rejecting mock settles inside
    // one act() flush and React never commits an `isFetching: true` render at
    // all — against which this test would pass without the fix.
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    const retry = await coldFailedPane();

    const started = startPoll();
    await waitFor(() => expect(monitorFindings).toHaveBeenCalledTimes(2));

    // Throughout the in-flight window, not merely at one instant in it: the
    // read is still outstanding across both of these flushes, because nothing
    // has settled `poll` yet.
    for (let flush = 0; flush < 2; flush += 1) {
      expect(retry).toBeEnabled();
      expect(retry).toHaveAccessibleName("Retry");
      expect(screen.queryByRole("button", { name: /retrying/i })).toBeNull();
      await act(async () => {});
    }

    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });

    // And afterwards: the same node, never disabled, never relabelled.
    expect(screen.getByRole("button", { name: "Retry" })).toBe(retry);
    expect(retry).toBeEnabled();
    expect(retry).toHaveAccessibleName("Retry");
  });

  it("lets an interleaved background poll neither arm nor disarm the operator's own flag", async () => {
    // AC3, reworded by the spec stage from a state query-core cannot produce.
    // The original asked for a poll that settles *before* an in-flight operator
    // retry; with `data === undefined` a second fetch cannot cancel the first
    // (`query.js:183-193` gates the cancel branch on `state.data !== void 0 &&
    // fetchOptions?.cancelRefetch`) and the interval tick passes no
    // `fetchOptions` at all (`queryObserver.js:214-217`), so it dedupes onto
    // the in-flight retryer's promise. One fetch, one settle.
    //
    // (a) below asserts that dedupe directly rather than assuming it: this
    // test is the thing that fails loudly if a query-core upgrade changes the
    // reading the rewording rests on.
    const own = deferred<MonitorFinding[]>();
    const after = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(own.promise)
      .mockReturnValueOnce(after.promise);

    const { startPoll } = renderView();
    const retry = await coldFailedPane();

    await userEvent.click(retry);
    expect(retry).toBeDisabled();
    expect(retry).toHaveTextContent("Retrying…");
    expect(monitorFindings).toHaveBeenCalledTimes(2);

    // A poll fires while the operator's retry is still outstanding.
    const polled = startPoll();
    await act(async () => {});

    // (a) The queryFn was not called again — the dedupe is the mechanism.
    expect(monitorFindings).toHaveBeenCalledTimes(2);
    // (b) Nothing the operator did not start can disarm their flag: the
    // control stays theirs until their own promise settles.
    expect(retry).toBeDisabled();
    expect(retry).toHaveTextContent("Retrying…");

    await act(async () => {
      own.reject(new Error("monitor endpoint is down"));
      await polled;
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled(),
    );

    // (c) The reachable converse: a poll starting *after* that settle cannot
    // re-disable or relabel the control either. This is AC1 again, on the path
    // through a completed operator retry — the path on which an
    // `armed && isFetching` derivation looks right and is not.
    const { started } = await pollInFlight(startPoll, 3);
    await act(async () => {
      after.reject(new Error("the gateway refused the connection"));
      await started;
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("drives the disabled attribute, the label and the focus restore from one flag", async () => {
    // AC10. `useRestoreFocusOnSettle`'s own contract requires its `pending`
    // argument to be the flag that disables the control, so the DOM is already
    // operable when its effect runs — and two separately-derived flags would
    // let the restore window and the disabled window drift apart, which is the
    // class of defect this whole sibling set is made of. So all three are
    // asserted in one test, in both regimes, and this is what fails if any one
    // of them is re-derived on its own.
    const own = deferred<MonitorFinding[]>();
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(own.promise)
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    const retry = await coldFailedPane();

    // The operator's own read: all three fire.
    await userEvent.click(retry);
    expect(retry).toBeDisabled();
    expect(retry).toHaveTextContent("Retrying…");
    // The browser blurs the control it disables; jsdom does not, so this
    // stands in for it rather than inventing a case.
    dropFocus();
    expect(document.activeElement).toBe(document.body);

    await act(async () => {
      own.reject(new Error("monitor endpoint is down"));
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled(),
    );
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Retry" }),
    );

    // A read nobody asked for: none of the three.
    dropFocus();
    expect(document.activeElement).toBe(document.body);

    const { started } = await pollInFlight(startPoll, 3);
    expect(document.activeElement).toBe(document.body);

    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    await waitFor(() =>
      expect(reasonElement().textContent).toBe(
        "the gateway refused the connection.",
      ),
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(document.activeElement).toBe(document.body);
  });
});

/**
 * lg-milestone-that-2196, test-break — the orders the designed oracles never
 * take, all of which a stated criterion already names.
 *
 * Every test above drives the two reads in one order: either nobody clicks, or
 * the operator clicks *first* and a poll arrives afterwards. The reverse order
 * is the one the operator actually meets, because query-core gates the 30s tick
 * on `focusManager.isFocused()`: the poll is in flight precisely while they are
 * looking at the pane and reaching for the control. It is also the order that
 * separates a flag latched by the activation from one derived off the query,
 * since `armed && findings.isFetching` and the promise latch agree on every
 * sequence above and disagree here — `isFetching` is already true when the
 * click lands, so the derivation cannot tell the operator's window from the
 * poll's, and the window that disables the control and the window that restores
 * focus are no longer the same window.
 *
 * AC7 states the invariant these pin: Retry is enabled and named `Retry` at
 * every moment the pane is on screen, with exactly one exception — while the
 * operator's own activation is pending. "A poll cannot disable it" is only half
 * of that. The other half is that an activation taken mid-poll must still
 * disable it, still relabel it, still swallow the second click, and still hand
 * focus back on settle; an implementation that merely ignored a fetch it did not
 * start would satisfy every test above and strand the operator with a control
 * that never acknowledges them.
 *
 * AC9(e) names the other two outright — "tab order … must be invariant across a
 * background poll" and "Enter or Space on Retry must start a retry even with a
 * background poll in flight" — and nothing above presses a key at all.
 */
describe("the activation that lands mid-poll", () => {
  /** The pane, failed cold, with a background read outstanding. */
  async function pollOutstanding(startPoll: () => Promise<unknown>, reads: number) {
    const { started } = await pollInFlight(startPoll, reads);
    return { started, retry: screen.getByRole("button", { name: "Retry" }) };
  }

  it("latches the operator's flag on a retry pressed while a background poll is in flight", async () => {
    // AC7's exception, AC10's three bindings, in the order no designed test
    // takes. The click cannot call the queryFn again — with `data === undefined`
    // query-core dedupes it onto the in-flight retryer's promise
    // (`query.js:183-193`) — so the *only* observable that the operator's
    // activation was acknowledged is the flag: disabled, relabelled, and focus
    // handed back when that promise settles. A pending flag that refuses to
    // latch while a fetch it did not start is outstanding passes every test
    // above and leaves the operator pressing a control that never answers.
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    await coldFailedPane();
    const { started, retry } = await pollOutstanding(startPoll, 2);

    await userEvent.click(retry);

    // Deduped: one fetch, so the flag is the whole of the acknowledgement.
    expect(monitorFindings).toHaveBeenCalledTimes(2);
    expect(retry).toBeDisabled();
    expect(retry).toHaveTextContent("Retrying…");
    // AC2's guard is still the native attribute, in this order too.
    await userEvent.click(retry);
    expect(monitorFindings).toHaveBeenCalledTimes(2);

    // The browser blurs the control it disables.
    dropFocus();
    expect(document.activeElement).toBe(document.body);

    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled(),
    );
    // Their own activation's window closed on their own promise's settle, and
    // the restore fired — so the disabled window and the restore window were
    // one window, which is what AC10 asks for.
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Retry" }),
    );
    expect(screen.getByRole("button", { name: "Retry" })).toHaveAccessibleName(
      "Retry",
    );
  });

  it("keeps the pane's tab order invariant across a background poll", async () => {
    // AC9(e), verbatim: the path is the reason region, then Retry, and a read
    // nobody asked for may not change it. `disabled` does not grey a control
    // out, it removes it from the tab order — so a keyboard operator mid-Tab
    // traverse has Retry vanish from under them once every 30 seconds, and the
    // Tab that should have landed on it lands past the pane instead.
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    await coldFailedPane();
    const { started } = await pollOutstanding(startPoll, 2);
    const region = screen.getByRole("group", {
      name: "Reason the monitor could not be read",
    });
    const retry = screen.getByRole("button", { name: "Retry" });

    dropFocus();
    expect(document.activeElement).toBe(document.body);

    // Mid-poll, with the read still outstanding.
    await userEvent.tab();
    expect(document.activeElement).toBe(region);
    await userEvent.tab();
    expect(document.activeElement).toBe(retry);
    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(region);

    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled(),
    );

    // And after it: the same two stops in the same order, so the traverse is
    // invariant in the number of polls rather than merely correct between them.
    dropFocus();
    await userEvent.tab();
    expect(document.activeElement).toBe(region);
    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Retry" }),
    );
  });

  it("starts a retry from Enter, and from Space, with a background poll in flight", async () => {
    // AC9(e), the other half of the same sentence. A keyboard operator gets
    // nothing from `disabled`: the keypress is swallowed with no sound and no
    // focus ring moved, which is indistinguishable from the pane being dead.
    // Both keys are driven, because a <button> fires Enter on keydown and Space
    // on keyup, and one implementation detail away they are not the same path.
    const polls = [deferred<MonitorFinding[]>(), deferred<MonitorFinding[]>()];
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(polls[0].promise)
      .mockReturnValueOnce(polls[1].promise);

    const { startPoll } = renderView();
    await coldFailedPane();

    for (const [index, key] of [["{Enter}"], [" "]].entries()) {
      const { started, retry } = await pollOutstanding(startPoll, index + 2);

      retry.focus();
      expect(document.activeElement).toBe(retry);
      await userEvent.keyboard(key[0]!);

      // The activation landed: deduped onto the outstanding read, so again the
      // flag is the only thing that can say so.
      expect(monitorFindings).toHaveBeenCalledTimes(index + 2);
      expect(retry).toBeDisabled();
      expect(retry).toHaveTextContent("Retrying…");

      dropFocus();
      await act(async () => {
        polls[index]!.reject(new Error("the gateway refused the connection"));
        await started;
      });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled(),
      );
      // Enter on Retry started a retry *and* the operator is still on the
      // control, which is the whole point of restoring focus to a keyboard
      // operator rather than leaving them on <body>.
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Retry" }),
      );
    }
  });
});

/**
 * The one binding a behavioural oracle can otherwise not reach.
 *
 * In this pane's regime the operator's pending window is a subset of
 * `findings.isFetching`'s — one fetch, one settle, and the arming only ever
 * exists inside an activation's own window — so feeding
 * `useRestoreFocusOnSettle` the *query's* flag while the control's `disabled`
 * and label take the operator's one is observationally identical on every
 * sequence above. That re-derivation is exactly what AC10 forbids, and it
 * survived the whole suite when it was tried as a mutant.
 *
 * It has one reachable consequence, and this is it: the 30s tick landing in the
 * same flush as the operator's own settle. `findings.isFetching` then never
 * commits a false render — the poll's fetch begins before React observes the
 * retry's end — so a restore keyed to the query never fires, and the keyboard
 * operator who pressed Enter is left on <body> for the rest of the outage. The
 * operator's own flag does drop, because their promise did settle, which is the
 * whole reason AC10 names it as the hook's argument.
 */
describe("the operator's settle that a poll lands on top of", () => {
  it("returns focus on a retry whose settle is overlapped by the next poll", async () => {
    const own = deferred<MonitorFinding[]>();
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(own.promise)
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    const retry = await coldFailedPane();

    await userEvent.click(retry);
    expect(retry).toBeDisabled();
    // The browser blurs the control it disables; this is the operator who
    // pressed Enter and is now on <body>, waiting to be put back.
    dropFocus();
    expect(document.activeElement).toBe(document.body);

    // Their read ends and the pane's own timer fires on top of it, inside one
    // flush: no render between the two in which nothing is fetching.
    let started: Promise<unknown> | undefined;
    await act(async () => {
      own.reject(new Error("monitor endpoint is down"));
      // Drain the retryer's own microtasks so the query is idle and the tick
      // starts a genuinely new fetch, rather than deduping onto theirs.
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      started = startPoll();
    });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled(),
    );
    // The poll is still outstanding, and still wears nothing of theirs.
    expect(monitorFindings).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("button", { name: "Retry" })).toHaveAccessibleName(
      "Retry",
    );
    // And they are back on the control they activated, not on <body>.
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Retry" }),
    );

    await act(async () => {
      poll.reject(new Error("the gateway refused the connection"));
      await started;
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });
});

/**
 * lg-milestone-that-2197 — the settle that *succeeds*.
 *
 * Everything above is the failed settle: the pane is still on screen, the
 * failure is still held, and the control the operator pressed is still in the
 * tree, so "put them back" means the same element. A retry that works is the
 * opposite shape, and it is the common one, because an operator presses Retry
 * expecting it to work. `useHeldFailure` clears, `failure && !all.length` stops
 * matching, the pane unmounts, and the node the hook armed is detached — which
 * `useRestoreFocusOnSettle` declines on, correctly, since focusing a detached
 * node moves focus to the document rather than restoring it. Nothing else puts
 * focus anywhere, so the operator who just fixed the outage is on <body>,
 * paying the full Tab traverse of the app chrome to reach the findings they
 * were waiting for.
 *
 * The landing is decided per rendered shape (D1, D2), and all three shapes a
 * successful settle out of this pane can reach are driven below:
 *
 *   (a) `[]`                        → "Nothing to report"   → that pane itself
 *   (b) findings all on done tickets → `nothingShown`        → "Needs attention"
 *   (c) findings on a live ticket    → the full grouped list → "Needs attention"
 *
 * Shape (a) is unreachable on the live database (418 persisted findings, never
 * zero), so this oracle is the only thing that covers it — which is exactly
 * why the `[]` resolution is driven explicitly rather than left to the two
 * shapes a real workspace produces.
 *
 * Oracles (b) and (c) find the scope button by **accessible name**, never by
 * position: the implementation reaches it through `.wm-scope` plus
 * `querySelector("button")`, which returns `Needs attention` only because it is
 * first in that group, so anything inserted ahead of it must go red here rather
 * than silently change where the operator lands.
 */
describe("the landing after a retry that succeeds", () => {
  /**
   * A keyboard operator on the cold failed pane, mid-retry.
   *
   * Enter on the focused control, then the blur a real browser performs when
   * the native `disabled` attribute goes on — jsdom does neither, so
   * `dropFocus()` stands in for the browser rather than inventing a case. The
   * returned promise is the read the caller settles.
   */
  async function retryFromTheKeyboard() {
    const retry = await coldFailedPane();
    retry.focus();
    expect(document.activeElement).toBe(retry);
    await userEvent.keyboard("{Enter}");
    expect(retry).toBeDisabled();
    dropFocus();
    expect(document.activeElement).toBe(document.body);
    return retry;
  }

  it("lands on the “Nothing to report” pane when the retry succeeds with nothing to report", async () => {
    // AC2 (a) / D2. No scope control and no findings list exist in this shape,
    // so the pane itself is the target: `role="group"` plus an accessible name
    // plus `tabIndex={-1}`, which is the pattern this same file already uses
    // for the error reason. Focusing it reads the group's name and then
    // "Nothing to report", which is the whole answer.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    const retry = await retryFromTheKeyboard();

    await act(async () => {
      second.resolve([]);
    });

    await screen.findByText("Nothing to report");
    expect(retry.isConnected).toBe(false);
    const pane = screen.getByRole("group", { name: "Monitor findings" });
    expect(document.activeElement).toBe(pane);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("lands on the Needs attention scope button when every restored finding is on a finished ticket", async () => {
    // AC2 (b) / D1 — `nothingShown`: the header and the scope control are
    // present, every <Section> is absent. The scope button is still the first
    // interactive element of the restored surface, and its name carries the
    // recovery and the volume in one, so the landing does not move just
    // because this shape renders no rows.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    await retryFromTheKeyboard();

    await act(async () => {
      second.resolve([finding({ ticket_state: "done" })]);
    });

    await screen.findByText("Nothing on live tickets");
    const scope = screen.getByRole("button", { name: /^Needs attention/ });
    expect(document.activeElement).toBe(scope);
    expect(document.activeElement).not.toBe(document.body);
    // The default scope is untouched by the landing: it is still "attention",
    // which is why this shape rendered at all.
    expect(scope).toHaveAttribute("aria-pressed", "true");
  });

  it("lands on the Needs attention scope button when the retry restores the findings list", async () => {
    // AC2 (c) / D1 — the common recovery. On the live database the default
    // scope renders 55 findings over 50 live tickets; one live ticket is the
    // same shape, and the shape is what decides the landing.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    await retryFromTheKeyboard();

    await act(async () => {
      second.resolve([finding()]);
    });

    await screen.findByText("Fix the flaky review");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^Needs attention/ }),
    );
  });

  it("does not take focus the operator moved elsewhere while their retry was in flight", async () => {
    // The <body> guard is unconditional and applies to the landing path too,
    // which is lg-milestone-that-1464's rule intact: focus the operator moved
    // themselves mid-flight is theirs, and a settle that yanked it onto the
    // restored surface would be taking focus rather than restoring it. Note
    // this is the *successful* settle — the pane the operator was on is gone,
    // and that still does not make their own focus available.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView(null, <a href="#elsewhere">Elsewhere</a>);
    const retry = await coldFailedPane();
    await userEvent.click(retry);

    const elsewhere = screen.getByRole("link", { name: "Elsewhere" });
    elsewhere.focus();
    expect(document.activeElement).toBe(elsewhere);

    await act(async () => {
      second.resolve([finding()]);
    });

    await screen.findByText("Fix the flaky review");
    expect(document.activeElement).toBe(elsewhere);
  });

  it("moves nothing when a background poll succeeds and clears the pane", async () => {
    // AC3. The trigger is the operator's activation, not the settle: a poll
    // the operator never asked for produces the identical "pane unmounts, armed
    // node detaches" transition, and query-core gates the tick on
    // `focusManager.isFocused()`, so it fires precisely when a stolen focus is
    // felt. There is no click and no keypress anywhere in this test.
    //
    // This must hold because nothing was armed — `rememberRetry` is called only
    // from the Retry `onClick`, so `activated.current` is null and the effect
    // returns on the first of its three facts, before a landing can be
    // consulted. An implementation that needs a *second* arming ref or a new
    // guard to pass this has put the behaviour in the wrong place.
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    await coldFailedPane();
    // Nobody has clicked, so <body> is active by default — which is exactly
    // why the <body> guard cannot distinguish this case on its own.
    expect(document.activeElement).toBe(document.body);

    const { started } = await pollInFlight(startPoll, 2);

    await act(async () => {
      poll.resolve([finding()]);
      await started;
    });

    await screen.findByText("Fix the flaky review");
    expect(screen.getByRole("button", { name: /^Needs attention/ })).toBeInTheDocument();
    expect(document.activeElement).toBe(document.body);
  });
});

/**
 * UX2 and AC4 — the landing is only useful if the next action is forward of it.
 *
 * A target that needs Shift+Tab to reach the findings, or that sits after them,
 * costs the traverse this ticket exists to remove. These two drive the tab
 * order out of each decided landing rather than asserting an attribute and
 * calling it reachable.
 */
describe("the restored surface, from the landing", () => {
  it("reaches the findings by Tab alone from the scope button", async () => {
    // UX2: "Needs attention" → "Everything" → Refresh → the first TicketLink.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    const retry = await coldFailedPane();
    await userEvent.click(retry);
    dropFocus();

    await act(async () => {
      second.resolve([finding()]);
    });
    await screen.findByText("Fix the flaky review");

    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^Needs attention/ }),
    );
    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^Everything/ }),
    );
    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Refresh monitor findings" }),
    );
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole("link"));
  });

  it("keeps the “Nothing to report” pane named, and out of the tab order", async () => {
    // AC4, for the one shape where the landing is a container rather than a
    // control. It must be focusable programmatically and skipped by Tab:
    // `tabIndex={-1}`, so the operator's own traverse of the page does not
    // acquire a stop that answers nothing. This shape renders no control at
    // all, so "forward rather than back into the chrome" is discharged by the
    // pane not holding focus against a Tab — the chrome link below sits
    // *before* the view, and Tab out of the pane must not land on it.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView(null, <a href="#chrome">Chrome</a>);
    const retry = await coldFailedPane();
    await userEvent.click(retry);
    dropFocus();

    await act(async () => {
      second.resolve([]);
    });
    await screen.findByText("Nothing to report");

    const pane = screen.getByRole("group", { name: "Monitor findings" });
    expect(document.activeElement).toBe(pane);
    expect(pane).toHaveAttribute("tabindex", "-1");
    // Tabbing out of it does not go back to the chrome that precedes the view.
    await userEvent.tab();
    expect(document.activeElement).not.toBe(
      screen.getByRole("link", { name: "Chrome" }),
    );
    // And the operator's own forward traverse never stops on it.
    screen.getByRole("link", { name: "Chrome" }).focus();
    await userEvent.tab();
    expect(document.activeElement).not.toBe(pane);
  });
});

/**
 * The same landing, adversarially — the orders a single happy-path settle does
 * not reach.
 *
 * Each of these three is a state the operator produces by ordinary use (a retry
 * that fails before it works, re-reading the reason while the retry runs, the
 * 30s poll arriving after the recovery), and each goes green under a correct
 * implementation while catching a different wrong one: a one-shot landing, a
 * landing gated on a focus snapshot taken at activation, and a landing that
 * re-fires on a settle nobody armed.
 */
describe("the landing, adversarially", () => {
  it("arms again, so the landing fires on the retry that finally works", async () => {
    // Order dependency: the first settle consumes the arming and restores
    // focus to Retry (lg-milestone-that-1435), and the second must land. An
    // implementation that latches "already landed", or arms once per mount
    // rather than per press, is green on a single retry and dead on the
    // second — which on a real outage is the common one.
    const second = deferred<MonitorFinding[]>();
    const third = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(third.promise);

    renderView();
    const first = await coldFailedPane();
    await userEvent.click(first);
    dropFocus();

    await act(async () => {
      second.reject(new Error("the gateway refused the connection"));
    });
    const again = await waitFor(() => {
      const control = screen.getByRole("button", { name: "Retry" });
      expect(control).toBeEnabled();
      return control;
    });
    // The failed settle is lg-milestone-that-1464's, unchanged: the pane is
    // still up and focus is back on the control the operator pressed.
    expect(document.activeElement).toBe(again);

    await userEvent.click(again);
    expect(again).toBeDisabled();
    dropFocus();

    await act(async () => {
      third.resolve([finding()]);
    });

    await screen.findByText("Fix the flaky review");
    expect(again.isConnected).toBe(false);
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^Needs attention/ }),
    );
  });

  it("lands when the operator's own focus was inside the pane the retry replaced", async () => {
    // The <body> guard is read at the settle, not snapshotted at activation.
    // An operator who presses Retry and then Shift+Tabs back to re-read the
    // reason has moved their focus — but into the pane that is about to be
    // replaced, so the browser drops them on <body> when it unmounts and the
    // landing is owed. An implementation that remembered "they moved focus"
    // from activation time declines this settle and costs the traverse.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    const retry = await coldFailedPane();
    await userEvent.click(retry);

    const reason = reasonElement() as HTMLElement;
    reason.focus();
    expect(document.activeElement).toBe(reason);

    await act(async () => {
      second.resolve([finding()]);
    });

    await screen.findByText("Fix the flaky review");
    // The browser's own doing: the element holding focus left the tree.
    expect(reason.isConnected).toBe(false);
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^Needs attention/ }),
    );
  });

  it("does not land a second time when a poll settles over the restored surface", async () => {
    // Cadence, not rows: once the operator has recovered, `refetchInterval:
    // 30_000` keeps settling behind them — 20 times across a ten-minute read.
    // The operator has moved on to "Everything" by then, and a landing that is
    // remembered rather than consumed drags them back to "Needs attention"
    // every half minute. Nothing is armed here, so nothing may move.
    const second = deferred<MonitorFinding[]>();
    const poll = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(poll.promise);

    const { startPoll } = renderView();
    const retry = await coldFailedPane();
    await userEvent.click(retry);
    dropFocus();

    await act(async () => {
      second.resolve([finding()]);
    });
    await screen.findByText("Fix the flaky review");
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^Needs attention/ }),
    );

    const everything = screen.getByRole("button", { name: /^Everything/ });
    await userEvent.click(everything);
    expect(document.activeElement).toBe(everything);

    const started = startPoll();
    await act(async () => {
      poll.resolve([finding()]);
      await started;
    });

    expect(monitorFindings).toHaveBeenCalledTimes(3);
    expect(document.activeElement).toBe(everything);
  });
});
