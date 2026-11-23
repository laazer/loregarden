import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QueueHistoricalAnalytics, type RunMetrics } from "../QueueHistoricalAnalytics";

function metric(overrides: Partial<RunMetrics> = {}): RunMetrics {
  return {
    ticket_type: "feature",
    count: 42,
    avg_duration_seconds: 245,
    min_duration_seconds: 50,
    max_duration_seconds: 420,
    success_rate: 0.9,
    last_7_days_count: 8,
    last_7_days_success_rate: 0.75,
    ...overrides,
  };
}

const fetchMock = jest.fn();

const ok = (metrics: RunMetrics[]) => ({ ok: true, status: 200, json: async () => ({ metrics }) });
const refused = (detail: string) => ({
  ok: false,
  status: 500,
  statusText: "Internal Server Error",
  text: async () => JSON.stringify({ detail }),
});
const requested = () => fetchMock.mock.calls.map(([url]) => String(url).replace(/^https?:\/\/[^/]+/, ""));
const summary = (label: string) =>
  screen.getByText(label).closest(".summary-card")!.querySelector(".summary-value")!.textContent;
const row = (label: string) =>
  screen.getByText(label).closest(".metric-row")!.querySelector(".metric-value")!.textContent;

beforeEach(() => {
  fetchMock.mockReset().mockResolvedValue(ok([metric()]));
  global.fetch = fetchMock;
});

describe("what it asks for", () => {
  it("is the last 7 days across every workspace, unless told otherwise", async () => {
    render(<QueueHistoricalAnalytics />);
    await screen.findByText("feature");
    expect(requested()).toEqual(["/api/parallel/analytics?range=7d"]);
    expect(screen.getByRole("button", { name: "Last 7 days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("is one workspace when given one", async () => {
    render(<QueueHistoricalAnalytics workspaceId="ws-1" />);
    await screen.findByText("feature");
    expect(requested()).toEqual(["/api/parallel/workspace/ws-1/analytics?range=7d"]);
  });

  it.each([
    ["Last 30 days", "30d"],
    ["Last 90 days", "90d"],
  ])("becomes %s when that is chosen", async (name, range) => {
    render(<QueueHistoricalAnalytics />);
    await screen.findByText("feature");
    fetchMock.mockResolvedValue(ok([metric({ ticket_type: "bug" })]));

    await userEvent.click(screen.getByRole("button", { name }));
    expect(await screen.findByText("bug")).toBeInTheDocument();
    expect(screen.queryByText("feature")).not.toBeInTheDocument();
    expect(requested()[1]).toBe(`/api/parallel/analytics?range=${range}`);
    expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Last 7 days" })).toHaveAttribute("aria-pressed", "false");
  });

  it("shows the range chosen last, however late an earlier answer arrives", async () => {
    let answerFirst: (response: unknown) => void = () => {};
    fetchMock.mockReturnValueOnce(new Promise((resolve) => (answerFirst = resolve)));
    render(<QueueHistoricalAnalytics />);
    fetchMock.mockResolvedValue(ok([metric({ ticket_type: "bug" })]));
    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    await screen.findByText("bug");

    await act(async () => answerFirst(ok([metric({ ticket_type: "milestone" })])));
    expect(screen.getByText("bug")).toBeInTheDocument();
    expect(screen.queryByText("milestone")).not.toBeInTheDocument();
  });

  it("stays loading when an abandoned range fails first", async () => {
    let failFirst: (reason: Error) => void = () => {};
    fetchMock.mockReturnValueOnce(new Promise((_resolve, reject) => (failFirst = reject)));
    render(<QueueHistoricalAnalytics />);
    fetchMock.mockReturnValue(new Promise(() => {}));
    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));

    await act(async () => failFirst(new Error("gone")));
    expect(screen.getByText("Loading analytics...")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("is not replaced by an abandoned range's late failure", async () => {
    let failFirst: (reason: Error) => void = () => {};
    fetchMock.mockReturnValueOnce(new Promise((_resolve, reject) => (failFirst = reject)));
    render(<QueueHistoricalAnalytics />);
    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    await screen.findByText("feature");

    await act(async () => failFirst(new Error("gone")));
    expect(screen.getByText("feature")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("a ticket type's card", () => {
  it("shows its figures", async () => {
    render(<QueueHistoricalAnalytics />);
    await screen.findByText("feature");
    expect(row("Total Runs")).toBe("42");
    expect(row("Avg Duration")).toBe("4m");
    expect(row("Range")).toBe("50s — 7m");
    expect(row("Last 7 Days")).toBe("8 runs (75%)");
  });

  it.each([
    [1, "excellent"],
    [0.95, "excellent"],
    [0.94, "good"],
    [0.85, "good"],
    [0.84, "fair"],
    [0.75, "fair"],
    [0.74, "poor"],
  ])("grades a success rate of %p as %s", async (success_rate, grade) => {
    fetchMock.mockResolvedValue(ok([metric({ success_rate })]));
    const { container } = render(<QueueHistoricalAnalytics />);
    await screen.findByText("feature");
    const badge = container.querySelector(".success-badge")!;
    expect(badge).toHaveClass(grade);
    expect(badge).toHaveTextContent(`${Math.round(success_rate * 100)}%`);
    expect(container.querySelector(".success-fill")).toHaveClass(grade);
  });

  it.each([
    [{ avg_duration_seconds: 119, success_rate: 0.9 }, ["⚡ Fast execution"]],
    [{ avg_duration_seconds: 120, success_rate: 0.95 }, ["✓ Highly reliable"]],
    [{ avg_duration_seconds: 120, success_rate: 0.85 }, []],
    [{ avg_duration_seconds: 120, success_rate: 0.84 }, ["⚠️ Success rate needs attention"]],
  ])("remarks on %p with %p", async (figures, remarks) => {
    fetchMock.mockResolvedValue(ok([metric(figures)]));
    const { container } = render(<QueueHistoricalAnalytics />);
    await screen.findByText("feature");
    expect([...container.querySelectorAll(".insight")].map((el) => el.textContent)).toEqual(remarks);
  });
});

describe("the summary", () => {
  it("weighs each type by how many runs it had", async () => {
    fetchMock.mockResolvedValue(
      ok([
        metric({ ticket_type: "task", count: 99, success_rate: 1, avg_duration_seconds: 60 }),
        metric({ ticket_type: "bug", count: 1, success_rate: 0, avg_duration_seconds: 6060 }),
      ]),
    );
    render(<QueueHistoricalAnalytics />);
    await screen.findByText("task");
    expect(summary("Overall Success Rate")).toBe("99%");
    expect(summary("Average Run Duration")).toBe("2m");
    expect(summary("Total Runs Completed")).toBe("100");
    expect(summary("Ticket Types Tracked")).toBe("2");
  });
});

describe("with nothing to show", () => {
  const noSummary = () => expect(screen.queryByText("Overall Success Rate")).not.toBeInTheDocument();

  it("says it is loading, and shows no figures from the range before", async () => {
    render(<QueueHistoricalAnalytics />);
    await screen.findByText("Overall Success Rate");
    fetchMock.mockReturnValue(new Promise(() => {}));

    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    expect(screen.getByText("Loading analytics...")).toBeInTheDocument();
    expect(screen.queryByText("feature")).not.toBeInTheDocument();
    noSummary();
  });

  it("says there is no history when there is none", async () => {
    fetchMock.mockResolvedValue(ok([]));
    render(<QueueHistoricalAnalytics />);
    expect(await screen.findByText("No run history available yet")).toBeInTheDocument();
    noSummary();
  });

  it("shows the server's reason for a failure, never the empty state, and retries", async () => {
    fetchMock.mockResolvedValue(refused("Could not retrieve analytics: disk I/O error"));
    render(<QueueHistoricalAnalytics />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not retrieve analytics: disk I/O error");
    expect(screen.queryByText("No run history available yet")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading analytics...")).not.toBeInTheDocument();

    fetchMock.mockResolvedValue(ok([metric()]));
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("feature")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("drops the figures of a range that loaded when the next one fails", async () => {
    render(<QueueHistoricalAnalytics />);
    await screen.findByText("feature");
    fetchMock.mockRejectedValue(new Error("Failed to fetch"));

    await userEvent.click(screen.getByRole("button", { name: "Last 90 days" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
    await waitFor(noSummary);
    expect(screen.queryByText("feature")).not.toBeInTheDocument();
  });
});
