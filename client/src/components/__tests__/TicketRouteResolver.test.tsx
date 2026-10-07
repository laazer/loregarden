import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { useTicketRefStore } from "../../state/ticketRefStore";
import { TicketRouteResolver } from "../TicketRouteResolver";

jest.mock("../../api/client", () => ({
  api: { ticket: jest.fn() },
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { api } = jest.requireMock("../../api/client") as { api: { ticket: jest.Mock } };

const UUID = "41aac2d7-26a6-4f0b-988a-fc220d8dfa6c";

function CurrentPath() {
  const location = useLocation();
  return <span data-testid="path">{`${location.pathname}${location.search}`}</span>;
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route
            path="/tickets/:ticketId/:artifactTab"
            element={
              <TicketRouteResolver>
                <TicketPage />
              </TicketRouteResolver>
            }
          />
        </Routes>
        <CurrentPath />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const REF = "lor-mcp-gateway-142";

/** Counts mounts, so a test can tell an address swap from a page reload. */
let pageMounts = 0;
function TicketPage() {
  useEffect(() => {
    pageMounts += 1;
  }, []);
  return <div data-testid="ticket-page">ticket page</div>;
}

beforeEach(() => {
  api.ticket.mockReset();
  pageMounts = 0;
  useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
});

test("a shareable id stays in the address bar and the page renders", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: REF });

  renderAt(`/tickets/${REF}/logs`);

  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${REF}/logs`);
  expect(api.ticket).toHaveBeenCalledWith(REF);
  expect(useTicketRefStore.getState().uuidByRef[REF]).toBe(UUID);
});

test("a shareable id already known is not asked for again", async () => {
  useTicketRefStore.getState().remember({ id: UUID, external_id: REF });

  renderAt(`/tickets/${REF}/diff`);

  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
  expect(api.ticket).not.toHaveBeenCalled();
});

test("a UUID address is swapped for the shareable id without reloading the page", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: REF });

  renderAt(`/tickets/${UUID}/diff?run=abc`);

  // The page does not wait on the lookup: it is keyed by the UUID already.
  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${REF}/diff?run=abc`);
  });
  expect(pageMounts).toBe(1);
});

test("a UUID address for a ticket with no shareable id stays put", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: "" });

  renderAt(`/tickets/${UUID}/diff`);

  await waitFor(() => expect(api.ticket).toHaveBeenCalledWith(UUID));
  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${UUID}/diff`);
});

test("a pre-restructure id moves to the current shareable id", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: REF });

  renderAt("/tickets/456-one-dispatch-decision-instead-of-three/diff");

  await waitFor(() => {
    expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${REF}/diff`);
  });
  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
  expect(api.ticket).toHaveBeenCalledWith("456-one-dispatch-decision-instead-of-three");
});

test("an id that resolves to nothing says so instead of bouncing home", async () => {
  api.ticket.mockRejectedValue(new Error("Ticket not found"));

  renderAt("/tickets/lor-nope-9999/diff");

  expect(await screen.findByText("No ticket with that id")).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent("/tickets/lor-nope-9999/diff");
  expect(screen.queryByTestId("ticket-page")).not.toBeInTheDocument();
});
