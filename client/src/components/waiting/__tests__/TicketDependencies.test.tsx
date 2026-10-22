import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTicketPeekStore } from "../../../state/ticketPeekStore";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { TicketDetail } from "../../../api/client";
import type { TicketDependencyRef, TicketSummary } from "../../../api/types";
import { TicketDependencies } from "../../TicketDependencies";

const addDependency = jest.fn();
const removeDependency = jest.fn();
const tickets = jest.fn();

jest.mock("../../../api/client", () => ({
  api: {
    addDependency: (...args: unknown[]) => addDependency(...args),
    removeDependency: (...args: unknown[]) => removeDependency(...args),
    tickets: (...args: unknown[]) => tickets(...args),
  },
}));
function dep(id: string, overrides: Partial<TicketDependencyRef> = {}): TicketDependencyRef {
  return {
    id,
    external_id: `lg-x-${id}`,
    title: `Ticket ${id}`,
    state: "in_progress",
    work_item_type: "task",
    is_integration_review: false,
    workspace_slug: "loregarden",
    unmet_reason: null,
    landing_target: "",
    overridden: false,
    ...overrides,
  };
}

function renderCard(dependencies: TicketDependencyRef[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const ticket = { id: "t1", workspace_slug: "loregarden", dependencies, dependents: [] } as unknown as TicketDetail;
  return render(
    <QueryClientProvider client={client}>
      <TicketDependencies ticket={ticket} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  addDependency.mockResolvedValue({});
  removeDependency.mockResolvedValue({});
  tickets.mockResolvedValue([]);
});

it("says of each prerequisite whether it still holds the ticket, and why", () => {
  renderCard([
    dep("1", { unmet_reason: "not_done" }),
    dep("2", { state: "done", unmet_reason: "not_landed", landing_target: "integration/lg-x-491" }),
    dep("3", { state: "done" }),
    dep("4", { unmet_reason: "not_done", overridden: true }),
    dep("5", { unmet_reason: "not_done", workspace_slug: "blobert" }),
  ]);

  expect(screen.getByText(/Dependencies/)).toHaveTextContent("3 holding it");
  expect(screen.getByText("done, not landed on integration/lg-x-491")).toBeInTheDocument();
  expect(screen.getByText("met")).toBeInTheDocument();
  expect(screen.getByText("started past it")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /blobert: lg-x-5/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /lg-x-1 Ticket 1/ }));
  expect(useTicketPeekStore.getState().peek).toEqual({ ticketId: "1", tab: undefined });
});

it("asks before it stops waiting for a ticket", async () => {
  renderCard([dep("1", { unmet_reason: "not_done" })]);

  fireEvent.click(screen.getByRole("button", { name: "Stop waiting for lg-x-1" }));
  expect(removeDependency).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Keep" }));
  expect(screen.getByRole("button", { name: "Stop waiting for lg-x-1" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Stop waiting for lg-x-1" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(removeDependency).toHaveBeenCalledWith("t1", "1"));
});

it("finds the ticket to wait for by title, and picks it from the keyboard", async () => {
  tickets.mockResolvedValue([
    { id: "t1", external_id: "lg-x-self", title: "This ticket", state: "backlog" },
    { id: "p1", external_id: "lg-x-1314", title: "Land the leads", state: "in_progress" },
  ] as TicketSummary[]);
  renderCard([]);

  const box = screen.getByRole("combobox", { name: "Wait for ticket" });
  fireEvent.change(box, { target: { value: "leads" } });
  const list = await screen.findByRole("listbox", { name: "Matching tickets" });
  // The ticket itself is never offered as its own prerequisite.
  expect(await within(list).findAllByRole("option")).toHaveLength(1);
  expect(box).toHaveAttribute("aria-expanded", "true");

  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(addDependency).toHaveBeenCalledWith("t1", "p1"));
});

it("takes a pasted id as written when the search finds nothing", async () => {
  renderCard([]);

  const box = screen.getByRole("combobox", { name: "Wait for ticket" });
  fireEvent.change(box, { target: { value: "lg-x-77" } });
  await screen.findByText(/No ticket matches/);
  fireEvent.keyDown(box, { key: "Enter" });

  await waitFor(() => expect(addDependency).toHaveBeenCalledWith("t1", "lg-x-77"));
});

it("says why an add failed, in the server's words", async () => {
  addDependency.mockRejectedValue(new Error("lg-x-1 already depends on t1; edge would cycle"));
  renderCard([]);

  const box = screen.getByRole("combobox", { name: "Wait for ticket" });
  fireEvent.change(box, { target: { value: "lg-x-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("edge would cycle");
});
