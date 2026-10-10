import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { LocalInstance } from "../../api/localInstancesTypes";
import * as viewsApi from "../../lib/viewsApi";
import type { ViewLayout, ViewSummary } from "../../lib/viewsApi";
import { emptyLayoutFor } from "../../lib/viewLayouts";
import { platform } from "../../services/platform";
import { SidebarWorkspaceProvider } from "../../state/SidebarWorkspaceContext";
import { useToastStore } from "../../state/toastStore";
import { LocalInstanceMenu } from "../LocalInstanceMenu";

jest.mock("../../services/platform", () => ({
  platform: { openExternal: jest.fn() },
}));

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchViews: jest.fn(),
  fetchView: jest.fn(),
  createView: jest.fn(),
  updateView: jest.fn(),
}));

const mockPlatform = platform as jest.Mocked<typeof platform>;
const mockViews = viewsApi as jest.Mocked<typeof viewsApi>;

function viewRecord(id: string, title: string, layout: ViewLayout): ViewSummary {
  return { id, title, icon: "", kind: layout.kind, layout } as unknown as ViewSummary;
}

const BOARD = viewRecord("v-1", "Board", emptyLayoutFor("flex_grid"));

const instance = (overrides: Partial<LocalInstance> = {}): LocalInstance => ({
  id: "server-feat-x-a1b2c3",
  project: "loregarden",
  name: "server-feat-x",
  kind: "server",
  role: "branch",
  template: "server",
  managed: true,
  url: "http://127.0.0.1:8101",
  health_path: "/health",
  ready_timeout_seconds: 120,
  log_path: null,
  target_instance_id: null,
  labels: {},
  started_at: "2026-09-30T00:00:00Z",
  exit_code: null,
  last_error: null,
  state: "ready",
  ...overrides,
});

const items = () => screen.getAllByRole("menuitem").map((item) => item.textContent);

function openMenu(of = instance()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SidebarWorkspaceProvider slug="loregarden">
        <LocalInstanceMenu instance={of} />
      </SidebarWorkspaceProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: `More actions for ${of.name}` }));
}

/** The settings of the single container in a layout the menu just wrote. */
function writtenSettings(layout: ViewLayout): Record<string, unknown> {
  const containers = Object.values(layout.containers as Record<string, { settings: Record<string, unknown> }>);
  expect(containers).toHaveLength(1);
  return containers[0].settings;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPlatform.openExternal.mockResolvedValue(undefined);
  mockViews.fetchViews.mockResolvedValue([BOARD]);
  mockViews.fetchView.mockResolvedValue(BOARD);
  mockViews.createView.mockImplementation(async (_slug, body) => viewRecord("v-new", body.title, body.layout));
  mockViews.updateView.mockImplementation(async (_slug, id, patch) =>
    viewRecord(id, "Board", patch.layout as ViewLayout),
  );
  useToastStore.setState({ toasts: [] });
});

it("offers the operator's loregarden tabs, not browser tabs", async () => {
  openMenu();
  await screen.findByRole("menuitem", { name: "Board" });
  expect(items()).toEqual(["Open in browser", "Copy URL", "New tab", "Board"]);
});

it("embeds the instance's site in a new loregarden tab named for it", async () => {
  openMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "New tab" }));
  await waitFor(() => expect(mockViews.createView).toHaveBeenCalledTimes(1));
  const [slug, body] = mockViews.createView.mock.calls[0];
  expect(slug).toBe("loregarden");
  expect(body.title).toBe("server-feat-x");
  expect(writtenSettings(body.layout)).toMatchObject({ primitive_id: "web_embed", url: "http://127.0.0.1:8101" });
});

it("embeds the instance's site in an existing loregarden tab", async () => {
  openMenu();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Board" }));
  await waitFor(() => expect(mockViews.updateView).toHaveBeenCalledTimes(1));
  const [, viewId, patch] = mockViews.updateView.mock.calls[0];
  expect(viewId).toBe("v-1");
  expect(writtenSettings(patch.layout as ViewLayout)).toMatchObject({
    primitive_id: "web_embed",
    url: "http://127.0.0.1:8101",
  });
});

it("still opens the site in the browser on request", () => {
  openMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in browser" }));
  expect(mockPlatform.openExternal).toHaveBeenCalledWith("http://127.0.0.1:8101");
});

it("offers no open or embed for an exited instance, whose port has nothing behind it", () => {
  openMenu(instance({ state: "exited" }));
  expect(items()).toEqual(["Copy URL"]);
  expect(mockViews.fetchViews).not.toHaveBeenCalled();
});

it("says why an open failed", async () => {
  mockPlatform.openExternal.mockRejectedValue(new Error("The browser blocked the tab"));
  openMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in browser" }));
  await waitFor(() =>
    expect(useToastStore.getState().toasts).toContainEqual(
      expect.objectContaining({ tone: "error", title: "Open instance failed", message: "The browser blocked the tab" }),
    ),
  );
});
