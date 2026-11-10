import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { LedgerAttempt, LedgerVisit } from "../../../api/types";
import { TicketFailurePanel, type FailureRun } from "../TicketFailurePanel";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function attempt(overrides: Partial<LedgerAttempt> = {}): LedgerAttempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "test_designer",
    skill_name: "",
    status: "failed",
    started_at: "2026-10-24T11:35:00+00:00",
    finished_at: "2026-10-24T11:35:02+00:00",
    duration_seconds: 2,
    error_tail: "",
    ...overrides,
  };
}

function visit(stage_key: string, attempts: LedgerAttempt[]): LedgerVisit {
  return { stage_key, visit_number: 1, status: attempts[attempts.length - 1].status, is_parallel: false, attempts };
}

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    state: "in_progress",
    stages: [
      { key: "spec", name: "Specification" },
      { key: "test_design", name: "Design tests" },
      { key: "gate", name: "Quality Gate" },
    ],
    blocking_issues: "",
    child_count: 0,
    artifacts: {},
    ...overrides,
  } as unknown as TicketDetail;
}

const LAUNCH = [
  "Run run_d4 failed",
  "Agent launch failed: command exceeded the system limit",
  "tmux: command too long",
].join("\n");

function renderPanel({
  detail = ticket(),
  visits = [] as LedgerVisit[],
  runs = [] as FailureRun[],
  onOpenRunLog = jest.fn(),
} = {}) {
  mockApi.ticketLedger.mockResolvedValue({ visits, total_runs: 0, reworked_stages: [], total_seconds: 0 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <TicketFailurePanel ticket={detail} runs={runs} isActive={false} onOpenRunLog={onOpenRunLog} />
    </QueryClientProvider>,
  );
  return { ...view, onOpenRunLog };
}

beforeEach(() => jest.clearAllMocks());

it("leads with the failing stage, which attempt it was, and that run's own output", async () => {
  const { onOpenRunLog } = renderPanel({
    detail: ticket({ state: "blocked", block_message: "Agent launch failed" } as Partial<TicketDetail>),
    visits: [
      visit("spec", [attempt({ run_id: "s1", status: "succeeded" })]),
      visit("test_design", [
        attempt({ run_id: "d1", run_code: "run_d1", error_tail: LAUNCH }),
        attempt({ run_id: "d2", run_code: "run_d2", error_tail: LAUNCH }),
      ]),
    ],
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(within(panel).getByRole("heading", { name: "Design tests" })).toBeInTheDocument();
  expect(panel).toHaveTextContent(/Attempt 2 of 2 · test_designer · .* · 2s/);
  expect(panel).toHaveTextContent("run_d2");

  const lines = within(within(panel).getByRole("list", { name: "Failure output" })).getAllByRole("listitem");
  expect(lines.map((line) => line.textContent)).toEqual(LAUNCH.split("\n"));
  // The two lines that state the error are marked; the narration is not.
  expect(lines.map((line) => line.classList.contains("is-error"))).toEqual([false, true, true]);

  fireEvent.click(within(panel).getByRole("button", { name: "Full log" }));
  expect(onOpenRunLog).toHaveBeenCalledWith("d2");
  expect(within(panel).getByRole("button", { name: "Copy the failure output" })).toBeInTheDocument();
});

it("collapses the run output behind its title and opens it again", async () => {
  renderPanel({
    detail: ticket({ state: "blocked", block_message: "Agent launch failed" } as Partial<TicketDetail>),
    visits: [visit("test_design", [attempt({ run_id: "d1", run_code: "run_d1", error_tail: LAUNCH })])],
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  const toggle = within(panel).getByRole("button", { name: "Agent run output" });
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(within(panel).getByRole("list", { name: "Failure output" })).toBeVisible();

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(within(panel).queryByRole("list", { name: "Failure output" })).not.toBeInTheDocument();
  // Copy and the log stay reachable while the output is folded away.
  expect(within(panel).getByRole("button", { name: "Full log" })).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(within(panel).getByRole("list", { name: "Failure output" })).toBeVisible();
});

it("shows the ticket's message when the run recorded no output, opening the log by its run code", async () => {
  const { onOpenRunLog } = renderPanel({
    detail: ticket({
      blocking_issues: "Gate failed: 2 commands",
      artifacts: { error: { message: "Gate failed: 2 commands", run_code: "run_zz", agent_id: "gatekeeper", stage_key: "gate", command: "" } },
    } as Partial<TicketDetail>),
    runs: [{ id: "g1", run_code: "run_zz" }],
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(within(panel).getByRole("heading", { name: "Quality Gate" })).toBeInTheDocument();
  expect(panel).toHaveTextContent("What stopped it");
  expect(panel).toHaveTextContent("Gate failed: 2 commands");
  fireEvent.click(within(panel).getByRole("button", { name: "Full log" }));
  expect(onOpenRunLog).toHaveBeenCalledWith("g1");
});

it("shows a long block's full text, not the pointer back to the Timeline", async () => {
  renderPanel({
    detail: ticket({
      state: "blocked",
      blocking_issues: "Stage 'test-break' hit a blocking issue — see the Timeline tab for details.",
      block_message: "No failing test could be written: the module under test does not exist.",
    } as Partial<TicketDetail>),
  });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(panel).toHaveTextContent("the module under test does not exist");
  expect(panel).not.toHaveTextContent("see the Timeline tab");
});

it("never calls a ticket blocked on the strength of an error artifact alone", async () => {
  renderPanel({
    detail: ticket({
      artifacts: { error: { message: "Run exited 1", run_code: "run_zz", agent_id: "implementer", stage_key: "implement", command: "" } },
    } as Partial<TicketDetail>),
  });

  const panel = await screen.findByRole("region", { name: "Last run failed" });
  expect(panel).toHaveTextContent("Run exited 1");
  expect(screen.queryByRole("region", { name: "Blocking issue" })).toBeNull();
});

it("shows a blocked ticket as blocked even when no reason was recorded", async () => {
  renderPanel({ detail: ticket({ state: "blocked" } as Partial<TicketDetail>) });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(panel).toHaveTextContent(/No reason was recorded/);
  expect(within(panel).queryByRole("button", { name: "Full log" })).toBeNull();
});

it("explains a parent blocked by its children rather than pointing at a run", async () => {
  renderPanel({ detail: ticket({ state: "blocked", child_count: 4 } as Partial<TicketDetail>) });

  const panel = await screen.findByRole("region", { name: "Blocking issue" });
  expect(panel).toHaveTextContent(/work under it is blocked/);
});

it("renders nothing while nothing has stopped the ticket", async () => {
  const { container } = renderPanel({
    visits: [visit("test_design", [attempt({ run_id: "d1", error_tail: "boom" }), attempt({ run_id: "d2", status: "succeeded" })])],
  });

  await waitFor(() => expect(mockApi.ticketLedger).toHaveBeenCalledWith("t1"));
  await act(async () => {});
  expect(container).toBeEmptyDOMElement();
});

/**
 * lg-milestone-that-2215 — `.tf-lines` is a bounded scrolling region, and the
 * keys that scroll it need something in the subtree to scroll FROM.
 *
 * The region is already `tabIndex={0}` and already carries `aria-label`; what
 * this ticket adds is the shared `scroll-focus-ring` class, so the ring is
 * defined once in `index.css` rather than copied into three stylesheets. The
 * rest of these assertions are the behaviour that must survive that change:
 * the exposed role (AC6 — it is an `<ol>`, so `list`, NEVER `group`), the
 * direction the surface actually offers a tab stop in (AC3), the empty case
 * (AC8), and the absence of any handler of its own (AC9).
 *
 * Measured, read-only on the live database, for why the bound is the defect
 * and not the fix: 490 `agent_runs` rows are `failed`, the largest `stderr` is
 * 8,638 chars over 97 lines, and `max-height: 240px` at `--fs-small`/1.6 shows
 * about 12 of them. The bound stays; `Full log` stays the route to the whole
 * thing. This region is a tail, not a log viewer.
 */
describe("the failure output region", () => {
  const withRun = {
    detail: ticket({ state: "blocked", block_message: "Agent launch failed" } as Partial<TicketDetail>),
    visits: [visit("test_design", [attempt({ run_id: "d1", run_code: "run_d1", error_tail: LAUNCH })])],
  };
  // No run and no error artifact, so `runId` is undefined and `Full log` is
  // not rendered. Which of the two it is decides where Shift+Tab lands, so the
  // fixture states it rather than leaving it to whatever the suite happens to
  // build.
  const withoutRun = {
    detail: ticket({
      state: "blocked",
      blocking_issues: "Stage 'implement' hit a blocking issue",
      block_message: LAUNCH,
    } as Partial<TicketDetail>),
  };

  const region = async () =>
    within(await screen.findByRole("region", { name: "Blocking issue" })).getByRole("list", {
      name: "Failure output",
    });

  it("exposes the list role and its name, and wears the shared ring class", async () => {
    renderPanel(withRun);

    const lines = await region();
    // AC6/AC2: an <ol> maps to `list`. `role="group"` would override it and
    // cost the "list, N items" announcement, so it must NOT be here.
    expect(lines.tagName).toBe("OL");
    expect(lines).not.toHaveAttribute("role");
    expect(lines).toHaveAttribute("tabindex", "0");
    expect(lines).toHaveClass("scroll-focus-ring");
  });

  it("is reachable after the disclosure is opened again", async () => {
    // The region is `hidden={!outputOpen}`, so a test that never touches the
    // toggle is only ever asserting the default. Close it and open it: the
    // same four things must hold on the way back.
    renderPanel(withRun);
    const panel = await screen.findByRole("region", { name: "Blocking issue" });
    const toggle = within(panel).getByRole("button", { name: "Agent run output" });

    fireEvent.click(toggle);
    expect(within(panel).queryByRole("list", { name: "Failure output" })).toBeNull();

    fireEvent.click(toggle);
    const lines = within(panel).getByRole("list", { name: "Failure output" });
    expect(lines).toHaveAttribute("tabindex", "0");
    expect(lines).toHaveClass("scroll-focus-ring");
  });

  it("gives back the Full log button on Shift+Tab, where the fixture has a run", async () => {
    // AC3 is DIRECTIONAL. `.tf-out-head` sits above the output, so there is no
    // forward control to assert: Tab from the region leaves the panel. The
    // control the surface leads to is behind it.
    renderPanel(withRun);
    const lines = await region();
    lines.focus();
    expect(document.activeElement).toBe(lines);

    await userEvent.tab({ shift: true });

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Full log" }));
  });

  it("gives back Copy on Shift+Tab, where the fixture has no run", async () => {
    // Same direction, different last control: with no `runId` the `Full log`
    // button is not rendered at all, so Copy is the stop behind the region.
    renderPanel(withoutRun);
    const lines = await region();
    lines.focus();

    await userEvent.tab({ shift: true });

    expect(screen.queryByRole("button", { name: "Full log" })).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Copy the failure output" }),
    );
  });

  it("renders no labelled box when there is nothing in it", async () => {
    // AC8. A tab stop that announces a name with nothing behind it is a dead
    // stop, and indistinguishable from the failure state. The substitute is
    // one line of text IN ITS PLACE.
    renderPanel({ detail: ticket({ state: "blocked" } as Partial<TicketDetail>) });

    const panel = await screen.findByRole("region", { name: "Blocking issue" });
    expect(within(panel).queryByRole("list", { name: "Failure output" })).toBeNull();
    expect(panel).toHaveTextContent(/No reason was recorded/);
  });

  it("binds no key of its own, so the browser's own scrolling is what runs", async () => {
    // AC9. Arrow keys, Page Up/Down, Home/End and Space scroll a focused
    // overflow container natively; anything bound here would be competing with
    // that. jsdom lays nothing out, so what it can observe is that none of
    // these keys moves focus, changes the disclosure, or is swallowed.
    renderPanel(withRun);
    const lines = await region();
    lines.focus();

    for (const key of ["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "]) {
      const event = createEvent.keyDown(lines, { key, bubbles: true });
      fireEvent(lines, event);
      expect(event.defaultPrevented).toBe(false);
      expect(document.activeElement).toBe(lines);
    }

    // Still open, still the same node: nothing here is a toggle.
    expect(screen.getByRole("list", { name: "Failure output" })).toBe(lines);
  });

  it("is never focused for the operator, on mount or on a refetch", async () => {
    // AC9. The region is reached by the operator's own Tab and no other way.
    // lg-milestone-that-2205 is the ticket for the opposite mistake.
    const { rerender } = renderPanel(withRun);
    await region();
    expect(document.activeElement).toBe(document.body);

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={client}>
        <TicketFailurePanel ticket={withRun.detail} runs={[]} isActive onOpenRunLog={jest.fn()} />
      </QueryClientProvider>,
    );
    await act(async () => {});

    expect(document.activeElement).toBe(document.body);
  });
});
