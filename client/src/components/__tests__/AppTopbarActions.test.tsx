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

describe("the inbox splits its count into one chip per kind", () => {
  type Approvals = Awaited<ReturnType<typeof api.approvals>>;

  async function chipsFor(name: string) {
    renderActions();
    const button = await screen.findByRole("button", { name });
    return [...button.querySelectorAll(".approvals-badge")].map((chip) => ({
      tone: chip.getAttribute("data-tone"),
      text: chip.textContent,
    }));
  }

  test("with nothing waiting it shows no chip at all, not a green light", async () => {
    expect(await chipsFor("Open approvals and notifications inbox")).toEqual([]);
  });

  test("informational notifications alone are one neutral chip and do not pulse", async () => {
    useNotificationStore.getState().push({ tone: "success", title: "Merged" });
    useNotificationStore.getState().push({ tone: "info", title: "Queued" });
    expect(await chipsFor("Open approvals and notifications inbox — 2 other notifications")).toEqual([
      { tone: "neutral", text: "2" },
    ]);
    expect(screen.getByRole("button", { name: /inbox/ })).not.toHaveClass("approvals-btn-pending");
  });

  test("failed, waiting on you and the rest each get their own chip, in that order", async () => {
    mockedApi.approvals.mockResolvedValue([{ id: "a1" }] as unknown as Approvals);
    useNotificationStore.getState().push({ tone: "error", title: "Run failed" });
    useNotificationStore.getState().push({ tone: "success", title: "Run complete" });
    useNotificationStore.getState().push({ tone: "success", title: "Run complete" });

    expect(
      await chipsFor(
        "Open approvals and notifications inbox — 1 failed — 1 waiting on you — 2 other notifications",
      ),
    ).toEqual([
      { tone: "bad", text: "■1" },
      { tone: "you", text: "◆1" },
      { tone: "neutral", text: "2" },
    ]);
  });
});
