/**
 * The first surfaces an agent can drive: page navigation and opening a ticket
 * from anywhere, and the open ticket's writes only while it is open — through
 * the same `api.updateTicket` call the ticket modal makes, refused for any
 * ticket the operator is not looking at.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { uiActionRegistry } from "../../lib/agentActions/registry";
import { TicketAgentActions } from "../TicketAgentActions";
import { AgentActionHost } from "../AgentActionHost";

const ticket = jest.fn();
const updateTicket = jest.fn();
const triggerAutoFix = jest.fn();

jest.mock("../../api/client", () => ({
  API_BASE: "http://127.0.0.1:8000",
  api: {
    ticket: (id: string) => ticket(id),
    updateTicket: (id: string, body: unknown) => updateTicket(id, body),
    triggerAutoFix: (id: string) => triggerAutoFix(id),
  },
}));

const OPEN = { id: "11111111-1111-1111-1111-111111111111", external_id: "lg-open-1", title: "Open" };

let location = "";
function Where() {
  location = useLocation().pathname;
  return null;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AgentActionHost />
        <Where />
        <Routes>
          <Route path="/tickets/:ticketId/:tab" element={<TicketAgentActions />} />
          <Route path="*" element={null} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  // The host opens a real socket on mount; nothing answers in a test, and the
  // protocol itself is covered in uiActionSocket.test.ts.
  (globalThis as { WebSocket?: unknown }).WebSocket = class {
    readyState = 0;
    close() {}
  };
});

beforeEach(() => {
  ticket.mockReset().mockResolvedValue(OPEN);
  triggerAutoFix.mockReset().mockResolvedValue({ status: "started", attempt_number: 2 });
  updateTicket.mockReset().mockImplementation(async (id: string, body: object) => ({ ...OPEN, id, ...body }));
});

it("offers navigation everywhere, and ticket writes only on a ticket route", async () => {
  const { unmount } = renderAt("/queue");
  expect(uiActionRegistry.available()).toEqual(["navigate.page", "ticket.open"]);
  unmount();

  const view = renderAt(`/tickets/${OPEN.id}/diff`);
  await waitFor(() => expect(uiActionRegistry.available()).toContain("ticket.set_state"));
  view.unmount();
  expect(uiActionRegistry.available()).toEqual([]);
});

it("navigates to a page and to a ticket", async () => {
  const view = renderAt("/");
  await act(() => uiActionRegistry.run("navigate.page", { page: "queue" }));
  await waitFor(() => expect(location).toBe("/queue"));
  await act(() => uiActionRegistry.run("ticket.open", { ticket_id: "lg-open-1" }));
  await waitFor(() => expect(location).toBe("/tickets/lg-open-1/diff"));
  view.unmount();
});

it("writes the open ticket through api.updateTicket, by uuid or external id", async () => {
  const view = renderAt(`/tickets/${OPEN.id}/diff`);
  await waitFor(() => expect(ticket).toHaveBeenCalled());
  await waitFor(async () =>
    expect(await uiActionRegistry.run("ticket.set_state", { ticket_id: "lg-open-1", state: "parked" })).toEqual({
      ticket_id: OPEN.id,
      state: "parked",
    }),
  );
  expect(updateTicket).toHaveBeenCalledWith(OPEN.id, { state: "parked" });

  await uiActionRegistry.run("ticket.update", { ticket_id: OPEN.id, title: "Renamed" });
  expect(updateTicket).toHaveBeenLastCalledWith(OPEN.id, { title: "Renamed" });
  view.unmount();
});

it("refuses a ticket the operator is not looking at, and writes nothing", async () => {
  const view = renderAt(`/tickets/${OPEN.id}/diff`);
  await waitFor(() => expect(ticket).toHaveBeenCalled());
  await waitFor(() =>
    expect(uiActionRegistry.run("ticket.set_state", { ticket_id: "lg-other-2", state: "done" })).rejects.toThrow(
      "lg-other-2 is not the open ticket (lg-open-1); open it first with ticket.open",
    ),
  );
  expect(updateTicket).not.toHaveBeenCalled();
  view.unmount();
});

it("starts the open ticket's CI auto-fix, and relays its attempt — or refuses another ticket", async () => {
  const view = renderAt(`/tickets/${OPEN.id}/diff`);
  await waitFor(() => expect(ticket).toHaveBeenCalled());
  await waitFor(async () =>
    expect(await uiActionRegistry.run("ticket.trigger_auto_fix", { ticket_id: "lg-open-1" })).toEqual({
      ticket_id: OPEN.id,
      status: "started",
      attempt_number: 2,
    }),
  );
  expect(triggerAutoFix).toHaveBeenCalledWith(OPEN.id);

  await expect(uiActionRegistry.run("ticket.trigger_auto_fix", { ticket_id: "lg-other-2" })).rejects.toThrow(
    "lg-other-2 is not the open ticket",
  );
  expect(triggerAutoFix).toHaveBeenCalledTimes(1);
  view.unmount();
});

it("relays an auto-fix failure to the agent instead of reporting success", async () => {
  triggerAutoFix.mockRejectedValue(new Error("no failing CI run to fix"));
  const view = renderAt(`/tickets/${OPEN.id}/diff`);
  await waitFor(() => expect(ticket).toHaveBeenCalled());
  await waitFor(() =>
    expect(uiActionRegistry.run("ticket.trigger_auto_fix", { ticket_id: OPEN.id })).rejects.toThrow(
      "no failing CI run to fix",
    ),
  );
  view.unmount();
});
