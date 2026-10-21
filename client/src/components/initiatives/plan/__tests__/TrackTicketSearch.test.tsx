import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type InitiativeMilestone } from "../../../../api/client";
import { ApiError } from "../../../../api/http";
import { createQueryClient } from "../../../../api/queryClient";
import { useToastStore } from "../../../../state/toastStore";
import { TrackTicketSearch } from "../TrackTicketSearch";

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

function renderPanel(onChanged = jest.fn()) {
  // The app's client, so a failed mutation toasts as it does in the app; no retries.
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <TrackTicketSearch initiativeId="init1" onChanged={onChanged} />
    </QueryClientProvider>,
  );
  return onChanged;
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
});

test("searching and adding a ticket tracks it and refreshes the plan", async () => {
  mockApi.initiativeMemberCandidates.mockResolvedValue([item({ member: false })]);
  mockApi.addInitiativeMember.mockResolvedValue({
    id: "init1",
    external_id: "init-ship-1",
    title: "Ship it",
    description: "",
    state: "in_progress",
    priority: 3,
    milestones: [item()],
    progress: { resolved: 0, total: 1 },
    workspaces: ["loregarden"],
  });
  const onChanged = renderPanel();

  await userEvent.type(await screen.findByLabelText("Track a ticket"), "780");
  const add = await screen.findByRole("button", { name: "Track lg-milestone-that-780 in this initiative" });
  await userEvent.click(add);

  await waitFor(() => expect(mockApi.addInitiativeMember).toHaveBeenCalledWith("init1", "f780"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(screen.getByLabelText("Track a ticket")).toHaveValue("");
});

test("a refused add is reported with the server's reason, and nothing changes", async () => {
  mockApi.initiativeMemberCandidates.mockResolvedValue([item({ member: false })]);
  mockApi.addInitiativeMember.mockRejectedValue(new ApiError(409, "init-ship-1 already covers it"));
  const onChanged = renderPanel();

  await userEvent.type(await screen.findByLabelText("Track a ticket"), "780");
  await userEvent.click(await screen.findByRole("button", { name: /^Track / }));

  await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
  expect(useToastStore.getState().toasts[0].message).toContain("already covers");
  expect(onChanged).not.toHaveBeenCalled();
});

test("a search with nothing to add says so", async () => {
  mockApi.initiativeMemberCandidates.mockResolvedValue([]);
  renderPanel();
  await userEvent.type(await screen.findByLabelText("Track a ticket"), "zz");
  expect(await screen.findByText(/Nothing matches “zz”/)).toBeInTheDocument();
});
