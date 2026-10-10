/**
 * The sidebar's Running section: one row per ticket holding a slot, linking to
 * that ticket, and distinct words for an idle queue and an unreachable one.
 */

import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { ActiveRun } from "../../lib/queueSocket";
import { useQueueStatus, type QueueStatusValue } from "../../state/QueueStatusContext";
import { SidebarRunningSection } from "../AppSidebarRunning";

jest.mock("../../state/QueueStatusContext", () => ({
  ...jest.requireActual("../../state/QueueStatusContext"),
  useQueueStatus: jest.fn(),
}));

const mockQueueStatus = useQueueStatus as jest.MockedFunction<typeof useQueueStatus>;

const TICKET_A = "11111111-1111-4111-8111-111111111111";
const TICKET_B = "22222222-2222-4222-8222-222222222222";

function run(overrides: Partial<ActiveRun>): ActiveRun {
  return {
    run_id: "run",
    ticket_id: TICKET_A,
    slot_number: 1,
    elapsed_seconds: 30,
    status: "running",
    agent_id: "agent",
    ticket_title: "Ticket A",
    ticket_code: "LG-1",
    ticket_state: "in_progress",
    ...overrides,
  } as ActiveRun;
}

function queue(value: Partial<QueueStatusValue>) {
  mockQueueStatus.mockReturnValue({
    activeRuns: [],
    error: null,
    loading: false,
    ...value,
  } as QueueStatusValue);
}

function renderSection(path = "/") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <SidebarRunningSection headingId="running-heading" />
    </MemoryRouter>,
  );
  return screen.getByRole("list", { name: "Running" });
}

test("one row per ticket, linking to it, in slot order", () => {
  queue({
    activeRuns: [
      run({ run_id: "b", ticket_id: TICKET_B, ticket_title: "Ticket B", slot_number: 2 }),
      run({ run_id: "a1", slot_number: 1, elapsed_seconds: 30 }),
      // Same ticket behind a second run row: still one entry.
      run({ run_id: "a2", slot_number: 1, elapsed_seconds: 90 }),
    ],
  });
  const list = renderSection();

  const links = within(list).getAllByRole("link");
  expect(links).toHaveLength(2);
  expect(links[0]).toHaveTextContent("Ticket A");
  expect(links[0]).toHaveAttribute("href", `/tickets/${TICKET_A}/diff`);
  expect(links[1]).toHaveAttribute("href", `/tickets/${TICKET_B}/diff`);
});

test("the ticket on screen is marked current", () => {
  queue({ activeRuns: [run({})] });
  const list = renderSection(`/tickets/${TICKET_A}/logs`);

  expect(within(list).getByRole("link")).toHaveAttribute("aria-current", "page");
});

test("an idle queue and an unreachable one say different things", () => {
  queue({ activeRuns: [] });
  const idle = renderSection();
  const idleText = idle.textContent;
  expect(within(idle).queryByRole("link")).toBeNull();
  expect(idleText).not.toBe("");

  document.body.innerHTML = "";
  queue({ activeRuns: [], error: "boom" });
  const failed = renderSection();
  expect(within(failed).getByRole("status")).toBeInTheDocument();
  expect(failed.textContent).not.toBe(idleText);
});
