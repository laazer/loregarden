import { fireEvent, render, screen, within } from "@testing-library/react";

import type { InitiativePlan, PlanNode } from "../../../../api/initiativeApi";
import type { ActiveRun, QueueLane } from "../../../../lib/queueSocket";
import { navigateToTicket } from "../../../../lib/useAppNavigation";
import { useQueueStatus, type QueueStatusValue } from "../../../../state/QueueStatusContext";
import { InitiativeRunningTickets } from "../InitiativeRunningTickets";

jest.mock("../../../../state/QueueStatusContext", () => ({
  useQueueStatus: jest.fn(),
}));

jest.mock("../../../../lib/useAppNavigation", () => ({
  navigateToTicket: jest.fn(),
}));

const mockUseQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

const node = (id: string, external = false): PlanNode =>
  ({ id, external_id: `lg-${id}`, title: id, milestone_id: external ? null : "m1", external }) as PlanNode;

const plan = {
  id: "init-1",
  milestones: [{ id: "m1" }],
  nodes: [node("t1"), node("t2"), node("prereq", true)],
} as unknown as InitiativePlan;

const run = (ticketId: string, extra: Partial<ActiveRun> = {}): ActiveRun => ({
  run_id: `run-${ticketId}`,
  ticket_id: ticketId,
  ticket_code: `LG-${ticketId}`,
  ticket_title: `Title ${ticketId}`,
  agent_id: "backend_implementer",
  stage_key: "implement",
  slot_number: 0,
  elapsed_seconds: 90,
  status: "running",
  ...extra,
});

const waitingEntry = {
  entry_id: "entry-1",
  ticket_id: "t2",
  ticket_title: "Queued behind it",
  workspace_id: "ws-1",
  position: 1,
  auto_approve: false,
  stop_at_stage_key: "",
  queued_at: null,
};

const withLanes = (lanes: QueueLane[]) =>
  mockUseQueueStatus.mockReturnValue({ lanes } as unknown as QueueStatusValue);

beforeEach(() => jest.clearAllMocks());

test("shows a slot card for each lane running one of the initiative's tickets, and only those", () => {
  withLanes([
    { slot_number: 1, running: run("t1"), waiting: [waitingEntry] },
    { slot_number: 2, running: run("elsewhere"), waiting: [] },
    { slot_number: 3, running: null, waiting: [] },
  ]);
  render(<InitiativeRunningTickets plan={plan} />);

  const section = screen.getByRole("region", { name: /Running now/ });
  expect(within(section).getAllByRole("listitem")).toHaveLength(1);
  expect(screen.getByTestId("initiative-running-t1")).toHaveTextContent("Slot 1");
  expect(screen.getByTestId("initiative-running-t1")).toHaveTextContent("Title t1");
  expect(screen.queryByTestId("initiative-running-elsewhere")).not.toBeInTheDocument();
});

test("shows no lane backlog and no way to add to the queue", () => {
  withLanes([{ slot_number: 1, running: run("t1"), waiting: [waitingEntry] }]);
  render(<InitiativeRunningTickets plan={plan} />);

  expect(screen.queryByText("Queued behind it")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "LG-t1 actions" }));
  expect(screen.getByRole("menuitem", { name: "Go to ticket" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Add ticket" })).not.toBeInTheDocument();
});

test("goes to the running ticket from its card", () => {
  withLanes([{ slot_number: 1, running: run("t1"), waiting: [] }]);
  render(<InitiativeRunningTickets plan={plan} />);

  fireEvent.click(screen.getByRole("button", { name: "LG-t1 actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Go to ticket" }));
  expect(navigateToTicket).toHaveBeenCalledWith("t1");
});

test("counts a ticket under the initiative that the graph does not list, by its ancestry", () => {
  withLanes([
    {
      slot_number: 2,
      running: run("deep-child", {
        ticket_ancestry: [
          { id: "init-1", code: "init-1", title: "Initiative" },
          { id: "deep-child", code: "LG-deep", title: "Deep child" },
        ],
      }),
      waiting: [],
    },
  ]);
  render(<InitiativeRunningTickets plan={plan} />);

  expect(screen.getByTestId("initiative-running-deep-child")).toBeInTheDocument();
});

test("counts a lane held above the initiative whose live descendant is its work", () => {
  withLanes([
    {
      slot_number: 1,
      running: run("outside-parent", { running_descendant: { id: "t2", code: "LG-t2", title: "Title t2" } }),
      waiting: [],
    },
  ]);
  render(<InitiativeRunningTickets plan={plan} />);

  expect(screen.getByTestId("initiative-running-outside-parent")).toBeInTheDocument();
});

test("does not count a prerequisite outside the initiative", () => {
  withLanes([{ slot_number: 1, running: run("prereq"), waiting: [] }]);
  const { container } = render(<InitiativeRunningTickets plan={plan} />);

  expect(container).toBeEmptyDOMElement();
});

test("renders nothing when none of its tickets is running", () => {
  withLanes([{ slot_number: 1, running: null, waiting: [waitingEntry] }]);
  const { container } = render(<InitiativeRunningTickets plan={plan} />);

  expect(container).toBeEmptyDOMElement();
});
