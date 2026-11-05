import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "fs";
import path from "path";

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
 * All five states are pinned, because the one that matters most here is the one
 * a panel like this normally gets wrong: an error must not render as an empty
 * list. "The monitor has nothing to report" and "we could not ask the monitor"
 * are opposite facts about the pipeline, and collapsing them into one blank
 * pane is the failure this whole surface exists to stop.
 *
 * Where focus goes across the retry lives in WorkflowMonitorView.focus.test.tsx.
 */

const monitorFindings = jest.fn();

jest.mock("../../../api/client", () => ({
  api: { monitorFindings: (ticketId?: string) => monitorFindings(ticketId) },
}));

beforeEach(() => monitorFindings.mockReset());
beforeEach(() => monitorFindings.mockReset());

it("asks for every finding, with no ticket id", async () => {
  monitorFindings.mockResolvedValue([finding()]);

  renderView();

  await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
  expect(monitorFindings).toHaveBeenCalledWith(undefined);
});

it("groups recurrence across tickets and says how many it spans", async () => {
  monitorFindings.mockResolvedValue([
    finding({ ticket_id: "t1", ticket_external_id: "lor-x-1" }),
    finding({ ticket_id: "t2", ticket_external_id: "lor-x-2" }),
    finding({ ticket_id: "t3", ticket_external_id: "lor-x-3" }),
  ]);

  renderView();

  expect(
    await screen.findByRole("heading", { name: "Recurring across tickets" }),
  ).toBeInTheDocument();
  expect(screen.getByText("3 tickets")).toBeInTheDocument();
});

it("names each ticket and links to it", async () => {
  monitorFindings.mockResolvedValue([finding({ ticket_id: "t1" })]);

  renderView();

  const link = await screen.findByRole("link", {
    name: /lor-x-1.*Fix the flaky review/,
  });
  expect(link).toHaveAttribute("href", expect.stringContaining("/tickets/t1"));
});

it("folds the same condition from several runs of one ticket into one line", async () => {
  monitorFindings.mockResolvedValue([finding(), finding(), finding()]);

  renderView();

  expect(await screen.findByText(/· 3 runs/)).toBeInTheDocument();
  expect(screen.getAllByText(/attempted 12x/)).toHaveLength(1);
});

it("hides findings on finished tickets until asked for everything", async () => {
  monitorFindings.mockResolvedValue([
    finding({ ticket_id: "live", ticket_external_id: "lor-live-1" }),
    finding({
      ticket_id: "old",
      ticket_external_id: "lor-old-2",
      ticket_state: "done",
    }),
  ]);

  renderView();

  expect(await screen.findByText("lor-live-1")).toBeInTheDocument();
  expect(screen.queryByText("lor-old-2")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /everything/i }));

  expect(screen.getAllByText("lor-old-2").length).toBeGreaterThan(0);
});

it("says so when every finding is on a finished ticket, rather than looking empty", async () => {
  monitorFindings.mockResolvedValue([finding({ ticket_state: "done" })]);

  renderView();

  expect(
    await screen.findByText("Nothing on live tickets"),
  ).toBeInTheDocument();
});

it("puts the selected ticket's own findings first", async () => {
  monitorFindings.mockResolvedValue([
    finding({ ticket_id: "other", ticket_external_id: "lor-other-9" }),
    finding({ ticket_id: "mine", ticket_external_id: "lor-mine-4" }),
  ]);

  renderView("mine");

  const section = await screen.findByRole("heading", {
    name: "On this ticket",
  });
  expect(section.parentElement).toHaveTextContent("lor-mine-4");
  expect(section.parentElement).not.toHaveTextContent("lor-other-9");
});

it("never renders the occurrences count, which counts sweep ticks", async () => {
  // The live endpoint returns 5989 for every finding: two days of the reconcile
  // sweep re-observing the same condition. Rendering it reads as 5989 thrashes.
  monitorFindings.mockResolvedValue([
    finding({ occurrences: 5989, first_seen: "2026-09-09T00:33:19Z" }),
  ]);

  renderView();

  await screen.findByText("Stage thrash");
  expect(screen.queryByText(/5989/)).not.toBeInTheDocument();
});

it("names the stage alongside the condition", async () => {
  monitorFindings.mockResolvedValue([finding()]);

  renderView();

  expect(await screen.findByText("script_review")).toBeInTheDocument();
  expect(screen.getByText(/attempted 12x/)).toBeInTheDocument();
});

it("shows a loading state rather than an empty pane while fetching", async () => {
  monitorFindings.mockReturnValue(new Promise(() => {}));

  renderView();

  expect(screen.getByText(/loading findings/i)).toBeInTheDocument();
  expect(screen.queryByText(/nothing to report/i)).not.toBeInTheDocument();
});

it("distinguishes a failure to ask from having nothing to report", async () => {
  monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));

  renderView();

  expect(
    await screen.findByText(/could not load monitor findings/i),
  ).toBeInTheDocument();
  expect(screen.getByText(/monitor endpoint is down/)).toBeInTheDocument();
  // Exact string, not a pattern: the error copy deliberately contains the words
  // "having nothing to report" while arguing it is not that, and a loose regex
  // here would match the very sentence drawing the distinction.
  expect(screen.queryByText("Nothing to report")).not.toBeInTheDocument();
});

/**
 * lg-milestone-that-1434 AC12, re-pointed by lg-milestone-that-1435 AC8: the
 * reason now stands in its own element instead of being spliced in front of the
 * sentence we own, so the oracle reads that element's own text. `endSentence`
 * is still what terminates it — the message is foreign text and ends where it
 * ends, which is why the *fallback* ending in a period hid the original defect
 * from every test.
 */
it("terminates the server's message, which stands as its own sentence", async () => {
  monitorFindings.mockRejectedValue(new Error("Failed to fetch"));

  renderView();

  await screen.findByText(/could not load monitor findings/i);
  expect(reasonElement().textContent).toBe("Failed to fetch.");
  // The sentence we own is a separate element, so nothing can run into it.
  expect(reasonElement().textContent).not.toContain("Nothing is known about");
  expect(screen.getByText(/Nothing is known about/).textContent).not.toContain(
    "Failed to fetch",
  );
});

it("does not double the period when the message already ends in one", async () => {
  monitorFindings.mockRejectedValue(
    new Error("The monitor endpoint did not answer."),
  );

  renderView();

  await screen.findByText(/could not load monitor findings/i);
  expect(reasonElement().textContent).toBe(
    "The monitor endpoint did not answer.",
  );
});

it("offers a retry that re-asks", async () => {
  monitorFindings
    .mockRejectedValueOnce(new Error("transient"))
    .mockResolvedValue([finding()]);

  renderView();

  const retry = await screen.findByRole("button", { name: /retry/i });
  await userEvent.click(retry);

  expect(await screen.findByText("Stage thrash")).toBeInTheDocument();
});

it("says what would be here when there is genuinely nothing", async () => {
  monitorFindings.mockResolvedValue([]);

  renderView();

  expect(await screen.findByText(/nothing to report/i)).toBeInTheDocument();
  expect(screen.getByText(/runs on the reconcile timer/i)).toBeInTheDocument();
});

it("gives the refresh control a name a screen reader can read", async () => {
  monitorFindings.mockResolvedValue([finding()]);

  renderView();

  expect(
    await screen.findByRole("button", { name: /refresh monitor findings/i }),
  ).toBeInTheDocument();
});

it("offers no resolve or dismiss control, because the monitor only reports", async () => {
  monitorFindings.mockResolvedValue([finding()]);

  renderView();

  await screen.findByText("Stage thrash");
  expect(
    screen.queryByRole("button", { name: /resolve|dismiss|fix/i }),
  ).not.toBeInTheDocument();
});

/**
 * lg-milestone-that-1435 — the error pane must survive its own retry.
 *
 * query-core puts a failed-with-no-data query back to `status: "pending"` and
 * clears `error` on `refetch()` (5.101.2 `query.ts:719-728`), so a branch
 * guarded on `isError` and placed *after* the `isPending` branch unmounts the
 * moment the operator presses its own Retry: they are dropped onto "Loading
 * findings…" and lose the reason they were retrying, and the in-flight label
 * and the double-click guard inside the branch can never be reached.
 *
 * Every test below therefore starts from a FIRST-LOAD failure
 * (`mockRejectedValueOnce` with no prior resolve), which is the only broken
 * regime. After one successful load query-core keeps `error` and `isError`
 * because `data` is defined, so a failed 30s poll already renders "Retrying…"
 * — that regime is lg-milestone-that-1454's and no test here may assert the
 * label is unreachable.
 */
describe("the error pane across its own retry", () => {
  const stuck = () => new Promise<MonitorFinding[]>(() => {});

  it("keeps the title and the reason on screen while the retry is in flight", async () => {
    // AC4. The retry never settles, so what is asserted is exactly the state
    // the operator was dropped out of before this fix.
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockImplementation(stuck);

    renderView();

    await userEvent.click(
      await screen.findByRole("button", { name: /retry/i }),
    );

    expect(
      screen.getByText(/could not load monitor findings/i),
    ).toBeInTheDocument();
    expect(reasonElement().textContent).toBe("monitor endpoint is down.");
    expect(screen.queryByText(/loading findings/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing to report")).not.toBeInTheDocument();
  });

  it("disables the retry while it is in flight, says so, and swallows a second click", async () => {
    // AC5. The guard the old branch claimed and did not have: with the branch
    // unmounting on click, `disabled={findings.isFetching}` was never evaluated
    // against a fetching query.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise)
      .mockResolvedValue([finding()]);

    renderView();
    const retry = await screen.findByRole("button", { name: /retry/i });
    await userEvent.click(retry);

    expect(retry).toBeDisabled();
    expect(retry).not.toHaveAttribute("aria-disabled");
    expect(retry).toHaveTextContent("Retrying…");
    expect(monitorFindings).toHaveBeenCalledTimes(2);

    await userEvent.click(retry);
    expect(monitorFindings).toHaveBeenCalledTimes(2);

    second.resolve([finding()]);

    expect(await screen.findByText("Stage thrash")).toBeInTheDocument();
    expect(
      screen.queryByText(/could not load monitor findings/i),
    ).not.toBeInTheDocument();
  });

  it("is the pane's only control, so the pane never ends in text alone", async () => {
    // AC7/AC10: one action, one focus stop, named by its visible text.
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();

    const alert = await screen.findByRole("alert");
    const controls = screen.getAllByRole("button");
    expect(controls).toHaveLength(1);
    expect(alert).toContainElement(controls[0]);
    expect(controls[0]).toHaveAccessibleName("Retry");
  });

  it("announces itself, because a later retry can fail for a different reason", async () => {
    // AC9. The pane now persists, so the reason changing under the operator is
    // a live possibility rather than a remount.
    const second = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise);

    renderView();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not load monitor findings/i);
    expect(alert).toHaveTextContent(/monitor endpoint is down/);

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    second.reject(new Error("the gateway refused the connection"));

    await waitFor(() =>
      expect(reasonElement().textContent).toBe(
        "the gateway refused the connection.",
      ),
    );
    expect(screen.getByRole("alert")).toBe(alert);
  });

  it("adds no spinner or progress indicator while retrying, and keeps the reason readable", async () => {
    // AC11. The disabled attribute and the label change are the whole
    // affordance; the reason must not be replaced or hidden by the wait.
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockImplementation(stuck);

    renderView();
    await userEvent.click(
      await screen.findByRole("button", { name: /retry/i }),
    );

    expect(reasonElement().textContent).toBe("monitor endpoint is down.");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps the failure when the selected ticket changes, because the read is workspace-wide", async () => {
    // AC6, and this is the INVERSE of the test the original AC3 prescribed.
    // api.monitorFindings() takes no argument and the key is constant, so
    // selecting another ticket re-reads nothing and the failure is still true.
    // Keying the hold to ticketId would drop it here while the query stayed in
    // `error` with no data — and execution would fall through to the
    // `!all.length` branch, so the monitor would report "Nothing to report":
    // health it never asked about, the single failure this surface exists to
    // stop.
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));

    const { setTicket } = renderView("ticket-a");
    await screen.findByText(/could not load monitor findings/i);

    setTicket("ticket-b");

    expect(
      screen.getByText(/could not load monitor findings/i),
    ).toBeInTheDocument();
    expect(reasonElement().textContent).toBe("monitor endpoint is down.");
    expect(screen.queryByText("Nothing to report")).not.toBeInTheDocument();
    expect(screen.queryByText(/loading findings/i)).not.toBeInTheDocument();
    expect(monitorFindings).toHaveBeenCalledTimes(1);
  });

  it("keeps the whole reason on screen, and Retry under it, when the reason is a proxy error page", async () => {
    // AC8. `request()` falls back to `message = text` when the body is not
    // JSON, so a dev-proxy or gateway HTML page arrives here verbatim. The
    // reason gets its own bounded, left-aligned element so it cannot push the
    // only control off the viewport; the sentence we own stays separate.
    const page = `<!doctype html><html><body><h1>502 Bad Gateway</h1>${"<p>upstream connect error</p>".repeat(140)}</body></html>`;
    expect(page.length).toBeGreaterThan(2000);
    monitorFindings.mockRejectedValue(new Error(page));

    renderView();

    await screen.findByText(/could not load monitor findings/i);
    const reason = reasonElement();
    expect(reason.textContent).toBe(`${page}.`);
    expect(reason.textContent).not.toContain("Nothing is known about");
    expect(screen.getByText(/Nothing is known about/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});


/**
 * lg-milestone-that-1435, test-break — the same criteria, approached from the
 * states and mutations the designed oracles leave open.
 *
 * Every test here still starts from a FIRST-LOAD failure, for the reason AC12
 * gives: after one successful load query-core keeps `error`, so the
 * data-present regime is lg-milestone-that-1454's and nothing here may pin it.
 */
describe("the error pane, adversarially", () => {
  const stuck = () => new Promise<MonitorFinding[]>(() => {});

  it("never shows a bare reason when the failure carries no message", async () => {
    // Real and routine: HTTP/2 has no reason phrase, so `res.statusText` is the
    // empty string, and a 502 with an empty body reaches `request()` as
    // `new ApiError(502, "")`. describeError recovers the status line, and
    // without it the pane would render a period with nothing in front of it.
    monitorFindings.mockRejectedValue(new ApiError(502, "", undefined));

    renderView();

    await screen.findByText(/could not load monitor findings/i);
    expect(reasonElement().textContent).toBe("Request failed (502).");
  });

  it("falls back to the monitor's own sentence when the rejection is not an error at all", async () => {
    // describeError's *own* default is "Unexpected error", which names nothing
    // the operator can act on. AC8 pins the second argument as part of the
    // source expression; dropping it is a silent, passing mutation everywhere
    // the rejection happens to be an Error.
    monitorFindings.mockRejectedValue({ status: 502 });

    renderView();

    await screen.findByText(/could not load monitor findings/i);
    expect(reasonElement().textContent).toBe(
      "The monitor endpoint did not answer.",
    );
    expect(screen.queryByText(/unexpected error/i)).not.toBeInTheDocument();
  });

  it("is announced by the centred container itself, with one alert on screen", async () => {
    // AC9 asks for a `role` prop on the local Centred helper rather than a
    // duplicated wm-centred div. A wrapper carrying the role instead would read
    // the same to getByRole and lose the layout, and two alerts would make a
    // screen reader announce the pane twice.
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));

    renderView();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveClass("wm-centred");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    const reason = reasonElement();
    expect(reason).not.toHaveClass("wm-error-title");
    expect(reason.textContent).not.toContain("Could not load monitor findings");
  });

  it("drops a click the native disabled attribute should have eaten", async () => {
    // AC5's second click goes through userEvent, which refuses to click an
    // element with no pointer events — so it would also pass against
    // aria-disabled plus a CSS rule, which is not a guard. fireEvent dispatches
    // regardless, and only a real `disabled` button suppresses the handler.
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockImplementation(stuck);

    renderView();
    const retry = await screen.findByRole("button", { name: /retry/i });
    await userEvent.click(retry);
    expect(monitorFindings).toHaveBeenCalledTimes(2);

    fireEvent.click(retry);
    fireEvent.click(retry);

    expect(monitorFindings).toHaveBeenCalledTimes(2);
    expect(retry).toBeDisabled();
  });

  it("keeps the failure across a ticket switch and still retries afterwards", async () => {
    // AC4 and AC6 together, which is the order the operator actually produces:
    // the first read fails, they click another ticket while reading the reason,
    // and only then press Retry. A hold keyed to ticketId is already gone by
    // the time the click lands, so the click would be made against a pane that
    // had fallen through to "Nothing to report".
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockResolvedValue([finding()]);

    const { setTicket } = renderView("ticket-a");
    await screen.findByText(/could not load monitor findings/i);

    setTicket("ticket-b");
    expect(screen.getByRole("alert")).toHaveTextContent(
      /monitor endpoint is down/,
    );

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText("Stage thrash")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns focus on every retry that settles, not only the first", async () => {
    // The effect is keyed on a true → false transition, and a latch that only
    // fires once satisfies AC10's single-retry oracle. An operator retrying a
    // down endpoint presses it repeatedly; the second press must not strand
    // them on <body> for the rest of the outage.
    const second = deferred<MonitorFinding[]>();
    const third = deferred<MonitorFinding[]>();
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(third.promise);

    renderView();
    await userEvent.click(
      await screen.findByRole("button", { name: /retry/i }),
    );
    dropFocus();
    second.reject(new Error("monitor endpoint is down"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled(),
    );
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /retry/i }),
    );

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    dropFocus();
    expect(document.activeElement).toBe(document.body);
    third.reject(new Error("the gateway refused the connection"));

    await waitFor(() =>
      expect(reasonElement().textContent).toBe(
        "the gateway refused the connection.",
      ),
    );
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /retry/i }),
    );
  });
});

/**
 * lg-milestone-that-1465 — the reason is a bounded scroll region, so it has to
 * be a focus stop.
 *
 * `.wm-error-reason` carries `max-height: 9em; overflow: auto` (AC3 keeps all
 * three declarations), and a scroll container that is neither focusable nor
 * holds anything focusable cannot be driven by the arrow keys, Page Up/Down or
 * Home/End in any engine: those keys scroll whichever scrollable ancestor has
 * focus, and before this change nothing in the subtree could take it. The
 * screen-reader path was never broken — the container is `role="alert"`, so the
 * whole reason is announced — which is what made it easy to miss. The operator
 * affected is the sighted keyboard-only one, who sees a scrollbar no key moves.
 *
 * This supersedes lg-milestone-that-1435's AC10 sentence "Retry is its only
 * focus stop": the pane now has two, the region and Retry, in that order.
 */
describe("the reason as a reachable scroll region", () => {
  const REGION_NAME = "Reason the monitor could not be read";

  /**
   * The >2000-character non-JSON body shape the AC8 oracle above already
   * builds: `request()` falls back to `message = text`, so a gateway page
   * arrives verbatim and the 9em box shows roughly its first six lines.
   */
  function proxyErrorPage() {
    const page = `<!doctype html><html><body><h1>502 Bad Gateway</h1>${"<p>upstream connect error</p>".repeat(140)}</body></html>`;
    expect(page.length).toBeGreaterThan(2000);
    return page;
  }

  async function renderFailedFirstLoad() {
    monitorFindings.mockRejectedValue(new Error(proxyErrorPage()));
    renderView();
    await screen.findByText(/could not load monitor findings/i);
  }

  it("exposes the bounded element itself as a group with an accessible name", async () => {
    // AC1, AC11. The name is pinned verbatim rather than paraphrased, and the
    // role is load-bearing: `aria-label` on a generic `<div>` is not reliably
    // exposed by browsers, so the name has to hang off a real role.
    await renderFailedFirstLoad();

    const region = screen.getByRole("group", { name: REGION_NAME });
    // The stop must be the element that scrolls, not a wrapper around it:
    // a focused parent does not scroll a bounded child.
    expect(region).toBe(reasonElement());
    expect(region).toHaveClass("wm-error-reason");
  });

  it("holds the server's text and nothing we wrote", async () => {
    // AC6. A screen reader entering the region hears foreign text only — the
    // title and the "Nothing is known…" sentence stay outside it.
    await renderFailedFirstLoad();

    const region = screen.getByRole("group", { name: REGION_NAME });
    expect(region.textContent).toBe(`${proxyErrorPage()}.`);
    expect(region.textContent).not.toContain("Nothing is known about");
    expect(region.textContent).not.toContain("Could not load monitor findings");
  });

  it("is the first Tab stop from outside the pane, and Retry is the next", async () => {
    // AC2, AC7: the pane's whole keyboard path is Tab → region → Tab → Retry,
    // and Shift+Tab from Retry comes back. jsdom computes tab order from the
    // DOM rather than from layout, so a tabbable div before a <button> is
    // reached first here for the same reason it is in a browser.
    await renderFailedFirstLoad();
    const region = screen.getByRole("group", { name: REGION_NAME });
    const retry = screen.getByRole("button", { name: /^retry$/i });

    dropFocus();
    expect(document.activeElement).toBe(document.body);

    await userEvent.tab();
    expect(document.activeElement).toBe(region);

    await userEvent.tab();
    expect(document.activeElement).toBe(retry);

    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(region);
  });

  it("is a stop whatever the reason's length, not only when it overflows", async () => {
    // AC10. `tabIndex={0}` is a literal: deciding it from
    // `scrollHeight > clientHeight` needs a post-layout read jsdom cannot
    // give, and a Tab stop that appears and disappears with the length of
    // foreign server text is less predictable than one always there.
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));
    renderView();
    await screen.findByText(/could not load monitor findings/i);

    const region = screen.getByRole("group", { name: REGION_NAME });
    expect(region.textContent).toBe("monitor endpoint is down.");
    dropFocus();
    await userEvent.tab();
    expect(document.activeElement).toBe(region);
  });

  it("adds one stop and one region, not one per failed poll", async () => {
    // AC12 and the node-count assertion above, from the other side: the held
    // failure keeps the same node mounted across consecutive failures, so
    // nothing accrues and there is no scroll-position reset to make.
    monitorFindings.mockRejectedValue(new Error(proxyErrorPage()));
    const { startPoll } = renderView();
    await screen.findByText(/could not load monitor findings/i);

    await act(async () => {
      await startPoll();
    });

    expect(screen.getAllByRole("group", { name: REGION_NAME })).toHaveLength(1);
    expect(
      screen.getByRole("alert").querySelectorAll(".wm-error-reason"),
    ).toHaveLength(1);
    dropFocus();
    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("group", { name: REGION_NAME }),
    );
    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /^retry$/i }),
    );
  });
});

/**
 * lg-milestone-that-1465, test-break — what the designed oracles above leave
 * reachable.
 *
 * Each test below holds against an implementation that passes all five tests
 * in the block above and is still wrong:
 *
 * 1. `role="group" aria-label={…} tabIndex={0}` added while `className` loses
 *    `wm-narrow`. The name resolves, the Tab path is unchanged, and AC8's
 *    measured volume stops being true: it is `.wm-narrow`'s 380px that makes
 *    the box ~61 characters a line, so dropping it changes what the operator
 *    sees at the median 205-character reason without failing a thing.
 * 2. The three attributes on a new wrapper *around* the bounded div, or the
 *    bounded div moved below the sentence we wrote. A focused parent does not
 *    scroll a bounded child, and AC1 pins the position — but Tab order from
 *    <body> is region-then-Retry either way.
 * 3. `tabIndex={1}`. From <body> inside this pane it is indistinguishable from
 *    0, and in the app it jumps the whole page's queue: a positive tabindex is
 *    reached before every `tabIndex={0}` stop on the screen.
 * 4. `role="region"`, which AC11 refuses. `getByRole("group", …)` is the only
 *    role assertion above, and a landmark injected for a transient error pane
 *    reaches every screen-reader user's landmark list.
 * 5. The attributes added to the `wm-stale` line as well — the branch that
 *    kept the operator's list. AC5 says nothing else about the pane changes,
 *    and that line is not bounded, so a stop there is a stop on nothing.
 * 6. A reason the server gave us no words for. Every oracle above feeds an
 *    `Error` with a message; `describeError`'s fallback branch is what stops
 *    the region being an empty box carrying a focus ring (AC6).
 *
 * The last test reads the stylesheet off disk, as `AppSidebarScrolling.test.tsx`
 * does: `jest.config.cjs:55` maps `*.css` to a stub, so `getComputedStyle`
 * reports nothing whatever the file says, and AC3 and AC9 would otherwise ship
 * with no oracle at all. It is weaker than a browser measurement and is not
 * pretending otherwise — it observes the declarations, not the painted ring.
 */
describe("the reason region, adversarially", () => {
  const NAME = "Reason the monitor could not be read";
  const PAGE = `<!doctype html><html><body><h1>502 Bad Gateway</h1>${"<p>upstream connect error</p>".repeat(140)}</body></html>`;

  async function failedFirstLoad() {
    monitorFindings.mockRejectedValue(new Error(PAGE));
    renderView();
    await screen.findByText(/could not load monitor findings/i);
    return screen.getByRole("group", { name: NAME });
  }

  it("keeps all three classes, and the place in the column, that AC8's measurement rests on", async () => {
    // AC1. `wm-muted` is the tone and `wm-narrow` the 380px width; the bound is
    // measured against both. "Exactly three new attributes and nothing else"
    // also means the element did not move and did not acquire a wrapper.
    const region = await failedFirstLoad();
    const alert = screen.getByRole("alert");

    expect(Array.from(region.classList).sort()).toEqual([
      "wm-error-reason",
      "wm-muted",
      "wm-narrow",
    ]);
    expect(region.parentElement).toBe(alert);
    const after = (first: Element, second: Element) =>
      Boolean(
        first.compareDocumentPosition(second) &
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    expect(after(alert.querySelector(".wm-error-title")!, region)).toBe(true);
    expect(after(region, screen.getByText(/Nothing is known about/))).toBe(
      true,
    );
    expect(
      after(region, screen.getByRole("button", { name: /^retry$/i })),
    ).toBe(true);
  });

  it("is a stop at tabindex 0, so it does not jump the page's queue", async () => {
    // AC10. A positive tabindex is reached before every tabIndex={0} stop in
    // the document, so the operator arrives here from somewhere unrelated.
    // `beside` is the sibling control the harness renders ahead of the view.
    monitorFindings.mockRejectedValue(new Error(PAGE));
    renderView(null, <a href="#elsewhere">Elsewhere</a>);
    await screen.findByText(/could not load monitor findings/i);
    const region = screen.getByRole("group", { name: NAME });

    expect(region.getAttribute("tabindex")).toBe("0");

    dropFocus();
    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("link", { name: "Elsewhere" }),
    );
    await userEvent.tab();
    expect(document.activeElement).toBe(region);
  });

  it("is a group and not a landmark", async () => {
    // AC11. `role="region"` would expose the same name to `aria-label` and put
    // a transient error pane in the landmark list of every screen reader.
    const region = await failedFirstLoad();

    expect(region.getAttribute("role")).toBe("group");
    expect(screen.queryAllByRole("region")).toHaveLength(0);
  });

  it("names the region even when the server gave us no words to quote", async () => {
    // AC6. `describeError` falls through to the supplied fallback for a
    // rejection that is neither an Error nor a string, so the region is never
    // an empty box carrying a focus ring — and it is still a named stop.
    monitorFindings.mockRejectedValue({ status: 502 });
    renderView();
    await screen.findByText(/could not load monitor findings/i);

    const region = screen.getByRole("group", { name: NAME });
    expect(region.textContent).toBe("The monitor endpoint did not answer.");
    dropFocus();
    await userEvent.tab();
    expect(document.activeElement).toBe(region);
  });

  it("holds the same node across a failed poll, which is why there is nothing to reset", async () => {
    // AC12, as the only part of it jsdom can observe: it lays nothing out, so
    // `scrollTop` is pinned at 0 and no assertion can see a scroll position.
    // What it can see is that the node is not replaced — a remount would reset
    // the scroll in a browser whether or not an effect ever sets it.
    monitorFindings.mockRejectedValue(new Error(PAGE));
    const { startPoll } = renderView();
    await screen.findByText(/could not load monitor findings/i);
    const before = screen.getByRole("group", { name: NAME });

    await act(async () => {
      await startPoll();
    });

    expect(screen.getByRole("group", { name: NAME })).toBe(before);
  });

  it("leaves the pane that kept the operator's list unbounded and unstopped", async () => {
    // AC5. A failed re-read over findings already on screen reports inline in
    // `wm-stale`, which is not bounded and not a scroll container. A focus
    // stop there would be a stop on nothing, and the fix must not spread.
    monitorFindings
      .mockResolvedValueOnce([finding()])
      .mockRejectedValue(new Error(PAGE));
    const { poll } = renderView();
    await screen.findByText("Stage thrash");

    await poll();

    expect(await screen.findByTestId("wm-refresh-failed")).toHaveTextContent(
      /may be out of date/,
    );
    expect(screen.queryByRole("group", { name: NAME })).toBeNull();
    expect(document.querySelectorAll(".wm-error-reason")).toHaveLength(0);
  });

  it("keeps the bound byte for byte, and rings the new stop inset in the theme's accent", () => {
    // AC3 and AC9, read out of the stylesheet because jest maps it to a stub.
    // The declarations are compared exhaustively: that is what rejects a
    // `line-clamp`, an ellipsis, or a loosened `max-height` "to fit the
    // typical case", none of which any rendered assertion in this file sees.
    const rules = Array.from(
      fs
        .readFileSync(
          path.resolve(__dirname, "..", "WorkflowMonitorView.css"),
          "utf8",
        )
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .matchAll(/([^{}]+)\{([^{}]*)\}/g),
      ([, selector, body]) => ({
        selector: selector.trim(),
        declarations: Object.fromEntries(
          body
            .split(";")
            .map((line) => line.split(":"))
            .filter(([property]) => property.trim())
            .map(([property, ...rest]) => [
              property.trim(),
              rest.join(":").trim(),
            ]),
        ),
      }),
    );
    const index = rules.findIndex(
      (rule) => rule.selector === ".wm-error-reason",
    );

    expect(index).toBeGreaterThanOrEqual(0);
    expect(rules[index]!.declarations).toEqual({
      "text-align": "left",
      "max-height": "9em",
      overflow: "auto",
    });
    // Immediately after, so the ring cannot drift away from what it rings.
    expect(rules[index + 1]!.selector).toBe(".wm-error-reason:focus-visible");
    expect(rules[index + 1]!.declarations).toEqual({
      outline: "2px solid var(--ac)",
      "outline-offset": "-2px",
    });
  });
});

/**
 * lg-milestone-that-2196 AC4/AC11 — the assertive re-announce is KEPT, and
 * this is the keep-branch's test.
 *
 * The 30-second cadence the ticket describes was never the reason string: it
 * was the Retry label flipping `Retry` → `Retrying…` inside the `role="alert"`
 * container on every poll, which AC1 removes. Once it is gone, a steady outage
 * returning the identical message mutates no DOM inside the live region and
 * never re-announces — what remains fires only on a *change* of message,
 * bounded by the number of distinct failure modes during the outage rather than
 * by the poll interval.
 *
 * A changed reason is a change of fact, not a report of activity: "connection
 * refused" to "401 unauthorized" changes what the operator does next, and a
 * screen-reader operator not told of it goes on acting on a reason that is no
 * longer true. Suppressing it has no honest implementation either — leaving the
 * DOM alone shows a stale reason, and updating the DOM while silencing the
 * announce (swapping the `role`, re-mounting the region) tells sighted and
 * screen-reader operators different things, and would also silence the
 * operator's own retry, which AC4's last sentence forbids.
 *
 * The nearest existing oracle, "holds the same node across a failed poll",
 * rejects with one value for both polls, so it cannot see this at all.
 */
describe("the re-announce across polls that fail differently", () => {
  it("replaces the reason, in the same live region, when consecutive polls fail for different reasons", async () => {
    // Two background polls, each with its own reason, and no click or keypress
    // anywhere in the test: this is the pane under a 30s timer, not under an
    // operator. Deferred, so each failure is a committed in-flight render
    // rather than one act() flush.
    const polls = [deferred<MonitorFinding[]>(), deferred<MonitorFinding[]>()];
    monitorFindings
      .mockRejectedValueOnce(new Error("the gateway refused the connection"))
      .mockReturnValueOnce(polls[0].promise)
      .mockReturnValueOnce(polls[1].promise);

    const { startPoll } = renderView();
    const alert = await screen.findByRole("alert");
    expect(reasonElement().textContent).toBe(
      "the gateway refused the connection.",
    );

    const reasons = ["401 unauthorized", "upstream timed out after 30s"];
    for (const [index, reason] of reasons.entries()) {
      const started = startPoll();
      await act(async () => {
        polls[index]!.reject(new Error(reason));
        await started;
      });

      // The fact changed, so the region's text changed with it — which is the
      // announcement, because the container is the live region.
      await waitFor(() =>
        expect(reasonElement().textContent).toBe(`${reason}.`),
      );
      // And it is still the same assertive container: not re-mounted, not
      // demoted to another role to buy silence.
      expect(screen.getByRole("alert")).toBe(alert);
      expect(alert).toHaveAttribute("role", "alert");
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    }
  });
});
