import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

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
                <div data-testid="ticket-page">ticket page</div>
              </TicketRouteResolver>
            }
          />
        </Routes>
        <CurrentPath />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  api.ticket.mockReset();
});

test("a UUID route renders the page without resolving anything", async () => {
  renderAt(`/tickets/${UUID}/diff`);

  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
  expect(api.ticket).not.toHaveBeenCalled();
});

test("a shareable id is rewritten to the canonical UUID path", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: "lor-mcp-gateway-142" });

  renderAt("/tickets/lor-mcp-gateway-142/logs");

  await waitFor(() => {
    expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${UUID}/logs`);
  });
  expect(api.ticket).toHaveBeenCalledWith("lor-mcp-gateway-142");
  expect(await screen.findByTestId("ticket-page")).toBeInTheDocument();
});

test("the tab and query string survive the rewrite", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: "lor-mcp-gateway-142" });

  renderAt("/tickets/lor-mcp-gateway-142/diff?run=abc");

  await waitFor(() => {
    expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${UUID}/diff?run=abc`);
  });
});

test("a pre-restructure id resolves the same way", async () => {
  api.ticket.mockResolvedValue({ id: UUID, external_id: "lor-mcp-gateway-142" });

  renderAt("/tickets/456-one-dispatch-decision-instead-of-three/diff");

  await waitFor(() => {
    expect(screen.getByTestId("path")).toHaveTextContent(`/tickets/${UUID}/diff`);
  });
});

test("an id that resolves to nothing says so instead of bouncing home", async () => {
  api.ticket.mockRejectedValue(new Error("Ticket not found"));

  renderAt("/tickets/lor-nope-9999/diff");

  expect(await screen.findByText("No ticket with that id")).toBeInTheDocument();
  expect(screen.getByTestId("path")).toHaveTextContent("/tickets/lor-nope-9999/diff");
  expect(screen.queryByTestId("ticket-page")).not.toBeInTheDocument();
});
