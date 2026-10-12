import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type AutopilotTicketRef, type SubtreeAutopilot } from "../../api/client";
import { ApiError } from "../../api/http";
import { createQueryClient } from "../../api/queryClient";
import { useToastStore } from "../../state/toastStore";
import { SubtreeAutopilotCard } from "../SubtreeAutopilotCard";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function ref(n: number): AutopilotTicketRef {
  return { id: `t${n}`, external_id: `lg-f-${n}`, title: `Feature ${n}` };
}

function view(overrides: Partial<SubtreeAutopilot> = {}, autopilot: Partial<SubtreeAutopilot["autopilot"]> = {}): SubtreeAutopilot {
  return {
    root_id: "m1",
    autopilot: {
      enabled: false,
      max_parallel: 3,
      paused_reason: "",
      in_flight: 0,
      next_up: [],
      available: true,
      recent: [],
      ...autopilot,
    },
    total_items: 4,
    open_items: 4,
    next_up: [],
    waiting_on_person: [],
    blocked: [],
    running_above: null,
    also_driven_by: [],
    ...overrides,
  };
}

function renderCard(onOpenTicket = jest.fn()) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <SubtreeAutopilotCard ticketId="m1" workItemType="milestone" onOpenTicket={onOpenTicket} />
    </QueryClientProvider>,
  );
  return onOpenTicket;
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
});

test("turning it on sends the switch and shows what it starts next", async () => {
  mockApi.ticketAutopilot.mockResolvedValue(view());
  mockApi.setTicketAutopilot.mockResolvedValue(
    view({ next_up: [ref(1), ref(2)] }, { enabled: true, in_flight: 2 }),
  );
  const onOpen = renderCard();

  const toggle = await screen.findByRole("button", { name: "Turn on" });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(toggle);

  expect(mockApi.setTicketAutopilot).toHaveBeenCalledWith("m1", { enabled: true });
  expect(await screen.findByRole("button", { name: "Turn off" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: /lg-f-2/ }));
  expect(onOpen).toHaveBeenCalledWith("t2");
});

test("the parallel cap applies only on Update", async () => {
  mockApi.ticketAutopilot.mockResolvedValue(view());
  mockApi.setTicketAutopilot.mockResolvedValue(view({}, { max_parallel: 6 }));
  renderCard();

  const select = await screen.findByRole("combobox", { name: "Most items the autopilot runs at once" });
  expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
  await userEvent.selectOptions(select, "6");
  expect(mockApi.setTicketAutopilot).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: "Update" }));
  expect(mockApi.setTicketAutopilot).toHaveBeenCalledWith("m1", { max_parallel: 6 });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Update" })).toBeNull());
});

test("a long waiting list is cut short and points at the child tickets", async () => {
  const waiting = Array.from({ length: 8 }, (_, i) => ref(i));
  mockApi.ticketAutopilot.mockResolvedValue(view({ waiting_on_person: waiting }));
  renderCard();

  expect(await screen.findByRole("heading", { name: "Waiting on a person (8)" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /^lg-f-/ })).toHaveLength(5);
  expect(screen.getByText(/\+3 more/)).toBeInTheDocument();
});

test("names the run above it and the autopilot above it, each a link", async () => {
  mockApi.ticketAutopilot.mockResolvedValue(
    view({
      running_above: { id: "m1", external_id: "lg-m-1", title: "M" },
      also_driven_by: [{ id: "i1", external_id: "init-ship-1", title: "Ship" }],
    }),
  );
  const onOpen = renderCard();

  await userEvent.click(await screen.findByRole("button", { name: "lg-m-1" }));
  await userEvent.click(screen.getByRole("button", { name: "init-ship-1" }));
  expect(onOpen.mock.calls).toEqual([["m1"], ["i1"]]);
});

test("a refused switch toasts the server's reason", async () => {
  mockApi.ticketAutopilot.mockResolvedValue(view({ total_items: 0, open_items: 0 }));
  mockApi.setTicketAutopilot.mockRejectedValue(
    new ApiError(409, "lg-m-1 has no sub-items for the autopilot to run; add some first"),
  );
  renderCard();

  await userEvent.click(await screen.findByRole("button", { name: "Turn on" }));

  await waitFor(() =>
    expect(useToastStore.getState().toasts.map((t) => `${t.title} ${t.message}`).join()).toMatch(/no sub-items/),
  );
});

test("a failed load says so and retries", async () => {
  mockApi.ticketAutopilot.mockRejectedValueOnce(new ApiError(500, "database is locked"));
  mockApi.ticketAutopilot.mockResolvedValueOnce(view());
  renderCard();

  expect(await screen.findByRole("alert")).toHaveTextContent("database is locked");
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: "Turn on" })).toBeInTheDocument();
});

test("on a sandbox server the switch is disabled, and the card says why", async () => {
  mockApi.ticketAutopilot.mockResolvedValue(view({}, { available: false }));
  renderCard();

  expect(await screen.findByRole("button", { name: "Turn on" })).toBeDisabled();
  expect(screen.getByText(/sandbox server/)).toBeInTheDocument();
});
