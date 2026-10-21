import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { TicketDetail } from "../../../api/client";
import type { TicketDependencyRef, WaitingOnRef } from "../../../api/types";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { TicketWaitingOn } from "../TicketWaitingOn";

const orchestrate = jest.fn();

jest.mock("../../../api/client", () => ({
  api: { orchestrate: (...args: unknown[]) => orchestrate(...args) },
}));
jest.mock("../../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../../lib/useAppNavigation"),
  navigateToTicket: jest.fn(),
}));

function ref(id: string, overrides: Partial<WaitingOnRef> = {}): WaitingOnRef {
  return {
    id,
    external_id: `lg-x-${id}`,
    title: `Ticket ${id}`,
    state: "in_progress",
    reason: "prerequisite",
    blocked_on: null,
    workspace_slug: "loregarden",
    ...overrides,
  };
}

function dep(id: string, overrides: Partial<TicketDependencyRef> = {}): TicketDependencyRef {
  return {
    id,
    external_id: `lg-x-${id}`,
    title: `Ticket ${id}`,
    state: "in_progress",
    work_item_type: "task",
    is_integration_review: false,
    workspace_slug: "loregarden",
    unmet_reason: "not_done",
    landing_target: "",
    overridden: false,
    ...overrides,
  };
}

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    external_id: "lg-x-1315",
    state: "backlog",
    workspace_slug: "loregarden",
    blocked_on: "tickets",
    activity: "idle",
    waiting_on: [],
    root_blockers: [],
    dependencies: [],
    ...overrides,
  } as TicketDetail;
}

function renderPanel(detail: TicketDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <div role="region" aria-label="panel">
        <TicketWaitingOn ticket={detail} />
      </div>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  orchestrate.mockResolvedValue({ id: "t1" });
});

it("renders nothing for a ticket that waits on nothing", () => {
  renderPanel(ticket({ blocked_on: null }));
  expect(screen.getByRole("region", { name: "panel" })).toBeEmptyDOMElement();
});

it("leads with the root of a deeper chain and names what it holds this through", () => {
  const middle = ref("1314", { blocked_on: "tickets" });
  const root = ref("1237", { state: "blocked", blocked_on: "person" });
  renderPanel(ticket({ waiting_on: [middle], root_blockers: [root], dependencies: [dep("1314")] }));

  const panel = screen.getByRole("region", { name: "panel" });
  expect(panel).toHaveTextContent(/Waiting on\s*lg-x-1237 · needs you\s*through\s*lg-x-1314/);
  fireEvent.click(within(panel).getByRole("button", { name: "lg-x-1237 · needs you" }));
  expect(navigateToTicket).toHaveBeenCalledWith("1237");
});

it("names a root that is also a direct prerequisite once, first", () => {
  const root = ref("900", { blocked_on: "person" });
  renderPanel(
    ticket({
      waiting_on: [ref("74", { blocked_on: "tickets" }), root],
      root_blockers: [root],
      dependencies: [dep("74"), dep("900")],
    }),
  );
  const panel = screen.getByRole("region", { name: "panel" });
  expect(within(panel).getAllByRole("button", { name: /lg-x-900/ })).toHaveLength(1);
  expect(panel).not.toHaveTextContent("through");
  expect(within(panel).getAllByRole("button", { name: /^lg-x-/ })[0]).toHaveTextContent("lg-x-900");
});

it("names a prerequisite in another workspace with its workspace", () => {
  renderPanel(ticket({ waiting_on: [ref("9", { workspace_slug: "blobert" })], dependencies: [dep("9")] }));
  expect(screen.getByRole("button", { name: "blobert: lg-x-9" })).toBeInTheDocument();
});

it("says a done prerequisite that never landed is what holds it, and where it must land", () => {
  renderPanel(
    ticket({
      blocked_on: null,
      dependencies: [dep("7", { state: "done", unmet_reason: "not_landed", landing_target: "integration/lg-x-491" })],
    }),
  );
  const panel = screen.getByRole("region", { name: "panel" });
  expect(panel).toHaveTextContent("Not landed");
  expect(panel).toHaveTextContent("is done; its work is not on integration/lg-x-491");
});

it("starts it anyway only after a person confirms, and says what it skips", async () => {
  renderPanel(ticket({ waiting_on: [ref("1314")], dependencies: [dep("1314")] }));

  fireEvent.click(screen.getByRole("button", { name: "Start anyway" }));
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("It waits on lg-x-1314");
  expect(orchestrate).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole("button", { name: "Start anyway" }));
  await waitFor(() => expect(orchestrate).toHaveBeenCalledWith("t1", { ignore_prerequisites: true }));
});

it("offers no override while the ticket is running or past every prerequisite", () => {
  renderPanel(ticket({ activity: "running", waiting_on: [ref("1314")], dependencies: [dep("1314")] }));
  expect(screen.queryByRole("button", { name: "Start anyway" })).not.toBeInTheDocument();
});
