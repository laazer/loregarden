import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../../api/client";
import { BtwPrimitive } from "../BtwPrimitive";
import { CalendarEventPrimitive, CalendarPrimitive } from "../CalendarPrimitive";

jest.mock("../../../../api/client", () => {
  const actual = jest.requireActual("../../../../api/client");
  return { ...actual, api: { ...actual.api, ticketAsides: jest.fn(), deleteAside: jest.fn(), escalateAside: jest.fn() } };
});

const mockedApi = api as jest.Mocked<typeof api>;

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("calendar", () => {
  it("switches Month, Week and Day from one toggle in the card's head", () => {
    const { container } = wrap(
      <CalendarPrimitive
        part={{
          primitive: "calendar",
          view: "day",
          focus_date: "2026-10-25T09:00:00Z",
          events: [{ title: "Bootstrap", starts_at: "2026-10-25T09:00:00Z", kind: "run" }],
        }}
      />,
    );
    const head = container.querySelector(".lg-primitive-card-header") as HTMLElement;
    const toggle = within(head).getByRole("group", { name: "Calendar view" });
    expect(within(toggle).getByRole("button", { name: "Day" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".lg-primitive-card-actions")).toBeNull();

    fireEvent.click(within(toggle).getByRole("button", { name: "Week" }));
    expect(within(toggle).getByRole("button", { name: "Week" })).toHaveAttribute("aria-pressed", "true");
    expect(head).toHaveTextContent("1 event");
  });

  it("draws one event as a live ref naming the ticket it books", () => {
    wrap(
      <CalendarEventPrimitive
        part={{
          primitive: "calendar_event",
          event: { title: "Bootstrap", starts_at: "2026-10-25T15:05:00Z", kind: "plan", ticket_id: "2e65ca4d-1111" },
        }}
      />,
    );
    expect(screen.getByText(/^plan · .* · 2e65ca4d$/)).toBeInTheDocument();
  });
});

describe("aside", () => {
  it("quotes the question, offers one action at the foot, and keeps Delete in ⋯", async () => {
    mockedApi.ticketAsides.mockResolvedValue({
      exchanges: [{ id: "x1", status: "answered", answer: "It checks the branch.", escalated: false, escalation_refusal: "" }],
    } as never);
    const { container } = wrap(
      <BtwPrimitive
        part={{
          primitive: "btw",
          exchange_id: "x1",
          ticket_id: "t1",
          question: "why shell out to git?",
          observed_agent_id: "backend_implementer",
          observed_stage_key: "implementation",
        }}
      />,
    );

    expect(screen.getByText("from backend_implementer · implementation")).toBeInTheDocument();
    expect(container.querySelector(".lg-btw-question")).toHaveTextContent("why shell out to git?");
    const footer = container.querySelector(".lg-primitive-card-actions") as HTMLElement;
    expect(await within(footer).findByRole("button", { name: "Ask the running agent" })).toBeInTheDocument();
    expect(within(footer).queryByRole("button", { name: /Delete/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "More for Aside" }));
    expect(screen.getByRole("menuitem", { name: "Delete this aside" })).toBeInTheDocument();
  });
});
