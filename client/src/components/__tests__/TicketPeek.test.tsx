import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import * as apiClient from "../../api/client";
import { getRouterNavigate, setRouterNavigate } from "../../lib/routerBridge";
import { useTicketPeekStore } from "../../state/ticketPeekStore";
import { TicketLink } from "../TicketLink";
import { TicketPeekHost } from "../TicketPeekHost";

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const TICKET = {
  id: "uuid-1",
  external_id: "lg-peek-1",
  title: "Peek at me",
  description: "The details, in place",
  acceptance_criteria: [],
  checked_acceptance_criteria: [],
  tags: [],
  state: "in_progress",
  priority: 2,
} as unknown as apiClient.TicketDetail;

function Where() {
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    setRouterNavigate(navigate);
    return () => setRouterNavigate(null);
  }, [navigate]);
  return <output data-testid="where">{location.pathname}</output>;
}

function renderApp(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/initiatives/x"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <Where />
                {ui}
                <TicketPeekHost />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  act(() => useTicketPeekStore.getState().close());
  jest.mocked(apiClient.api.ticket).mockResolvedValue(TICKET);
});

it("opens a clicked ticket link's details in place, and leaves for the ticket only on Open ticket", async () => {
  renderApp(<TicketLink ticketId="uuid-1" tab="diff">Peek at me</TicketLink>);

  fireEvent.click(screen.getByRole("link", { name: "Peek at me" }));

  expect(await screen.findByText("The details, in place")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/initiatives/x");

  fireEvent.click(screen.getByRole("button", { name: "Open ticket" }));

  expect(screen.getByTestId("where")).toHaveTextContent("/tickets/lg-peek-1/diff");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("leaves a modified click to the browser", () => {
  renderApp(<TicketLink ticketId="uuid-1">Peek at me</TicketLink>);
  const link = screen.getByRole("link", { name: "Peek at me" });

  expect(link).toHaveAttribute("href", "/tickets/uuid-1/timeline");
  fireEvent.click(link, { metaKey: true });

  expect(useTicketPeekStore.getState().peek).toBeNull();
});

it("closes when the route changes underneath it", async () => {
  renderApp(null);
  act(() => useTicketPeekStore.getState().open({ ticketId: "uuid-1" }));
  await screen.findByRole("dialog");

  // A route change through the router, as a sidebar link would make.
  act(() => {
    void getRouterNavigate()?.("/home");
  });

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("closes on Escape", async () => {
  renderApp(null);
  act(() => useTicketPeekStore.getState().open({ ticketId: "uuid-1" }));
  await screen.findByRole("dialog");

  fireEvent.keyDown(document, { key: "Escape" });

  expect(useTicketPeekStore.getState().peek).toBeNull();
});
