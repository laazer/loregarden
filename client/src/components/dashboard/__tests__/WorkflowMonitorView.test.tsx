import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

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
  ticket_title: "Fix the flaky review",
  ticket_external_id: "lor-x-1",
  ticket_state: "in_progress",
  workspace_slug: "loregarden",
  ...over,
});

function renderView(ticketId: string | null = null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <WorkflowMonitorView ticketId={ticketId} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
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
