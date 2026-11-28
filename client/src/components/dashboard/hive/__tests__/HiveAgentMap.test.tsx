/**
 * The Hive map pane, over the recorded fixtures (lg-hive-office-2784, S6/S7 and the four UI
 * criteria). It answers one question — "what is each agent run on this ticket doing right now, and
 * did any of its tool calls fail?" — so every assertion here is about what a person can read or
 * reach, never about an internal call.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../../api/client";
import type { RunLog, TicketDetail, TicketLedger } from "../../../../api/types";
import { buildToolActs } from "../../../../lib/hive/toolActs";
import { BUSIEST, BUSY, FAILED_CALL, FAILING, NO_CALLS, asActRun, asRunLog } from "../../../../test/hiveRunFixtures";
import type { HiveRunFixture } from "../../../../test/hiveRunFixtures";
import { HiveAgentMap } from "../HiveAgentMap";

jest.mock("../../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const ticket = { id: "t1" } as TicketDetail;

type Attempt = TicketLedger["visits"][number]["attempts"][number];

function ledger(attempts: Attempt[]): TicketLedger {
  return {
    visits: [{ stage_key: "implement", visit_number: 1, status: "running", is_parallel: false, attempts }],
    total_runs: attempts.length,
    reworked_stages: [],
    total_seconds: 0,
  };
}

function attempt(overrides: Partial<Attempt> = {}): Attempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    status: "running",
    started_at: "2026-11-20T10:00:00Z",
    finished_at: null,
    duration_seconds: null,
    error_tail: "",
    ...overrides,
  };
}

function runLog(lines: RunLog["lines"], overrides: Partial<RunLog> = {}): RunLog {
  return {
    id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    stage_key: "implement",
    status: "running",
    command: "",
    adapter: "claude",
    call_lines_expected: true,
    started_at: "2026-11-20T10:00:00Z",
    finished_at: null,
    lines,
    live: null,
    stderr: "",
    transport: "",
    attach_command: "",
    ...overrides,
  };
}

/** One run on the ticket, built from a recorded fixture. */
function oneFixtureRun(fixture: HiveRunFixture, overrides: Partial<RunLog> = {}) {
  const status = String(overrides.status ?? fixture.status);
  mockApi.ticketLedger.mockResolvedValue(
    ledger([attempt({ run_id: fixture.run_id, run_code: fixture.run_code, agent_id: fixture.agent_id, status })]),
  );
  mockApi.runLog.mockResolvedValue(asRunLog(fixture, { status, ...overrides }));
}

function renderMap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HiveAgentMap ticket={ticket} />
    </QueryClientProvider>,
  );
}

const feedSection = () => screen.getByText("Latest calls").closest("section") as HTMLElement;
const feedRows = () => within(feedSection()).getAllByRole("button");
/** The run list, which the feed also fills with "Open its log" buttons — hence the scoping. */
const agentsSection = () => screen.getByText(/At work|Last run/).closest("section") as HTMLElement;
const agentRows = () => within(agentsSection()).getAllByRole("button");
const agentRow = () => agentRows()[0];
const svgLabel = () => screen.getByRole("img").getAttribute("aria-label") ?? "";

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "", cancel_requested_at: null });
});

// ---- what was already true, kept -----------------------------------------------------------------

it("says how to get a run when the ticket has none", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([]));
  renderMap();
  expect(await screen.findByText("No agent has run on this ticket yet")).toBeTruthy();
});

it("shows a ledger error that does not look like the empty state", async () => {
  mockApi.ticketLedger.mockRejectedValue(new Error("down"));
  renderMap();
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn't load this ticket's agent runs");
});

it("lists the running agent and its calls, and opens its log from either list", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([attempt()]));
  mockApi.runLog.mockResolvedValue(
    runLog([
      { tag: "TOOL", text: "call · Read · hive/cast.ts", time: "10:00:01" },
      { tag: "TOOL", text: "result\nfile body", time: "10:00:02" },
      { tag: "TOOL", text: "call · Bash · pytest: Run the hive tests", time: "10:00:03" },
    ]),
  );
  renderMap();

  await screen.findByText("● Shell — Run the hive tests");
  expect(agentRow().textContent).toContain("2 calls");
  expect(feedRows().map((r) => r.getAttribute("aria-label"))).toEqual([
    expect.stringContaining("Shell, Run the hive tests, running"),
    expect.stringContaining("Files, hive/cast.ts, done"),
  ]);
  expect(svgLabel()).toContain("Files 1");

  fireEvent.click(feedRows()[0]);
  expect(await screen.findByRole("dialog")).toBeTruthy();
});

// ---- the three recorded fixtures, each showing its own visible difference ------------------------

it("shows the busy run's collapsed act with a count the operator cannot misread", async () => {
  oneFixtureRun(BUSY);
  renderMap();
  await screen.findByText("Latest calls");

  const folds = await waitFor(() => {
    const found = feedSection().querySelectorAll("[title*='more calls at']");
    expect(found.length).toBeGreaterThan(0);
    return [...found];
  });
  // The count is of the log's 1000-line tail, never of the run: 117 of this run's calls are in the
  // log, and a long run's would not be.
  for (const fold of folds) {
    expect(fold.getAttribute("title")).toMatch(
      /^\+\d+ more calls at .+ in this act, of the log's 1000 most recent lines$/,
    );
    expect(fold.textContent).toMatch(/^\+\d+$/);
    expect(fold.getAttribute("aria-hidden")).toBe("true");
  }
  expect(feedRows().length).toBeLessThanOrEqual(16);
});

it("marks the failing run's own status after it ends, and marks no call failed", async () => {
  // S6 + S4-AC3. `fcc2a8a7` makes 82 clean calls and then dies at the gate: ERR/FAIL are about
  // the run. Nothing here may read `✕` on a call.
  oneFixtureRun(FAILING);
  renderMap();
  await screen.findByText("✕ failed");
  expect(agentRow().getAttribute("aria-label")).toContain("failed");
  expect(agentRow().textContent).not.toContain("1 failed");
  for (const row of feedRows()) {
    expect(row.getAttribute("aria-label")).not.toContain(", failed");
  }
});

it("reads the no-call-lines run as unknown activity and never as no calls yet", async () => {
  // S7-AC1. A `cursor-agent` run: 100 lines, 0 call lines. Idleness is the one reading that is
  // wrong here, and `no calls yet` is how it used to be said.
  oneFixtureRun(NO_CALLS);
  renderMap();
  const notice = await screen.findByText(
    `activity unknown — the ${NO_CALLS.adapter} adapter does not log tool calls`,
  );
  expect(notice).toBeTruthy();
  expect(screen.queryByText("no calls yet")).toBeNull();
  expect(screen.queryByText("No tool calls logged yet")).toBeNull();
  expect(agentRow().textContent).toContain("· activity unknown");
  expect(agentRow().textContent).not.toMatch(/· \d+ calls/);
});

// ---- S7: three different nothings, three different strings ---------------------------------------

it("says `no calls yet` only for a run whose adapter does write them", async () => {
  // S7-AC2: 22 live Claude runs have logged none.
  oneFixtureRun({ ...BUSY, lines: [] }, { adapter: "claude", call_lines_expected: true });
  renderMap();
  expect(await screen.findByText("no calls yet")).toBeTruthy();
  expect(screen.queryByText(/activity unknown/)).toBeNull();
});

it("says `adapter not recorded` for a run with no command, not `no calls yet`", async () => {
  // S7-AC3: 790 of 6,211 live runs record no command, which is the only place an adapter is read
  // from. This is the largest single group on the map.
  oneFixtureRun({ ...NO_CALLS, adapter: "", call_lines_expected: false });
  renderMap();
  expect(await screen.findByText("activity unknown — adapter not recorded")).toBeTruthy();
  expect(screen.queryByText("no calls yet")).toBeNull();
  expect(screen.queryByText(/the .* adapter does not log tool calls/)).toBeNull();
});

it("says `loading activity…` while a run's own log is still being fetched, never `unknown`", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([attempt({ run_id: NO_CALLS.run_id })]));
  let release: (log: RunLog) => void = () => {};
  mockApi.runLog.mockReturnValue(new Promise<RunLog>((resolve) => { release = resolve; }));
  renderMap();
  expect(await screen.findByText("loading activity…")).toBeTruthy();
  expect(screen.queryByText(/activity unknown/)).toBeNull();
  release(asRunLog(NO_CALLS));
  expect(await screen.findByText(/activity unknown/)).toBeTruthy();
});

it("keeps the four nothings as four different strings", async () => {
  // Collapsing any two of them is what made an unobservable adapter read as idleness.
  const strings = new Set([
    "no calls yet",
    `activity unknown — the ${NO_CALLS.adapter} adapter does not log tool calls`,
    "activity unknown — adapter not recorded",
    "loading activity…",
  ]);
  expect(strings.size).toBe(4);
});

it("replaces the feed's empty copy when every run on the map is unknown", async () => {
  // S7-AC5. The sentence must name the adapter and point at the row button as the way out, and
  // read as neither failure nor idleness.
  oneFixtureRun(NO_CALLS);
  renderMap();
  await screen.findByText(/activity unknown/);
  const empty = within(feedSection()).getByRole("status");
  expect(empty.textContent).toContain(NO_CALLS.adapter);
  expect(empty.textContent).toMatch(/log/i);
  expect(empty.textContent).not.toContain("No tool calls logged yet");
  expect(empty.textContent).not.toMatch(/\bidle\b|\bfailed\b/i);
});

it("draws no spoke, no pulse and no busy ring for an unknown run, and names it in the SVG label", async () => {
  // S7-AC6.
  oneFixtureRun(NO_CALLS);
  renderMap();
  await screen.findByText(/activity unknown/);
  const svg = screen.getByRole("img");
  expect(svg.querySelectorAll(".hive-map__spoke")).toHaveLength(0);
  expect(svg.querySelectorAll(".hive-map__pulse")).toHaveLength(0);
  expect(svg.querySelectorAll(".hive-map__station-busy")).toHaveLength(0);
  expect(svgLabel()).toContain("1 runs with unknown activity.");
});

// ---- S6: the run's status wins, and its vocabulary is pinned -------------------------------------

describe("the run's now-line", () => {
  it.each([
    ["succeeded", "✓ succeeded"],
    ["failed", "✕ failed"],
    ["cancelled", "✕ cancelled"],
    ["awaiting_permission", "⏳ waiting for you — awaiting permission"],
  ])("reads %s as `%s`, in the row's aria-label too", async (status, rendered) => {
    oneFixtureRun(BUSY, { status });
    renderMap();
    expect(await screen.findByText(rendered)).toBeTruthy();
    const label = agentRow().getAttribute("aria-label") ?? "";
    for (const word of rendered.replace(/[✓✕⏳]/g, "").trim().split(/\s+/)) {
      expect(label).toContain(word);
    }
    // The mark itself is never what a screen reader reads.
    for (const glyph of ["✓", "✕", "⏳"]) expect(label).not.toContain(glyph);
  });

  it("never renders `completed`, which is not a RunStatus", async () => {
    oneFixtureRun(BUSY, { status: "succeeded" });
    renderMap();
    await screen.findByText("✓ succeeded");
    expect(document.body.textContent).not.toContain("completed");
  });

  it("loses the running mark and shows no running act the moment a run ends", async () => {
    // The fixture's last call is left open, so a run that ended mid-call must not keep `●`.
    oneFixtureRun({ ...BUSY, lines: [...BUSY.lines, { tag: "TOOL", text: "call · Read · open.ts", time: "15:35:50" }] }, { status: "succeeded" });
    renderMap();
    await screen.findByText("✓ succeeded");
    expect(agentRow().getAttribute("aria-busy")).not.toBe("true");
    expect(feedSection().textContent).not.toContain("●");
    for (const row of feedRows()) expect(row.getAttribute("aria-label")).not.toContain(", running");
  });

  it("carries aria-busy on a running act's row while the run is still at work", async () => {
    mockApi.ticketLedger.mockResolvedValue(ledger([attempt()]));
    mockApi.runLog.mockResolvedValue(runLog([{ tag: "TOOL", text: "call · Read · a.ts", time: "10:00:01" }]));
    renderMap();
    await screen.findByText("● Files — a.ts");
    expect(feedRows()[0].getAttribute("aria-busy")).toBe("true");
  });
});

// ---- the Failed only toggle: a failure the operator can always reach ------------------------------

describe("the Failed only toggle", () => {
  /** A run whose two failures are older than its newest 16 acts, so the feed's tail hides them. */
  const buried = { ...FAILED_CALL, lines: [...FAILED_CALL.lines, ...BUSIEST.lines] };

  it("is hidden when nothing failed", async () => {
    oneFixtureRun(BUSIEST);
    renderMap();
    await screen.findByText("Latest calls");
    expect(screen.queryByRole("button", { name: /Failed only/ })).toBeNull();
  });

  it("names its count, and reaches a failure the 16-row feed had pushed off", async () => {
    // The measured reason this control exists: 78 acts against 16 rows, ~7.5% of results failing.
    oneFixtureRun(buried);
    renderMap();
    const toggle = await screen.findByRole("button", { name: /Failed only/ });
    expect(toggle.textContent).toContain("Failed only (2)");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(feedRows().some((r) => (r.getAttribute("aria-label") ?? "").includes(", failed"))).toBe(false);

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    const failedRows = feedRows().filter((r) => (r.getAttribute("aria-label") ?? "").includes(", failed"));
    expect(failedRows).toHaveLength(2);
    fireEvent.click(failedRows[0]);
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("keeps focus when toggled and announces the result outside the feed's live region", async () => {
    oneFixtureRun(buried);
    renderMap();
    const toggle = await screen.findByRole("button", { name: /Failed only/ });
    toggle.focus();
    fireEvent.click(toggle);
    expect(document.activeElement).toBe(toggle);
    const status = screen.getByText(/Showing 2 failed acts of \d+/);
    expect(status.getAttribute("role")).toBe("status");
    // Not the feed's own aria-live region, which would re-read the whole list.
    expect(status.closest("[aria-live='polite']")).toBeNull();
  });

  it("sits in tab order between the heading and the first feed row", async () => {
    oneFixtureRun(buried);
    renderMap();
    const toggle = await screen.findByRole("button", { name: /Failed only/ });
    const order = [...feedSection().querySelectorAll("button")];
    expect(order[0]).toBe(toggle);
    expect(toggle.getAttribute("disabled")).toBeNull();
  });
});

// ---- marks, the desk column, and motion ----------------------------------------------------------

it("renders a failed act as its own row marked ✕, never inside a +N", async () => {
  oneFixtureRun(FAILED_CALL);
  renderMap();
  await screen.findByText("Latest calls");
  const failed = await waitFor(() => {
    const rows = feedRows().filter((r) => (r.getAttribute("aria-label") ?? "").includes(", failed"));
    expect(rows).toHaveLength(2);
    return rows;
  });
  for (const row of failed) {
    expect(row.textContent).toContain("✕");
    expect(row.querySelector("[title*='more calls at']")).toBeNull();
    // The glyph is decoration; the word is in the label.
    expect(row.querySelector("[aria-hidden='true']")?.textContent).toBe("✕");
  }
});

it("keeps a failed row after a later act on the same run succeeded", async () => {
  // Both failures in this fixture are followed by clean acts at the same place.
  oneFixtureRun(FAILED_CALL, { status: "succeeded" });
  renderMap();
  await screen.findByText("✓ succeeded");
  expect(feedRows().filter((r) => (r.getAttribute("aria-label") ?? "").includes(", failed"))).toHaveLength(2);
});

it("reads a desk act's station column as `Desk`, and draws it no spoke", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([attempt()]));
  mockApi.runLog.mockResolvedValue(
    runLog([
      { tag: "TOOL", text: "call · TaskOutput · agent_1", time: "10:00:01" },
      { tag: "TOOL", text: "result\nok", time: "10:00:02" },
    ]),
  );
  renderMap();
  await screen.findByText("Latest calls");
  const row = await waitFor(() => {
    const [first] = feedRows();
    expect(first.getAttribute("aria-label")).toContain("Desk");
    return first;
  });
  expect(row.getAttribute("aria-label")).toContain("TaskOutput");
  expect(row.getAttribute("aria-label")).not.toContain("Shell");
  expect(screen.getByRole("img").querySelectorAll(".hive-map__spoke")).toHaveLength(0);
  expect(svgLabel()).not.toContain("Shell 1");
});

it("flies one pulse per act, not one per call", async () => {
  // A 40-call burst must not draw 40 journeys in 10 s, or the picture claims journeys the agent
  // never made. The first render pulses nothing — the history already on screen is not news — so
  // the burst has to arrive on a later poll, which is why this test waits one POLL_MS.
  const burst: RunLog["lines"] = [];
  for (let i = 0; i < 40; i += 1) {
    const time = `10:00:${String(i).padStart(2, "0")}`;
    burst.push({ tag: "TOOL", text: `call · Read · f${i}.ts`, time }, { tag: "TOOL", text: "result\nok", time });
  }
  mockApi.ticketLedger.mockResolvedValue(ledger([attempt()]));
  mockApi.runLog.mockResolvedValueOnce(runLog([]));
  mockApi.runLog.mockResolvedValue(runLog(burst));
  renderMap();
  await screen.findByText("no calls yet");

  const pulses = await waitFor(
    () => {
      const found = screen.getByRole("img").querySelectorAll(".hive-map__pulse");
      expect(found.length).toBeGreaterThan(0);
      return found;
    },
    { timeout: 8000 },
  );
  // All 40 calls are consecutive Reads: one act, so one flight across the Files spoke.
  expect(pulses).toHaveLength(1);
  expect(feedRows()).toHaveLength(1);
  expect(feedSection().textContent).toContain("+39");
});

it("rewords a per-run log failure so it cannot read as idleness", async () => {
  mockApi.ticketLedger.mockResolvedValue(ledger([attempt({ run_code: "run_zz" })]));
  mockApi.runLog.mockRejectedValue(new Error("down"));
  renderMap();
  const warn = await screen.findByText(/Couldn't load run_zz's log/);
  expect(warn.textContent).toContain("its activity is unknown, not idle");
  expect(warn.textContent).toContain("Retrying every 2 seconds.");
});

it("shows at most six agent rows, the measured overlap on one ticket", async () => {
  const fixtures = [BUSY, BUSIEST, FAILING, NO_CALLS, FAILED_CALL, BUSY];
  mockApi.ticketLedger.mockResolvedValue(
    ledger(
      fixtures.map((f, i) =>
        attempt({ run_id: `${f.run_id}-${i}`, run_code: `${f.run_code}${i}`, agent_id: f.agent_id, status: "running" }),
      ),
    ),
  );
  mockApi.runLog.mockImplementation(async (runId: string) => {
    const index = Number(runId.slice(-1));
    return asRunLog(fixtures[index], { id: runId, status: "running" });
  });
  renderMap();
  await screen.findByText("Latest calls");
  await waitFor(() => expect(feedRows().length).toBeGreaterThan(0));
  expect(agentRows()).toHaveLength(6);
  expect(feedRows().length).toBeLessThanOrEqual(16);
});

// ---- S9 and S10, on the rendered surface ---------------------------------------------------------

it("renders every `+N` from the act model's own callsInLog, with no second rule of its own", async () => {
  // S10. The pane's `+N` and `buildToolActs`'s `callsInLog` must be the same number, because
  // there is now only one rule that collapses anything. The old `foldFeed` in agentMap.ts was the
  // second, and it folded only `done` pairs, so the two disagreed on an `unknown`-state run.
  oneFixtureRun(BUSY);
  renderMap();
  await screen.findByText("Latest calls");

  const acts = buildToolActs(asActRun(BUSY) as never, Date.parse(BUSY.started_at), null).acts;
  const newestFirst = [...acts].sort((a, b) => a.lastAtMs - b.lastAtMs).reverse();
  const expected = newestFirst
    .slice(0, 16)
    .map((act) => act.callsInLog - 1)
    .filter((more) => more > 0);

  const rendered = await waitFor(() => {
    const found = [...feedSection().querySelectorAll("[title*='more calls at']")];
    expect(found.length).toBeGreaterThan(0);
    return found.map((n) => Number((n.textContent ?? "").replace("+", "")));
  });
  expect(rendered.sort((a, b) => a - b)).toEqual(expected.sort((a, b) => a - b));
});

it("shows a TodoWrite-only run as a Model act, never as `no calls yet`", async () => {
  // S9 where the operator meets it: `call · TodoWrite · …` is a real call line, so S7's "any
  // `call ·` line at all ⇒ calls" has to hold for it. Filtering the THINK/OUT pseudo-call by its
  // *place* also swallows this, and the pane then reports a working run as having done nothing.
  oneFixtureRun(
    {
      ...BUSY,
      lines: [
        { tag: "TOOL", text: "call · TodoWrite · 3 items", time: "10:00:01" },
        { tag: "TOOL", text: "result\nok", time: "10:00:02" },
      ],
    },
    { status: "running" },
  );
  renderMap();
  await screen.findByText("Latest calls");

  expect(await screen.findByText("✓ Model — TodoWrite")).toBeTruthy();
  expect(screen.queryByText("no calls yet")).toBeNull();
  expect(screen.queryByText(/activity unknown/)).toBeNull();
  expect(agentRow().textContent).toContain("1 calls");
  const row = feedRows()[0];
  expect(row.getAttribute("aria-label")).toContain("Model, TodoWrite, done");
});
