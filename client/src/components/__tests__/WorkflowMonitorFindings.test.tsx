import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";

import type { MonitorFinding } from "../../api/types";
import { WorkflowMonitorFindings } from "../WorkflowMonitorFindings";

const monitorFindings = jest.fn();

jest.mock("../../api/client", () => ({
  api: { monitorFindings: (ticketId: string) => monitorFindings(ticketId) },
}));

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
    // Duration from first_seen is the replacement; do not pin an exact "Nd ago"
    // string against unfrozen Date.now().
    monitorFindings.mockResolvedValue([
      finding({ occurrences: 5989, first_seen: "2026-09-09T00:33:19Z" }),
    ]);
    renderPanel();

    expect(await screen.findByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
    expect(screen.getByText(/first seen/)).toBeInTheDocument();
    expect(screen.queryByText(/5989/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen \d+×/)).not.toBeInTheDocument();
    expect(screen.queryByText(/seen in \d+ sweeps/)).not.toBeInTheDocument();
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

  it("renders nothing for a ticket with no findings", async () => {
    // The monitor runs against every ticket on the reconcile timer; a panel that
    // is always present would be noise on the vast majority that are fine.
    monitorFindings.mockResolvedValue([]);
    const { container } = renderPanel();

    await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
