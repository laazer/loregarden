/**
 * An agent adds a sidebar tab or shows one: the create goes through the same
 * POST the New View form makes, refreshes the sidebar's two queries, and opens
 * the view; an unknown view fails back to the agent instead of navigating; and
 * a view in another workspace switches the sidebar to it first, since a view
 * route resolves against the sidebar's workspace. An edit is refused when the
 * view changed since the agent read it, and waits behind the page's own writes.
 */

import { MutationObserver, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { uiActionRegistry } from "../../lib/agentActions/registry";
import { viewWriteScope } from "../../hooks/useViewLayoutEdit";
import { viewsKeys } from "../../lib/viewsApi";
import { SidebarWorkspaceProvider } from "../../state/SidebarWorkspaceContext";
import { useUiStore } from "../../state/uiStore";
import { ViewAgentActions, layoutRevision } from "../ViewAgentActions";

const createView = jest.fn();
const fetchView = jest.fn();
const updateView = jest.fn();

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  createView: (slug: string, body: unknown) => createView(slug, body),
  fetchView: (slug: string, viewId: string) => fetchView(slug, viewId),
  updateView: (slug: string, viewId: string, patch: unknown) => updateView(slug, viewId, patch),
}));

const CANVAS = {
  kind: "canvas",
  containers: { c1: { kind: "panel", settings: { primitive_id: "ticket_logs" } } },
  items: [{ id: "n1", container_id: "c1", x: 0, y: 0, width: 400, height: 300 }],
};

const VIEW = { id: "v-1", kind: "canvas", title: "Logs", icon: "", layout: CANVAS };

let location = "";
function Where() {
  location = useLocation().pathname;
  return null;
}

function renderWithSidebarOn(slug: string, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const invalidate = jest.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <SidebarWorkspaceProvider slug={slug}>
        <MemoryRouter initialEntries={["/queue"]}>
          <ViewAgentActions />
          <Where />
        </MemoryRouter>
      </SidebarWorkspaceProvider>
    </QueryClientProvider>,
  );
  return { ...view, invalidate };
}

beforeEach(() => {
  createView.mockReset().mockResolvedValue(VIEW);
  fetchView.mockReset().mockResolvedValue(VIEW);
  updateView.mockReset().mockImplementation(async (_slug: string, id: string, patch: object) => ({
    ...VIEW,
    id,
    viewport: { x: 0, y: 0, zoom: 1 },
    ...patch,
  }));
  useUiStore.getState().setWorkspace("all");
});

it("offers both view actions while mounted", () => {
  const { unmount } = renderWithSidebarOn("loregarden");
  expect(uiActionRegistry.available()).toEqual(["view.create", "view.open", "view.update"]);
  unmount();
  expect(uiActionRegistry.available()).toEqual([]);
});

it("creates the view, refreshes the sidebar, and opens the new tab", async () => {
  const { unmount, invalidate } = renderWithSidebarOn("loregarden");
  let result: unknown;
  await act(async () => {
    result = await uiActionRegistry.run("view.create", {
      workspace_slug: "loregarden",
      title: "Logs",
      layout: CANVAS,
    });
  });

  expect(createView).toHaveBeenCalledWith("loregarden", { title: "Logs", icon: "", layout: CANVAS });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: viewsKeys.views("loregarden") });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: viewsKeys.sidebarEntries("loregarden") });
  await waitFor(() => expect(location).toBe("/view/v-1"));
  expect(result).toEqual({
    view_id: "v-1",
    title: "Logs",
    kind: "canvas",
    revision: layoutRevision(CANVAS),
    path: "/view/v-1",
  });
  // Same workspace: the operator's chosen workspace is left alone.
  expect(useUiStore.getState().workspace).toBe("all");
  unmount();
});

it("refuses an unknown view without navigating", async () => {
  fetchView.mockRejectedValue(new Error("View not found"));
  const { unmount } = renderWithSidebarOn("loregarden");
  await expect(
    uiActionRegistry.run("view.open", { workspace_slug: "loregarden", view_id: "nope" }),
  ).rejects.toThrow(/nope/);
  expect(location).toBe("/queue");
  unmount();
});

it("switches the sidebar to the view's workspace before showing it", async () => {
  const { unmount } = renderWithSidebarOn("loregarden");
  await act(() => uiActionRegistry.run("view.open", { workspace_slug: "blobert", view_id: "v-1" }));
  expect(fetchView).toHaveBeenCalledWith("blobert", "v-1");
  expect(useUiStore.getState().workspace).toBe("blobert");
  await waitFor(() => expect(location).toBe("/view/v-1"));

  await act(() =>
    uiActionRegistry.run("view.create", { workspace_slug: "lore-eden", title: "Logs", layout: CANVAS }),
  );
  expect(useUiStore.getState().workspace).toBe("lore-eden");
  unmount();
});

const MOVED = { ...CANVAS, items: [{ ...CANVAS.items[0], x: 120 }] };

it("replaces the layout when it is unchanged since the agent read it", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pannedTo = { x: 5, y: 5, zoom: 2 };
  client.setQueryData(viewsKeys.view("loregarden", "v-1"), { ...VIEW, viewport: pannedTo });
  const { unmount } = renderWithSidebarOn("loregarden", client);
  let result: unknown;
  await act(async () => {
    result = await uiActionRegistry.run("view.update", {
      workspace_slug: "loregarden",
      view_id: "v-1",
      layout: MOVED,
      base_revision: layoutRevision(CANVAS),
    });
  });

  expect(updateView).toHaveBeenCalledWith("loregarden", "v-1", { layout: MOVED });
  // The page composes its next edit from this cache entry, so it holds the
  // agent's layout — and keeps the operator's viewport, which the PATCH did not set.
  expect(client.getQueryData(viewsKeys.view("loregarden", "v-1"))).toMatchObject({
    layout: MOVED,
    viewport: pannedTo,
  });
  expect(result).toMatchObject({ revision: layoutRevision(MOVED), path: "/view/v-1" });
  await waitFor(() => expect(location).toBe("/view/v-1"));
  unmount();
});

it("refuses a layout made from a stale read, writing nothing", async () => {
  fetchView.mockResolvedValue({ ...VIEW, layout: MOVED });
  const { unmount } = renderWithSidebarOn("loregarden");
  await expect(
    uiActionRegistry.run("view.update", {
      workspace_slug: "loregarden",
      view_id: "v-1",
      layout: CANVAS,
      base_revision: layoutRevision(CANVAS),
    }),
  ).rejects.toThrow(/changed since revision/);
  expect(updateView).not.toHaveBeenCalled();
  expect(location).toBe("/queue");
  unmount();
});

it("renames without a revision, since a title cannot clobber a layout", async () => {
  const { unmount } = renderWithSidebarOn("loregarden");
  await act(() =>
    uiActionRegistry.run("view.update", { workspace_slug: "loregarden", view_id: "v-1", title: "Renamed" }),
  );
  expect(fetchView).not.toHaveBeenCalled();
  expect(updateView).toHaveBeenCalledWith("loregarden", "v-1", { title: "Renamed" });
  unmount();
});

it("waits behind a layout write the page already has in flight", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let release: () => void = () => {};
  const pageWrite = new MutationObserver(client, {
    scope: { id: viewWriteScope("loregarden", "v-1") },
    mutationFn: () => new Promise<void>((resolve) => (release = resolve)),
  }).mutate();
  const { unmount } = renderWithSidebarOn("loregarden", client);

  let agentWrite: Promise<unknown> = Promise.resolve();
  act(() => {
    agentWrite = uiActionRegistry.run("view.update", {
      workspace_slug: "loregarden",
      view_id: "v-1",
      layout: MOVED,
      base_revision: layoutRevision(CANVAS),
    });
  });
  // Neither the revision read nor the PATCH may start while the page's write is open.
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(fetchView).not.toHaveBeenCalled();
  expect(updateView).not.toHaveBeenCalled();

  await act(async () => {
    release();
    await pageWrite;
    await agentWrite;
  });
  expect(updateView).toHaveBeenCalledTimes(1);
  unmount();
});
