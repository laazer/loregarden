import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import type { TicketSummary } from "../../api/types";
import { fetchBranchTriage } from "../../lib/branchTriageApi";
import { matchPages, matchText, searchNotes } from "../../lib/topbarSearch";
import { fetchViews } from "../../lib/viewsApi";
import { SidebarWorkspaceProvider } from "../../state/SidebarWorkspaceContext";
import { useUiStore } from "../../state/uiStore";
import { TopbarSearch } from "../TopbarSearch";

jest.mock("../../api/client", () => ({
  api: {
    tickets: jest.fn(),
    initiatives: jest.fn(),
    baxterChatSessions: jest.fn(),
    workspaces: jest.fn(),
  },
}));
jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchViews: jest.fn(),
}));
jest.mock("../../lib/branchTriageApi", () => ({
  ...jest.requireActual("../../lib/branchTriageApi"),
  fetchBranchTriage: jest.fn(),
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockBranches = fetchBranchTriage as jest.MockedFunction<typeof fetchBranchTriage>;

const TICKET_ID = "11111111-1111-4111-8111-111111111111";

function ticket(overrides: Partial<TicketSummary> = {}): TicketSummary {
  return {
    id: TICKET_ID,
    external_id: "lg-auth-7",
    title: "Auth token refresh",
    state: "in_progress",
    priority: 2,
    workspace_slug: "loregarden",
    workflow_stage_key: "",
    workflow_stage_status: "pending",
    workflow_stage_name: "",
    run_code: "",
    ...overrides,
  } as TicketSummary;
}

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderSearch() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SidebarWorkspaceProvider slug="loregarden">
        <MemoryRouter initialEntries={["/"]}>
          <TopbarSearch />
          <Routes>
            <Route path="*" element={<Where />} />
          </Routes>
        </MemoryRouter>
      </SidebarWorkspaceProvider>
    </QueryClientProvider>,
  );
  return screen.getByRole("combobox", { name: "Search pages, tickets and more" });
}

const group = (name: string) => screen.findByRole("group", { name });

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ chatScope: "loregarden", baxterChatSessionId: "", branchTriageBranch: "" });
  mockApi.tickets.mockResolvedValue([]);
  mockApi.initiatives.mockResolvedValue([]);
  mockApi.baxterChatSessions.mockResolvedValue([]);
  mockApi.workspaces.mockResolvedValue([]);
  mockViews.mockResolvedValue([]);
  mockBranches.mockResolvedValue({ branches: [] } as unknown as Awaited<
    ReturnType<typeof fetchBranchTriage>
  >);
});

describe("matching", () => {
  test("pages match by name, in rail order", () => {
    expect(matchPages("ma").map((page) => page.label)).toEqual(["Machine"]);
    expect(matchPages("  WORK ").map((page) => page.label)).toEqual(["Work items", "Workspaces"]);
    expect(matchPages("")).toEqual([]);
  });

  test("any field can match, and the limit stops the scan", () => {
    const items = [
      { a: "alpha", b: "x" },
      { a: "beta", b: "ALPHA" },
      { a: "alphabet", b: "" },
    ];
    expect(matchText(items, "alpha", (item) => [item.a, item.b])).toHaveLength(3);
    expect(matchText(items, "alpha", (item) => [item.a, item.b], 2)).toEqual(items.slice(0, 2));
  });

  test("a failed source is named with its reason; a pending one says it is still searching", () => {
    expect(
      searchNotes([
        { group: "Tickets", pending: true, error: null },
        { group: "Branches", pending: false, error: "git timed out" },
        { group: "Chats", pending: false, error: null },
      ]),
    ).toEqual({
      pending: "Searching tickets…",
      errors: ["Couldn't search branches: git timed out"],
    });
  });
});

test("a page shows on the first keystroke, and Enter goes there", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "q");
  const pages = await group("Pages");
  expect(within(pages).getByRole("option", { name: /Queue/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  // One letter is not enough for the server search or the git scan.
  expect(mockApi.tickets).not.toHaveBeenCalled();
  expect(mockBranches).not.toHaveBeenCalled();

  await user.keyboard("{Enter}");
  expect(screen.getByTestId("where")).toHaveTextContent("/queue");
  expect(box).toHaveValue("");
});

test("tickets match by id or title, and ↓ then Enter opens one", async () => {
  mockApi.tickets.mockResolvedValue([ticket()]);
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "auth");
  const option = within(await group("Tickets")).getByRole("option", { name: /lg-auth-7/ });
  expect(mockApi.tickets).toHaveBeenLastCalledWith({ search: "auth", limit: 8 });

  await user.keyboard("{ArrowDown}");
  expect(option).toHaveAttribute("aria-selected", "true");
  expect(box).toHaveAttribute("aria-activedescendant", option.id);
  await user.keyboard("{Enter}");
  expect(screen.getByTestId("where")).toHaveTextContent(`/tickets/${TICKET_ID}`);
});

test("a tab from the sidebar opens its view", async () => {
  mockViews.mockResolvedValue([
    { id: "v-1", title: "Release board" } as Awaited<ReturnType<typeof fetchViews>>[number],
  ]);
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "release");
  await user.click(within(await group("Tabs")).getByRole("option", { name: /Release board/ }));
  expect(mockViews).toHaveBeenCalledWith("loregarden");
  expect(screen.getByTestId("where")).toHaveTextContent("/view/v-1");
  expect(box).not.toHaveFocus();
});

test("an initiative matches by title or id and opens its plan", async () => {
  mockApi.initiatives.mockResolvedValue([
    {
      id: "i-1",
      external_id: "init-tinkercg-3",
      title: "TinkerCG launch",
      state: "in_progress",
    } as Awaited<ReturnType<typeof api.initiatives>>[number],
  ]);
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "tinker");
  await user.click(
    within(await group("Initiatives")).getByRole("option", { name: /TinkerCG launch/ }),
  );
  expect(screen.getByTestId("where")).toHaveTextContent("/initiatives/i-1");
});

test("a chat thread in the current scope opens in Chat", async () => {
  mockApi.baxterChatSessions.mockResolvedValue([
    {
      id: "c-1",
      title: "Why did the gate fail",
      message_count: 4,
      preview: "",
      created_at: "",
      updated_at: "",
    },
  ]);
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "gate");
  await user.click(
    within(await group("Chats")).getByRole("option", { name: /Why did the gate fail/ }),
  );
  expect(mockApi.baxterChatSessions).toHaveBeenCalledWith("loregarden");
  expect(useUiStore.getState().baxterChatSessionId).toBe("c-1");
  expect(screen.getByTestId("where")).toHaveTextContent("/chat");
});

test("a branch opens in Branch Triage, selected, in the sidebar's workspace", async () => {
  mockBranches.mockResolvedValue({
    branches: [{ name: "ux/s2-top-bar", is_current: false }],
  } as unknown as Awaited<ReturnType<typeof fetchBranchTriage>>);
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "s2-top");
  await user.click(within(await group("Branches")).getByRole("option", { name: /ux\/s2-top-bar/ }));
  expect(mockBranches).toHaveBeenCalledWith("loregarden");
  expect(useUiStore.getState().branchTriageBranch).toBe("ux/s2-top-bar");
  expect(useUiStore.getState().branchTriageWorkspaceSlug).toBe("loregarden");
  expect(screen.getByTestId("where")).toHaveTextContent("/branch-triage");
});

test("a failed source says so, beside the results that did come back", async () => {
  mockApi.tickets.mockRejectedValue(new ApiError(503, "search unavailable"));
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "wor");
  expect(await screen.findByText(/Couldn't search tickets/)).toBeInTheDocument();
  expect(
    within(await group("Pages")).getByRole("option", { name: /Work items/ }),
  ).toBeInTheDocument();
  expect(box).toHaveAttribute("aria-expanded", "true");
});

test("nothing matching says what search looks at", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "zzqx");
  expect(await screen.findByText(/Nothing matches “zzqx”/)).toBeInTheDocument();
});

test("Escape clears the text, then leaves the field", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.type(box, "home");
  await user.keyboard("{Escape}");
  expect(box).toHaveValue("");
  expect(box).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(box).not.toHaveFocus();
});

test("⌘K or Ctrl+K focuses the box from anywhere on the page", () => {
  const box = renderSearch();

  act(() => {
    fireEvent.keyDown(window, { key: "k", metaKey: true });
  });
  expect(box).toHaveFocus();

  box.blur();
  act(() => {
    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
  });
  expect(box).toHaveFocus();
});

test("⌘K is left to the code editor while it has focus", () => {
  const box = renderSearch();
  const editor = document.createElement("div");
  editor.className = "monaco-editor";
  const field = document.createElement("textarea");
  editor.appendChild(field);
  document.body.appendChild(editor);
  field.focus();

  fireEvent.keyDown(field, { key: "k", metaKey: true });
  expect(field).toHaveFocus();
  expect(box).not.toHaveFocus();
  editor.remove();
});

test("nothing is fetched until the box has text in it", async () => {
  const user = userEvent.setup();
  const box = renderSearch();

  await user.click(box);
  expect(mockApi.initiatives).not.toHaveBeenCalled();
  expect(mockApi.baxterChatSessions).not.toHaveBeenCalled();
  expect(mockViews).not.toHaveBeenCalled();
});
