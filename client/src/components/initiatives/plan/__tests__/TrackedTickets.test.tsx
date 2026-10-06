import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type InitiativeMilestone, type InitiativeView } from "../../../../api/client";
import { ApiError } from "../../../../api/http";
import { createQueryClient } from "../../../../api/queryClient";
import { useToastStore } from "../../../../state/toastStore";
import { TrackedTickets } from "../TrackedTickets";

jest.mock("../../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function item(overrides: Partial<InitiativeMilestone> = {}): InitiativeMilestone {
  return {
    id: "f780",
    external_id: "lg-milestone-that-780",
    title: "Gate outcomes",
    state: "in_progress",
    workspace_slug: "loregarden",
    work_item_type: "feature",
    member: true,
    home_milestone: "lg-milestone-that-491",
    ...overrides,
  };
}

function view(milestones: InitiativeMilestone[]): InitiativeView {
  return {
    id: "init1",
    external_id: "init-ship-1",
    title: "Ship it",
    description: "",
    state: "in_progress",
    priority: 3,
    milestones,
    progress: { resolved: 0, total: milestones.length },
    workspaces: ["loregarden"],
  };
}

const OWNED = item({ id: "m1", external_id: "lg-m-1", work_item_type: "milestone", member: false, home_milestone: "" });

function renderPanel(onChanged = jest.fn()) {
  // The app's client, so a failed mutation toasts as it does in the app; no retries.
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <TrackedTickets initiativeId="init1" onChanged={onChanged} />
    </QueryClientProvider>,
  );
  return onChanged;
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
});

test("lists only members, each with the milestone and workspace it stays in", async () => {
  mockApi.initiative.mockResolvedValue(view([OWNED, item()]));
  renderPanel();

  const list = await screen.findByRole("list", { name: "Tracked tickets" });
  const rows = within(list).getAllByRole("listitem");
  expect(rows).toHaveLength(1);
  expect(rows[0]).toHaveTextContent("lg-milestone-that-780");
  expect(rows[0]).toHaveTextContent("feature · in lg-milestone-that-491 · loregarden");
  expect(screen.getByRole("heading", { name: "Tracked tickets (1)" })).toBeInTheDocument();
});

test("with no members it says what one is and how to add it", async () => {
  mockApi.initiative.mockResolvedValue(view([OWNED]));
  renderPanel();
  expect(await screen.findByText(/None yet\. Add a ticket below/)).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Tracked tickets" })).not.toBeInTheDocument();
});

test("a failed load says so in place, with a retry", async () => {
  mockApi.initiative.mockRejectedValueOnce(new ApiError(500, "boom"));
  renderPanel();
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");

  mockApi.initiative.mockResolvedValue(view([item()]));
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("list", { name: "Tracked tickets" })).toBeInTheDocument();
});

test("searching and adding a ticket tracks it and refreshes the plan", async () => {
  mockApi.initiative.mockResolvedValue(view([]));
  mockApi.initiativeMemberCandidates.mockResolvedValue([item({ member: false })]);
  mockApi.addInitiativeMember.mockResolvedValue(view([item()]));
  const onChanged = renderPanel();

  await userEvent.type(await screen.findByLabelText("Add a ticket"), "780");
  const add = await screen.findByRole("button", { name: "Track lg-milestone-that-780 in this initiative" });
  await userEvent.click(add);

  await waitFor(() => expect(mockApi.addInitiativeMember).toHaveBeenCalledWith("init1", "f780"));
  expect(await screen.findByRole("list", { name: "Tracked tickets" })).toHaveTextContent("lg-milestone-that-780");
  expect(onChanged).toHaveBeenCalled();
  expect(screen.getByLabelText("Add a ticket")).toHaveValue("");
});

test("a refused add is reported with the server's reason, and nothing changes", async () => {
  mockApi.initiative.mockResolvedValue(view([]));
  mockApi.initiativeMemberCandidates.mockResolvedValue([item({ member: false })]);
  mockApi.addInitiativeMember.mockRejectedValue(new ApiError(409, "init-ship-1 already covers it"));
  const onChanged = renderPanel();

  await userEvent.type(await screen.findByLabelText("Add a ticket"), "780");
  await userEvent.click(await screen.findByRole("button", { name: /^Track / }));

  await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
  expect(useToastStore.getState().toasts[0].message).toContain("already covers");
  expect(onChanged).not.toHaveBeenCalled();
});

test("removing a member asks the server and refreshes", async () => {
  mockApi.initiative.mockResolvedValue(view([item()]));
  mockApi.removeInitiativeMember.mockResolvedValue(undefined);
  const onChanged = renderPanel();

  await userEvent.click(await screen.findByRole("button", { name: "Stop tracking lg-milestone-that-780 in this initiative" }));
  await waitFor(() => expect(mockApi.removeInitiativeMember).toHaveBeenCalledWith("init1", "f780"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

test("a search with nothing to add says so", async () => {
  mockApi.initiative.mockResolvedValue(view([]));
  mockApi.initiativeMemberCandidates.mockResolvedValue([]);
  renderPanel();
  await userEvent.type(await screen.findByLabelText("Add a ticket"), "zz");
  expect(await screen.findByText(/Nothing matches “zz”/)).toBeInTheDocument();
});
