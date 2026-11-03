import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { WorkflowMonitorView } from "../WorkflowMonitorView";
import { ApiError } from "../../../api/http";
import type { MonitorFinding } from "../../../api/types";

const monitorFindings = jest.fn();

jest.mock("../../../api/client", () => ({
  api: { monitorFindings: (ticketId?: string) => monitorFindings(ticketId) },
}));

/**
 * All five states are pinned, because the one that matters most here is the one
 * a panel like this normally gets wrong: an error must not render as an empty
 * list. "The monitor has nothing to report" and "we could not ask the monitor"
 * are opposite facts about the pipeline, and collapsing them into one blank
 * pane is the failure this whole surface exists to stop.
 */

const finding = (over: Partial<MonitorFinding> = {}): MonitorFinding => ({
  condition: "stage_thrash",
  ticket_id: "t1",
  stage_key: "script_review",
  summary: "script_review attempted 12x against a baseline of 2",
  evidence: {},
  occurrences: 1,
  first_seen: null,
  last_seen: null,
  ticket_title: "Fix the flaky review",
  ticket_external_id: "lor-x-1",
  ticket_state: "in_progress",
  workspace_slug: "loregarden",
  ...over,
});

/**
 * `setTicket` re-renders against the *same* QueryClient, which is the whole
 * point: the query key is the constant ["monitor-findings", "all"], so changing
 * the selected ticket starts no new read (lg-milestone-that-1435 AC6).
 *
 * `beside` renders a sibling control, for the one case that needs somewhere
 * else for focus to be (AC10). An <a> rather than a <button> because the theme
 * gate rejects a raw <button> on a changed line.
 */
function renderView(ticketId: string | null = null, beside: ReactNode = null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const tree = (id: string | null) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
        {beside}
        <WorkflowMonitorView ticketId={id} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const result = render(tree(ticketId));
  return { ...result, setTicket: (id: string | null) => result.rerender(tree(id)) };
}

/**
 * Put focus back on <body>, standing in for what a real browser does when a
 * focused <button> is disabled.
 *
 * `activeElement.blur()` cannot do it: jsdom treats a disabled button as a
 * non-focusable area, so `blur()` on one is a no-op and the element the click
 * focused stays active — an oracle built on it can never go green under any
 * implementation. `document.body.focus()` does not move it either, because
 * jsdom will not make <body> the active element on request. Focusing a
 * throwaway anchor and blurring *that* leaves activeElement on <body>, which is
 * the state a real browser leaves behind when it disables a focused control.
 */
function dropFocus() {
  const pad = document.createElement("a");
  pad.href = "#pad";
  document.body.append(pad);
  pad.focus();
  pad.blur();
  pad.remove();
}

/** A promise the test settles, so an in-flight state can be asserted on. */
function deferred<T>() {
  let settle!: { resolve: (value: T) => void; reject: (error: unknown) => void };
  const promise = new Promise<T>((resolve, reject) => {
    settle = { resolve, reject };
  });
  return { promise, ...settle };
}

/** The one element the reason text lives in, and nothing else (AC8). */
function reasonElement() {
  const reason = screen.getByRole("alert").querySelector(".wm-error-reason");
  if (!reason) throw new Error("the error pane rendered no .wm-error-reason element");
  return reason;
}

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

  expect(await screen.findByRole("heading", { name: "Recurring across tickets" })).toBeInTheDocument();
  expect(screen.getByText("3 tickets")).toBeInTheDocument();
});

it("names each ticket and links to it", async () => {
  monitorFindings.mockResolvedValue([finding({ ticket_id: "t1" })]);

  renderView();

  const link = await screen.findByRole("link", { name: /lor-x-1.*Fix the flaky review/ });
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
    finding({ ticket_id: "old", ticket_external_id: "lor-old-2", ticket_state: "done" }),
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

  expect(await screen.findByText("Nothing on live tickets")).toBeInTheDocument();
});

it("puts the selected ticket's own findings first", async () => {
  monitorFindings.mockResolvedValue([
    finding({ ticket_id: "other", ticket_external_id: "lor-other-9" }),
    finding({ ticket_id: "mine", ticket_external_id: "lor-mine-4" }),
  ]);

  renderView("mine");

  const section = await screen.findByRole("heading", { name: "On this ticket" });
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

  expect(await screen.findByText(/could not load monitor findings/i)).toBeInTheDocument();
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
  expect(screen.getByText(/Nothing is known about/).textContent).not.toContain("Failed to fetch");
});

it("does not double the period when the message already ends in one", async () => {
  monitorFindings.mockRejectedValue(new Error("The monitor endpoint did not answer."));

  renderView();

  await screen.findByText(/could not load monitor findings/i);
  expect(reasonElement().textContent).toBe("The monitor endpoint did not answer.");
});

it("offers a retry that re-asks", async () => {
  monitorFindings.mockRejectedValueOnce(new Error("transient")).mockResolvedValue([finding()]);

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
  expect(screen.queryByRole("button", { name: /resolve|dismiss|fix/i })).not.toBeInTheDocument();
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

    await userEvent.click(await screen.findByRole("button", { name: /retry/i }));

    expect(screen.getByText(/could not load monitor findings/i)).toBeInTheDocument();
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
    expect(screen.queryByText(/could not load monitor findings/i)).not.toBeInTheDocument();
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

    await waitFor(() => expect(reasonElement().textContent).toBe("the gateway refused the connection."));
    expect(screen.getByRole("alert")).toBe(alert);
  });

  it("adds no spinner or progress indicator while retrying, and keeps the reason readable", async () => {
    // AC11. The disabled attribute and the label change are the whole
    // affordance; the reason must not be replaced or hidden by the wait.
    monitorFindings
      .mockRejectedValueOnce(new Error("monitor endpoint is down"))
      .mockImplementation(stuck);

    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /retry/i }));

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

    expect(screen.getByText(/could not load monitor findings/i)).toBeInTheDocument();
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

    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /retry/i }));
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

    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled());
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
    expect(reasonElement().textContent).toBe("The monitor endpoint did not answer.");
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
    expect(screen.getByRole("alert")).toHaveTextContent(/monitor endpoint is down/);

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
    await userEvent.click(await screen.findByRole("button", { name: /retry/i }));
    dropFocus();
    second.reject(new Error("monitor endpoint is down"));
    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /retry/i }));

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    dropFocus();
    expect(document.activeElement).toBe(document.body);
    third.reject(new Error("the gateway refused the connection"));

    await waitFor(() => expect(reasonElement().textContent).toBe("the gateway refused the connection."));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /retry/i }));
  });
});
