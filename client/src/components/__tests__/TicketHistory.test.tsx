import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { TicketHistoryEvent } from "../../api/types";
import { TicketHistory } from "../TicketHistory";

const ticketHistory = jest.fn();

jest.mock("../../api/client", () => ({
  api: { ticketHistory: (id: string) => ticketHistory(id) },
}));

function event(overrides: Partial<TicketHistoryEvent> = {}): TicketHistoryEvent {
  return {
    id: "e1",
    type: "StageSkipped",
    ticket_id: "t1",
    workspace_id: "w1",
    payload: { stage_key: "ui-design" },
    created_at: "2026-09-04T10:00:00Z",
    ...overrides,
  };
}

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TicketHistory ticketId="t1" />
    </QueryClientProvider>,
  );
}

describe("TicketHistory", () => {
  beforeEach(() => ticketHistory.mockReset());

  it("is collapsed until asked, and says how much there is", async () => {
    ticketHistory.mockResolvedValue([event(), event({ id: "e2" })]);
    renderHistory();

    expect(await screen.findByRole("button", { name: /History \(2\)/ })).toBeInTheDocument();
    expect(screen.queryByText(/ui-design/)).not.toBeInTheDocument();
  });

  it("shows the transitions when opened", async () => {
    ticketHistory.mockResolvedValue([event()]);
    renderHistory();

    fireEvent.click(await screen.findByRole("button", { name: /History/ }));
    expect(screen.getByText(/Stage skipped ui-design/)).toBeInTheDocument();
  });

  it("renders nothing for a ticket with no recorded transitions", async () => {
    // Tickets predating the log having a reader are the common case, and an
    // empty disclosure button on every one of them is noise.
    ticketHistory.mockResolvedValue([]);
    const { container } = renderHistory();

    await waitFor(() => expect(ticketHistory).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
