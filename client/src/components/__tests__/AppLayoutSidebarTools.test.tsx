/**
 * The default state of a fresh install: `uiStore.workspace` is the persisted
 * `"all"`, nothing has ever opened the Dashboard's picker, and the workspace
 * list has loaded. That is the state most users are in on first launch, and it
 * must still produce navigation.
 *
 * This renders the *real* `AppSidebar` through `AppLayout`, unlike
 * `AppLayoutChrome.test.tsx`, because what is under test is that navigation
 * survives the whole composed chrome with an empty store behind it.
 *
 * Before ticket 472 this file tested the opposite arrangement: the layout told
 * the sidebar whether it might seed, and the sidebar answered an empty read by
 * pinning seven pages. The seed is gone, and these tests pin why — the seven
 * pages are a static catalog, so there is no read that can come back short and
 * no write that has to succeed for the app to be navigable.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AppLayout } from "../AppLayout";
import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import { useUiStore } from "../../state/uiStore";
import {
  fetchSidebarEntries,
  fetchViews,
  reorderSidebarEntries,
  setEntryPinned,
} from "../../lib/viewsApi";

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
    ticketStatusSummary: jest.fn(async () => ({
      backlog: 0,
      in_progress: 0,
      blocked: 0,
      parked: 0,
      done: 0,
      wont_do: 0,
      running: 0,
      awaiting: 0,
      queued: 0,
      idle: 0,
    })),
  },
}));

jest.mock("../../lib/viewsApi", () => ({
  // The module also exports the query-key factory the sidebar hook reads its
  // cache keys from; mocking it away leaves the hook without any keys at all.
  ...jest.requireActual("../../lib/viewsApi"),
  createView: jest.fn(),
  fetchViews: jest.fn(),
  fetchSidebarEntries: jest.fn(),
  setEntryPinned: jest.fn(),
  reorderSidebarEntries: jest.fn(),
  updateView: jest.fn(),
  deleteView: jest.fn(),
}));

const mockedApi = api as jest.Mocked<typeof api>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;
const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockSetPinned = setEntryPinned as jest.MockedFunction<typeof setEntryPinned>;
const mockReorder = reorderSidebarEntries as jest.MockedFunction<typeof reorderSidebarEntries>;

/** The built-in pages, in rail order: Work, then Build, then System. */
const TOOL_LABELS = [
  "Home",
  "Chat",
  "Console",
  "Work items",
  "Initiatives",
  "Editor",
  "Studios",
  "Branch Triage",
  "Machine",
  "MCP Gateway",
  "Workspaces",
  "Memory",
];

/** Each built-in page's name, in the order the rail draws them across its groups. */
function toolNames(): string[] {
  return ["Work", "Build", "System"].flatMap((name) =>
    Array.from(
      screen.getByRole("list", { name }).querySelectorAll("li .app-sidebar-name"),
      (el) => el.textContent ?? "",
    ),
  );
}

function renderApp() {
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
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({
    utilityDockEdge: "bottom",
    terminalOpen: false,
    // The persisted default. Nothing resolves it but the Dashboard's picker,
    // where "All workspaces" is a legitimate place to stay.
    workspace: "all",
    baxterHistoryOpen: false,
    chatScope: "",
  });
  mockedApi.workspaces.mockResolvedValue([
    { slug: "loregarden", name: "Loregarden" },
    { slug: "blobert", name: "Blobert" },
  ] as never);
  mockFetchEntries.mockImplementation(async () => []);
  mockFetchViews.mockImplementation(async () => []);
});

// AC2 — all seven built-in pages appear in Tools on a fresh workspace with no
// stored entries.

test("a default-state app draws every built-in page with an empty store", async () => {
  renderApp();

  // The first workspace, not `"all"` — which 404s against every view route.
  await waitFor(() => expect(mockFetchEntries).toHaveBeenCalledWith("loregarden"));

  await screen.findByRole("list", { name: "Work" });
  expect(toolNames()).toEqual(TOOL_LABELS);
});

// AC5 — Tools is derived from the static page catalog rather than from
// sidebar_entries, so it needs no seeding.

test("nothing is written to set the sidebar up", async () => {
  renderApp();

  await screen.findByRole("list", { name: "Work" });
  await waitFor(() => expect(mockFetchEntries).toHaveBeenCalled());
  // The whole point of deriving Tools: an empty workspace is a finished
  // workspace. A seed would show up here as a write nobody asked for.
  expect(mockSetPinned).not.toHaveBeenCalled();
  expect(mockReorder).not.toHaveBeenCalled();
});

// AC3 — no stored state could omit a built-in page.

test("the pages are still all there when the entries request fails outright", async () => {
  // The strongest form of "cannot drift from the app's routes": not merely an
  // empty list, but no list at all. A Tools section read from `sidebar_entries`
  // renders nothing here, and the user has no navigation and no control that
  // brings it back — which is the failure this ticket exists to remove.
  mockFetchEntries.mockRejectedValue(new ApiError(500, "sidebar unavailable"));
  renderApp();

  await screen.findByRole("list", { name: "Work" });
  expect(toolNames()).toEqual(TOOL_LABELS);
});
