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

  it("shows how many sweeps have seen a repeated finding", async () => {
    monitorFindings.mockResolvedValue([finding({ occurrences: 4 })]);
    renderPanel();

    expect(await screen.findByText(/seen 4×/)).toBeInTheDocument();
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
