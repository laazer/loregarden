/**
 * The "add this to a tab" menu, and what it actually writes.
 *
 * The claim being tested is a round trip: a surface that knows what it is
 * showing produces a pane already configured for that thing. So the assertions
 * read the POST and PATCH bodies rather than the menu's own state — a menu that
 * opened, listed tabs and wrote the wrong settings would pass every UI-shaped
 * assertion.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AddToTabMenu } from "../AddToTabMenu";
import { ToastHost } from "../ToastHost";
import { useToastStore } from "../../state/toastStore";
import { SidebarWorkspaceProvider } from "../../state/SidebarWorkspaceContext";
import * as viewsApi from "../../lib/viewsApi";
import type { ViewLayout, ViewSummary } from "../../lib/viewsApi";
import { emptyLayoutFor } from "../../lib/viewLayouts";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchViews: jest.fn(),
  fetchView: jest.fn(),
  createView: jest.fn(),
  updateView: jest.fn(),
}));

const mockViews = viewsApi as jest.Mocked<typeof viewsApi>;

function viewRecord(id: string, title: string, layout: ViewLayout): ViewSummary {
  return { id, title, icon: "", kind: layout.kind, layout } as unknown as ViewSummary;
}

const BOARD = viewRecord("v-1", "Board", emptyLayoutFor("flex_grid"));

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  mockViews.fetchViews.mockResolvedValue([BOARD]);
  mockViews.fetchView.mockResolvedValue(BOARD);
  mockViews.createView.mockImplementation(async (_slug, body) =>
    viewRecord("v-new", body.title, body.layout),
  );
  mockViews.updateView.mockImplementation(async (_slug, id, patch) =>
    viewRecord(id, "Board", patch.layout as ViewLayout),
  );
});

function renderMenu(props?: Partial<React.ComponentProps<typeof AddToTabMenu>>, slug = "loregarden") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SidebarWorkspaceProvider slug={slug}>
          {/* The real host, so the confirmation is asserted where an operator
              would read it rather than in the store behind it. */}
          <ToastHost />
          <AddToTabMenu
            primitiveId="queue_lane"
            values={new Map([["slot", "2"]])}
            title="Lane 2"
            label="Add lane 2 to a tab"
            {...props}
          />
        </SidebarWorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The settings of the single container in a layout the menu just wrote. */
function onlySettings(layout: ViewLayout): Record<string, unknown> {
  const values = Object.values(layout.containers as Record<string, { settings: Record<string, unknown> }>);
  expect(values).toHaveLength(1);
  return values[0].settings;
}

describe("what the menu offers", () => {
  it("lists a new tab and every existing one", async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole("button", { name: "Add lane 2 to a tab" }));
    expect(await screen.findByRole("menuitem", { name: "New tab" })).toBeInTheDocument();
    expect(await screen.findByRole("menuitem", { name: "Board" })).toBeInTheDocument();
  });

  it("offers nothing for a primitive the map says has no home", () => {
    // `web_embed` points at an arbitrary URL and no page here is about it. The
    // map records that, and the menu honours it rather than rendering a control
    // with nowhere sensible to appear.
    const { container } = renderMenu({ primitiveId: "web_embed" });
    expect(container).toBeEmptyDOMElement();
  });

  it("offers nothing for a primitive this build does not have", () => {
    const { container } = renderMenu({ primitiveId: "not_a_primitive" });
    expect(container).toBeEmptyDOMElement();
  });
});

describe("what it writes", () => {
  it("creates a tab holding the pane the surface described", async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole("button", { name: "Add lane 2 to a tab" }));
    await user.click(await screen.findByRole("menuitem", { name: "New tab" }));

    await waitFor(() => expect(mockViews.createView).toHaveBeenCalledTimes(1));
    const [, body] = mockViews.createView.mock.calls[0];
    // The tab is named for the thing, not the primitive's type: "Lane 2" is
    // what an operator finds in a tab list.
    expect(body.title).toBe("Lane 2");
    expect(onlySettings(body.layout)).toMatchObject({ primitive_id: "queue_lane", slot: "2" });
  });

  it("appends to an existing tab without dropping what is in it", async () => {
    const user = userEvent.setup();
    const occupied = viewRecord(
      "v-1",
      "Board",
      // A tab that already holds a terminal — the pane that must survive.
      (() => {
        const seed = emptyLayoutFor("flex_grid");
        const id = Object.keys(seed.containers as Record<string, unknown>)[0];
        return {
          ...seed,
          containers: { [id]: { kind: "terminal", settings: { primitive_id: "terminal" } } },
        } as ViewLayout;
      })(),
    );
    mockViews.fetchView.mockResolvedValue(occupied);

    renderMenu();
    await user.click(screen.getByRole("button", { name: "Add lane 2 to a tab" }));
    await user.click(await screen.findByRole("menuitem", { name: "Board" }));

    await waitFor(() => expect(mockViews.updateView).toHaveBeenCalledTimes(1));
    const [, viewId, patch] = mockViews.updateView.mock.calls[0];
    expect(viewId).toBe("v-1");
    const stored = Object.values(
      (patch.layout as ViewLayout).containers as Record<string, { settings: Record<string, unknown> }>,
    ).map((container) => container.settings.primitive_id);
    expect(stored.sort()).toEqual(["queue_lane", "terminal"]);
  });

  it("reads the target's layout fresh rather than from the list it listed", async () => {
    // The list entry carries the layout as it was when the list was fetched.
    // Appending to that drops anything added since — in another tab, or by the
    // last use of this very menu.
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole("button", { name: "Add lane 2 to a tab" }));
    await user.click(await screen.findByRole("menuitem", { name: "Board" }));

    await waitFor(() => expect(mockViews.updateView).toHaveBeenCalled());
    expect(mockViews.fetchView).toHaveBeenCalledWith("loregarden", "v-1");
  });

  it("says where it went, because nothing on this page changes", async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole("button", { name: "Add lane 2 to a tab" }));
    await user.click(await screen.findByRole("menuitem", { name: "New tab" }));

    // A silent success is indistinguishable from a menu that did nothing: the
    // pane lands in a tab the operator is not looking at.
    expect(await screen.findByText(/Queue Lane added to Lane 2/i)).toBeInTheDocument();
  });
});
