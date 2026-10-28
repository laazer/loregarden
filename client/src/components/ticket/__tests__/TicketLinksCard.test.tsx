import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { TicketDetail } from "../../../api/client";
import type { TicketDependencyRef } from "../../../api/types";
import { TicketLinksCard } from "../TicketLinksCard";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function ref(id: string, overrides: Partial<TicketDependencyRef> = {}): TicketDependencyRef {
  return {
    id,
    external_id: `lg-${id}`,
    title: `Ticket ${id}`,
    state: "in_progress",
    work_item_type: "task",
    is_integration_review: false,
    workspace_slug: "loregarden",
    ...overrides,
  };
}

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t1",
    external_id: "lg-t1",
    workspace_slug: "loregarden",
    parent_ticket_id: null,
    dependencies: [],
    dependents: [],
    related: [],
    ...overrides,
  } as unknown as TicketDetail;
}

function renderCard(detail: TicketDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TicketLinksCard ticket={detail} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.clearAllMocks());

it("lists the parent, what still holds the ticket, what it holds up, and what is related — each opening its page", async () => {
  mockApi.ticket.mockResolvedValue({ id: "p", external_id: "lg-parent", title: "The feature", state: "in_progress" } as TicketDetail);
  renderCard(
    ticket({
      parent_ticket_id: "p",
      dependencies: [ref("met"), ref("held", { unmet_reason: "not_done", state: "blocked" })],
      dependents: [ref("after")],
      related: [ref("ctx", { state: "done", workspace_slug: "blobert" })],
    } as Partial<TicketDetail>),
  );

  const card = screen.getByRole("region", { name: "Linked tickets" });
  expect(card).toHaveTextContent("1 holding it");
  await within(card).findByText("The feature");
  const links = within(card).getAllByRole("link");
  expect(links.map((link) => link.textContent)).toEqual([
    expect.stringMatching(/^ParentThe featurelg-parent/),
    expect.stringMatching(/^Waiting onTicket heldlg-held/),
    expect.stringMatching(/^Depends onTicket metlg-met/),
    expect.stringMatching(/^BlocksTicket afterlg-after/),
    expect.stringMatching(/^RelatedTicket ctxblobert: lg-ctx/),
  ]);
  expect(links[1]).toHaveAttribute("href", "/tickets/held/timeline");
  expect(links[1]).toHaveAttribute("title", expect.stringContaining("waiting · blocked"));
});

it("says what a link would be for when there are none, and opens the editors in a dialog", () => {
  renderCard(ticket());

  const card = screen.getByRole("region", { name: "Linked tickets" });
  expect(card).toHaveTextContent(/Not linked to any ticket/);
  expect(within(card).queryByRole("list")).toBeNull();

  fireEvent.click(within(card).getByRole("button", { name: "+ Link" }));
  const dialog = screen.getByRole("dialog", { name: "Link tickets" });
  expect(dialog).toHaveTextContent("Dependencies");
  expect(dialog).toHaveTextContent("Related");
});

it("holds the rest of a long list behind Show all", () => {
  renderCard(ticket({ related: Array.from({ length: 11 }, (_, i) => ref(`r${i}`)) } as Partial<TicketDetail>));

  const card = screen.getByRole("region", { name: "Linked tickets" });
  expect(within(card).getAllByRole("link")).toHaveLength(8);
  const more = within(card).getByRole("button", { name: "Show all 11" });
  expect(more).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(more);
  expect(within(card).getAllByRole("link")).toHaveLength(11);
});
