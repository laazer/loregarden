/**
 * The default state of a fresh install: `uiStore.workspace` is the persisted
 * `"all"`, nothing has ever opened the Dashboard's picker, and the workspace
 * list has loaded. That is the state most users are in on first launch, and it
 * must still produce navigation — the rail this sidebar replaces always drew
 * seven links.
 *
 * This renders the *real* `AppSidebar` through `AppLayout`, unlike
 * `AppLayoutChrome.test.tsx`, because the thing under test is the seeding
 * decision the layout hands down and the sidebar acts on.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AppLayout } from "../AppLayout";
import { api } from "../../api/client";
import { useUiStore } from "../../state/uiStore";
import { fetchSidebarEntries, fetchViews, pinPage, type SidebarEntry } from "../../lib/viewsApi";

jest.mock("../CopilotDock", () => ({
  CopilotDock: () => <div data-testid="copilot-dock" />,
}));

jest.mock("../AppTopbarActions", () => ({
  AppTopbarActions: () => <div data-testid="topbar-actions" />,
}));

jest.mock("../SettingsModal", () => ({
  SettingsModal: () => null,
}));

jest.mock("../QueueNotificationsHost", () => ({
  QueueNotificationsHost: () => null,
}));

jest.mock("../../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    workspaces: jest.fn(async () => []),
    runtimeOptions: jest.fn(async () => ({})),
    approvals: jest.fn(async () => []),
    usage: jest.fn(async () => null),
    ticket: jest.fn(async () => null),
  },
}));

jest.mock("../../lib/viewsApi", () => ({
  // The module also exports the query-key factory the sidebar hook reads its
  // cache keys from; mocking it away leaves the hook without any keys at all.
  ...jest.requireActual("../../lib/viewsApi"),
  createView: jest.fn(),
  fetchViews: jest.fn(),
  fetchSidebarEntries: jest.fn(),
  pinPage: jest.fn(),
  unpinEntry: jest.fn(),
  reorderSidebarEntries: jest.fn(),
  updateView: jest.fn(),
  deleteView: jest.fn(),
}));

const mockedApi = api as jest.Mocked<typeof api>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;
const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockPinPage = pinPage as jest.MockedFunction<typeof pinPage>;

const SEED_LABELS = [
  "Home",
  "Chat",
  "Console",
  "Studios",
  "Parallel Execution",
  "MCP Gateway",
  "Branch Triage",
];

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({
    utilityDockEdge: "bottom",
    terminalOpen: false,
    // The persisted default. Nothing resolves it but the Dashboard's picker,
    // where "All workspaces" is a legitimate place to stay.
    workspace: "all",
    baxterHistoryOpen: false,
    chatWorkspaceSlug: "",
  });
  mockedApi.workspaces.mockResolvedValue([
    { slug: "loregarden", name: "Loregarden" },
    { slug: "blobert", name: "Blobert" },
  ] as never);

  // The server-side effect of the seed, so the pins become the list the sidebar
  // then draws.
  const landed: string[] = [];
  mockFetchEntries.mockImplementation(async () =>
    landed.map<SidebarEntry>((pageKey, index) => ({
      id: `e-${pageKey}`,
      position: (index + 1) * 10,
      entry_kind: "page",
      page_key: pageKey,
      view_id: "",
    })),
  );
  mockFetchViews.mockImplementation(async () => []);
  mockPinPage.mockImplementation(async (_slug: string, pageKey: string) => {
    if (!landed.includes(pageKey)) landed.push(pageKey);
    return {
      id: `e-${pageKey}`,
      position: landed.length * 10,
      entry_kind: "page",
      page_key: pageKey,
      view_id: "",
    };
  });
});

test("a default-state app seeds the first workspace and draws all seven page links", async () => {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
        })
      }
    >
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="*" element={<AppLayout>{<div>body</div>}</AppLayout>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  // The first workspace, not `"all"` — which 404s against every view route.
  await waitFor(() => expect(mockFetchEntries).toHaveBeenCalledWith("loregarden"));
  await waitFor(() => expect(mockPinPage).toHaveBeenCalledTimes(SEED_LABELS.length));
  expect(mockPinPage.mock.calls.every(([slug]) => slug === "loregarden")).toBe(true);

  const pinned = await screen.findByRole("list", { name: "Pinned Tabs" });
  for (const label of SEED_LABELS) {
    await waitFor(() => expect(screen.getByRole("link", { name: label })).toBeInTheDocument());
  }
  expect(pinned.querySelectorAll("li")).toHaveLength(SEED_LABELS.length);
});
