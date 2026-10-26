/**
 * The topbar's right end: the Inbox on every page.
 *
 * GitHub sync, Instances and Usage used to sit here, with a phone-width
 * overflow menu for them; they moved to Settings, each ticket's header and the
 * Machine page, and the header no longer offers them anywhere.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import { useNotificationStore } from "../../state/notificationStore";
import { AppTopbarActions } from "../AppTopbarActions";

jest.mock("../../api/client", () => ({
  api: {
    approvals: jest.fn(),
  },
}));

jest.mock("../ApprovalInboxPanel", () => ({ ApprovalInboxPanel: () => null }));
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
  useNotificationStore.getState().clear();
});

test("GitHub, Instances and Usage are no longer header actions", () => {
  renderActions();

  expect(screen.queryByRole("button", { name: /GitHub/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /instances/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /usage/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /More actions/ })).not.toBeInTheDocument();
});

describe("the inbox badge keeps one meaning per hue", () => {
  type Approvals = Awaited<ReturnType<typeof api.approvals>>;

  async function badgeFor(name: string) {
    renderActions();
    const button = await screen.findByRole("button", { name });
    return button.querySelector(".approvals-badge");
  }

  test("with nothing waiting it is a neutral zero, not a green light", async () => {
    const badge = await badgeFor("Open approvals and notifications inbox");
    expect(badge).toHaveTextContent("0");
    expect(badge).toHaveAttribute("data-tone", "neutral");
  });

  test("informational notifications alone stay neutral and do not pulse", async () => {
    useNotificationStore.getState().push({ tone: "success", title: "Merged" });
    useNotificationStore.getState().push({ tone: "info", title: "Queued" });
    const badge = await badgeFor("Open approvals and notifications inbox");
    expect(badge).toHaveTextContent("2");
    expect(badge).toHaveAttribute("data-tone", "neutral");
    expect(badge?.closest("button")).not.toHaveClass("approvals-btn-pending");
  });

  test("a pending approval is violet and named as waiting on you", async () => {
    mockedApi.approvals.mockResolvedValue([{ id: "a1" }] as unknown as Approvals);
    const badge = await badgeFor("Open approvals and notifications inbox — 1 waiting on you");
    expect(badge).toHaveAttribute("data-tone", "you");
  });

  test("red is kept for failure, even beside a pending approval", async () => {
    mockedApi.approvals.mockResolvedValue([{ id: "a1" }] as unknown as Approvals);
    useNotificationStore.getState().push({ tone: "error", title: "Run failed" });
    const badge = await badgeFor("Open approvals and notifications inbox — 1 failed — 1 waiting on you");
    expect(badge).toHaveTextContent("2");
    expect(badge).toHaveAttribute("data-tone", "bad");
  });
});
