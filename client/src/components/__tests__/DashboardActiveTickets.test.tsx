/**
 * The Dashboard's answer to "what is running right now".
 *
 * The behaviours worth pinning are the ones the single-ticket page could not
 * express: more than one ticket at once, one entry per ticket rather than per
 * run, and nothing at all when the machine is idle.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { DashboardActiveTickets } from "../DashboardActiveTickets";
import { useQueueStatus, type QueueStatusValue } from "../../state/QueueStatusContext";

jest.mock("../../state/QueueStatusContext", () => ({
  useQueueStatus: jest.fn(),
}));

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

function activeRun(overrides: Record<string, unknown>) {
  return {
    run_id: "run-1",
    ticket_id: "ticket-1",
    ticket_title: "Bootstrap vertical slice",
    ticket_code: "LG-101",
    ticket_state: "in_progress",
    agent_id: "backend_implementer",
    slot_number: 1,
    elapsed_seconds: 120,
    status: "running",
    ...overrides,
  };
}

function setActiveRuns(runs: ReturnType<typeof activeRun>[]) {
  mockUseQueueStatus.mockReturnValue({
    activeRuns: runs,
    queuedRuns: [],
    lanes: [],
    workspaces: [],
    workspacesLoading: false,
    stats: {
      max_concurrent: 3,
      active_count: runs.length,
      available_slots: 3 - runs.length,
      queued_count: 0,
      total_slots_occupied: runs.length,
      longest_wait_seconds: 0,
    },
    estimatedClearSeconds: null,
    estimatedWaitSeconds: null,
    isWebSocket: true,
    loading: false,
    onQueueEvent: () => () => {},
  } as unknown as QueueStatusValue);
}

test("shows every ticket holding a slot, not just one", () => {
  setActiveRuns([
    activeRun({}),
    activeRun({
      run_id: "run-2",
      ticket_id: "ticket-2",
      ticket_code: "LG-102",
      ticket_title: "Wire the approval gate",
      slot_number: 2,
    }),
  ]);

  render(<DashboardActiveTickets onSelect={() => {}} />);

  expect(screen.getByTestId("active-ticket-count")).toHaveTextContent("2");
  expect(screen.getByText("LG-101")).toBeInTheDocument();
  expect(screen.getByText("LG-102")).toBeInTheDocument();
});

test("a ticket whose lane spans several runs appears once", () => {
  setActiveRuns([
    activeRun({ run_id: "run-a", elapsed_seconds: 30 }),
    activeRun({ run_id: "run-b", elapsed_seconds: 300 }),
  ]);

  render(<DashboardActiveTickets onSelect={() => {}} />);

  expect(screen.getByTestId("active-ticket-count")).toHaveTextContent("1");
  // The longer-running row wins, because that is the lane's own age.
  expect(screen.getByText(/5m 0s/)).toBeInTheDocument();
});

test("renders nothing when the machine is idle", () => {
  setActiveRuns([]);

  const { container } = render(<DashboardActiveTickets onSelect={() => {}} />);

  expect(container).toBeEmptyDOMElement();
});

test("clicking a ticket selects it", () => {
  setActiveRuns([activeRun({})]);
  const onSelect = jest.fn();

  render(<DashboardActiveTickets onSelect={onSelect} />);
  fireEvent.click(screen.getByText("LG-101"));

  expect(onSelect).toHaveBeenCalledWith("ticket-1");
});
