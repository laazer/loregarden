import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";

import type { MonitorFinding } from "../../api/types";
import { formatRelativeAge } from "../../lib/timestamps";
import { WorkflowMonitorFindings } from "../WorkflowMonitorFindings";

const monitorFindings = jest.fn();

jest.mock("../../api/client", () => ({
  api: { monitorFindings: (ticketId: string) => monitorFindings(ticketId) },
}));

/** Fixed ISO far enough in the past that formatRelativeAge is day-stable across a test. */
const FIRST_SEEN = "2026-09-09T00:33:19Z";

function finding(overrides: Partial<MonitorFinding> = {}): MonitorFinding {
  return {
    condition: "stage_thrash",
    ticket_id: "t1",
    stage_key: "implement",
    summary: "Stage 'implement' ran 9 times in one orchestration run (baseline 1.56).",
    evidence: { attempts: "9" },
    occurrences: 1,
    first_seen: null,
    last_seen: null,
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkflowMonitorFindings ticketId="t1" />
    </QueryClientProvider>,
  );
}

describe("WorkflowMonitorFindings", () => {
  beforeEach(() => monitorFindings.mockReset());

  it("shows the summary with the numbers behind it", async () => {
    monitorFindings.mockResolvedValue([finding()]);
    renderPanel();

    expect(await screen.findByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
  });

  it("never renders the occurrences count, which counts sweep ticks", async () => {
    // Live payloads carry five-figure sweep ticks (e.g. 5989). Rendering that
    // number — or "seen N×" / "seen in N sweeps" — reads as thrash events.
    // Duration from first_seen is the replacement. Exact age string is checked
    // against formatRelativeAge for this day-stable ISO (not a wall-clock flake).
    monitorFindings.mockResolvedValue([
      finding({ occurrences: 5989, first_seen: FIRST_SEEN }),
    ]);
    const { container } = renderPanel();

    expect(await screen.findByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
    expect(screen.getByText(/first seen/)).toBeInTheDocument();
    const countSpan = container.querySelector(".monitor-findings-count");
    expect(countSpan?.textContent).toBe(` first seen ${formatRelativeAge(FIRST_SEEN)}`);
    expect(screen.queryByText(/5989/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen \d+×/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen in \d+ sweeps/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows first-seen duration even when occurrences is 1 (wrong-gate killer)", async () => {
    // Half-fix: keep `occurrences > 1` as the render gate and only swap the
    // string to formatRelativeAge. A fixture of 4 alone would still pass that
    // mutation; occurrences:1 must still show duration when first_seen is set.
    monitorFindings.mockResolvedValue([finding({ occurrences: 1, first_seen: FIRST_SEEN })]);
    const { container } = renderPanel();

    expect(await screen.findByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
    expect(screen.getByText(/first seen/)).toBeInTheDocument();
    expect(container.querySelector(".monitor-findings-count")?.textContent).toBe(
      ` first seen ${formatRelativeAge(FIRST_SEEN)}`,
    );
    expect(screen.queryByText(/seen \d+×/)).not.toBeInTheDocument();
  });

  it("shows duration for a small occurrences:4 fixture without shipping a count", async () => {
    // AC2 intent: fixture scale of 4 must not become a count assertion. Duration
    // only — and the digits of 4 must not appear as a "seen 4×" secondary line.
    monitorFindings.mockResolvedValue([finding({ occurrences: 4, first_seen: FIRST_SEEN })]);
    renderPanel();

    expect(await screen.findByText(/first seen/)).toBeInTheDocument();
    expect(screen.queryByText(/seen 4×/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen in 4 sweeps/)).not.toBeInTheDocument();
  });

  it("omits the duration line when first_seen is absent", async () => {
    // Gate is first_seen truthiness only — a high occurrences value must not
    // force a secondary span (and must never leak sweep digits into the DOM).
    monitorFindings.mockResolvedValue([finding({ occurrences: 5989, first_seen: null })]);
    renderPanel();

    expect(await screen.findByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
    expect(screen.queryByText(/first seen/)).not.toBeInTheDocument();
    expect(screen.queryByText(/5989/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen \d+×/)).not.toBeInTheDocument();
  });

  it("omits the duration line when first_seen is an empty string", async () => {
    // Truthiness gate: "" is falsy — must not render a secondary span, and must
    // not fall back to interpolating occurrences.
    monitorFindings.mockResolvedValue([finding({ occurrences: 5989, first_seen: "" })]);
    renderPanel();

    expect(await screen.findByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
    expect(screen.queryByText(/first seen/)).not.toBeInTheDocument();
    expect(screen.queryByText(/5989/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen \d+×/)).not.toBeInTheDocument();
  });

  it("renders nothing for a ticket with no findings", async () => {
    // The monitor runs against every ticket on the reconcile timer; a panel that
    // is always present would be noise on the vast majority that are fine.
    monitorFindings.mockResolvedValue([]);
    const { container } = renderPanel();

    await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("stays quiet while the findings query is pending", async () => {
    monitorFindings.mockReturnValue(new Promise(() => {}));
    const { container } = renderPanel();

    await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument();
  });

  it("stays quiet when the findings query fails", async () => {
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));
    const { container } = renderPanel();

    await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
