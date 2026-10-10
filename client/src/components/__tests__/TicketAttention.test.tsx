import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { TicketDetail } from "../../api/client";
import type { MonitorFinding } from "../../api/types";
import { formatRelativeAge } from "../../lib/timestamps";
import { navigateToTicketTab } from "../../lib/useAppNavigation";
import { useReaderStore } from "../../state/readerStore";
import { TicketAttention } from "../TicketAttention";

const monitorFindings = jest.fn();

jest.mock("../../api/client", () => ({
  api: { monitorFindings: (ticketId: string) => monitorFindings(ticketId) },
}));
jest.mock("../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../lib/useAppNavigation"),
  navigateToTicketTab: jest.fn(),
}));

/** Far enough in the past that formatRelativeAge is day-stable across a test. */
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
    ticket_title: "",
    ticket_external_id: "",
    ticket_state: "in_progress",
    workspace_slug: "",
    ...overrides,
  };
}

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return { id: "t1", state: "in_progress", blocking_issues: "", block_kind: null, ...overrides } as TicketDetail;
}

function renderStrip(detail = ticket(), hasRunErrors = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TicketAttention ticket={detail} hasRunErrors={hasRunErrors} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  monitorFindings.mockResolvedValue([]);
  useReaderStore.getState().close();
});

describe("the block", () => {
  it("is one line, labelled by what it is, with the way to the detail", async () => {
    renderStrip(ticket({ state: "blocked", block_kind: "harness", blocking_issues: "Weekly usage limit reached." }));

    const strip = await screen.findByRole("region", { name: "Needs attention" });
    expect(strip).toHaveTextContent("Blocked · harness");
    expect(strip).toHaveTextContent("Weekly usage limit reached.");
    // The whole text is on screen, so there is nothing more to read.
    expect(within(strip).queryByRole("button", { name: "Read" })).not.toBeInTheDocument();

    fireEvent.click(within(strip).getByRole("button", { name: "Timeline" }));
    expect(navigateToTicketTab).toHaveBeenCalledWith("t1", "timeline");
  });

  it("labels reviewer feedback on an in-progress ticket as rework, not a block", async () => {
    renderStrip(ticket({ blocking_issues: "Missing tests for the rollback path." }));

    expect(await screen.findByRole("region", { name: "Needs attention" })).toHaveTextContent(/^Rework/);
  });

  it("shows the first line of a long block and opens the rest in the reader", async () => {
    const text = "## Gate failed\n\nruff: 3 errors\npytest: 2 failed";
    renderStrip(ticket({ state: "blocked", blocking_issues: text }));

    const strip = await screen.findByRole("region", { name: "Needs attention" });
    expect(strip).toHaveTextContent("Gate failed");
    expect(strip).not.toHaveTextContent("pytest: 2 failed");

    fireEvent.click(within(strip).getByRole("button", { name: "Read" }));
    expect(useReaderStore.getState().document).toEqual({ title: "Blocked", content: text });
  });

  it("says a run failed when there is no block text, rather than nothing", async () => {
    renderStrip(ticket(), true);

    expect(await screen.findByRole("region", { name: "Needs attention" })).toHaveTextContent("Run failed");
  });
});

describe("the monitor's findings", () => {
  it("fold into one line of short labels until opened", async () => {
    monitorFindings.mockResolvedValue([
      finding(),
      finding({ condition: "stalled_run", stage_key: "plan", summary: "Run has been RUNNING for 0.4h." }),
    ]);
    renderStrip();

    const toggle = await screen.findByRole("button", { name: /Monitor · 2/ });
    expect(toggle).toHaveTextContent("stage thrash · implement, stalled run · plan");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/ran 9 times/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/ran 9 times in one orchestration run/)).toBeInTheDocument();
    expect(screen.getByText(/RUNNING for 0.4h/)).toBeInTheDocument();
  });

  it("dates a finding by first-seen age and never shows the sweep-tick count", async () => {
    // `occurrences` counts reconcile sweeps — five figures live — and reads as thrash.
    monitorFindings.mockResolvedValue([finding({ occurrences: 5989, first_seen: FIRST_SEEN })]);
    renderStrip();

    fireEvent.click(await screen.findByRole("button", { name: /Monitor · 1/ }));
    expect(screen.getByText(`first seen ${formatRelativeAge(FIRST_SEEN)}`, { exact: false })).toBeInTheDocument();
    expect(screen.queryByText(/5989/)).not.toBeInTheDocument();
  });

  it("omits the age when first_seen is absent", async () => {
    monitorFindings.mockResolvedValue([finding({ first_seen: null })]);
    renderStrip();

    fireEvent.click(await screen.findByRole("button", { name: /Monitor · 1/ }));
    expect(screen.queryByText(/first seen/)).not.toBeInTheDocument();
  });
});

describe("when nothing needs attention", () => {
  it("renders nothing", async () => {
    const { container } = renderStrip();

    await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("stays quiet while findings load or when the monitor endpoint fails", async () => {
    monitorFindings.mockRejectedValue(new Error("monitor endpoint is down"));
    const { container } = renderStrip();

    await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
