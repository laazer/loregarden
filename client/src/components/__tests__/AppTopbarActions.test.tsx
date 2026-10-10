/**
 * The topbar's global actions at phone width.
 *
 * Which row shows is decided by CSS (`@media (max-width: 640px)` in index.css),
 * which jsdom does not apply — so both render here. What these tests pin is the
 * part CSS cannot: the overflow menu reaches the same three dialogs the inline
 * buttons do, and a near-limit Usage warning is still announced once Usage is
 * folded into it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import { AppTopbarActions } from "../AppTopbarActions";

jest.mock("../../api/client", () => ({
  api: {
    usage: jest.fn(),
    approvals: jest.fn(),
  },
}));

jest.mock("../ApprovalInboxPanel", () => ({ ApprovalInboxPanel: () => null }));
jest.mock("../GithubSyncModal", () => ({
  GithubSyncModal: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="GitHub sync" /> : null),
}));
jest.mock("../LocalInstancesModal", () => ({
  LocalInstancesModal: ({ open }: { open: boolean }) =>
    open ? <div role="dialog" aria-label="Local instances" /> : null,
}));
jest.mock("../UsageModal", () => ({
  UsageModal: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="Usage" /> : null),
}));

const mockedApi = api as jest.Mocked<typeof api>;

function renderActions() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/chat"]}>
        <AppTopbarActions />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedApi.approvals.mockResolvedValue([]);
  mockedApi.usage.mockResolvedValue({ near_limit: false } as Awaited<ReturnType<typeof api.usage>>);
});

test.each([
  ["Sync with GitHub", "GitHub sync"],
  ["Local instances", "Local instances"],
  ["Usage", "Usage"],
])("the overflow menu's %s item opens the same dialog as its inline button", async (item, dialog) => {
  const user = userEvent.setup();
  renderActions();

  await user.click(screen.getByRole("button", { name: "More actions" }));
  await user.click(screen.getByRole("menuitem", { name: item }));

  expect(screen.getByRole("dialog", { name: dialog })).toBeInTheDocument();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

test("a near-limit Usage warning is announced on the overflow trigger and its item", async () => {
  mockedApi.usage.mockResolvedValue({ near_limit: true } as Awaited<ReturnType<typeof api.usage>>);
  const user = userEvent.setup();
  renderActions();

  const trigger = await screen.findByRole("button", {
    name: "More actions — usage limits are getting close",
  });
  await waitFor(() => expect(trigger.parentElement?.parentElement).toHaveClass("topbar-overflow--warning"));

  await user.click(trigger);
  expect(screen.getByRole("menuitem", { name: "Usage — limits getting close" })).toBeInTheDocument();
});
