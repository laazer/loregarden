import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WorkflowMonitorView } from "../WorkflowMonitorView";
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
  ...over,
});

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WorkflowMonitorView />
    </QueryClientProvider>,
  );
}

beforeEach(() => monitorFindings.mockReset());

it("asks for every finding, with no ticket id", async () => {
  monitorFindings.mockResolvedValue([finding()]);

  renderView();

  await waitFor(() => expect(monitorFindings).toHaveBeenCalled());
  expect(monitorFindings).toHaveBeenCalledWith(undefined);
});

it("groups recurrence and says how many tickets it spans", async () => {
  monitorFindings.mockResolvedValue([
    finding({ ticket_id: "t1" }),
    finding({ ticket_id: "t2" }),
    finding({ ticket_id: "t3" }),
  ]);

  renderView();

  expect(await screen.findByText("Stage thrash")).toBeInTheDocument();
  expect(screen.getByText(/3 tickets/)).toBeInTheDocument();
  expect(screen.getByText(/1 condition across 3 findings/)).toBeInTheDocument();
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
  expect(screen.getByText(/first seen/)).toBeInTheDocument();
});

it("names the stage alongside the condition", async () => {
  monitorFindings.mockResolvedValue([finding()]);

  renderView();

  // The stage appears twice by design — once as the group's own label and once
  // inside the summary sentence — so this asserts the label element itself.
  expect(await screen.findByText("· script_review")).toBeInTheDocument();
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
