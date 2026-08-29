/**
 * The sidebar footer's pair of controls: **Pin tab** and **New tab** (473).
 *
 * One control becomes two. `434` put a "Pin a page" menu in the footer and
 * `438` put a "New View" button in the Tabs section head, so the two ways a tab
 * appears were a footer menu and a section control, reading as unrelated. `472`
 * then made Tools static and there were no pages left to pin, which is what
 * decides the open question `473` inherits: Pin tab pins a *view*, taking an
 * unpinned tab out of Tabs and into Pinned Tabs.
 *
 * What these tests are for, and what each will not claim:
 *
 *   - **The flows are moved, not rebuilt.** New tab must open `438`'s modal —
 *     the kind picker, and the non-optimistic create that waits for the
 *     server's id. The assertions here are that the modal appears and that
 *     `createView` is what runs; the flow's own behaviour is covered where it
 *     was written, in `AppSidebarViewActions.test.tsx`, and is not restated.
 *   - **The pin menu keeps its dismissal contract**, added at `436`'s review:
 *     Escape, a pointer press outside it, and arrow-key movement between its
 *     items. A second control beside it is the realistic way outside-click
 *     detection breaks, so New tab is used as the outside press.
 *   - **Names are real text in both states.** A `title` attribute resolves an
 *     accessible name in dom-accessibility-api, so a name-based query alone
 *     passes for a tooltip-only control — the thing `434` replaced. The
 *     collapsed assertion is therefore on the text node.
 *   - **Height is not measured.** AC6 asks that the second control not push the
 *     footer out of reach. jsdom reports every element at 0px, so what is
 *     checkable here is the structural cause — the two controls occupy one row
 *     of the footer rather than two, and the footer is not inside anything that
 *     scrolls. Whether the rail is tall enough at a real viewport is a browser
 *     check, and is recorded as a gap.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AppSidebar } from "../AppSidebar";
import {
  createView,
  fetchSidebarEntries,
  fetchViews,
  setEntryPinned,
  type SidebarEntry,
  type ViewSummary,
} from "../../lib/viewsApi";
import { useToastStore } from "../../state/toastStore";

jest.mock("../../lib/viewsApi", () => ({
  // The query-key factory is a real export the sidebar hook reads its cache
  // keys from; mocking the module away wholesale leaves it with no keys at all.
  ...jest.requireActual("../../lib/viewsApi"),
  createView: jest.fn(),
  fetchViews: jest.fn(),
  fetchSidebarEntries: jest.fn(),
  setEntryPinned: jest.fn(),
  reorderSidebarEntries: jest.fn(),
  updateView: jest.fn(),
  deleteView: jest.fn(),
}));

const mockCreateView = createView as jest.MockedFunction<typeof createView>;
const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;
const mockSetPinned = setEntryPinned as jest.MockedFunction<typeof setEntryPinned>;

const SLUG = "loregarden";

function viewEntry(id: string, position: number, viewId: string, pinned: boolean): SidebarEntry {
  return { id, position, entry_kind: "view", page_key: "", view_id: viewId, pinned };
}

function view(id: string, title: string): ViewSummary {
  return {
    id,
    kind: "flex_grid",
    title,
    icon: "",
    layout: { kind: "flex_grid", root: null },
    viewport: {},
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

const ENTRIES: SidebarEntry[] = [
  viewEntry("e-grid", 30, "v-grid", true),
  viewEntry("e-canvas", 60, "v-canvas", false),
  viewEntry("e-scratch", 90, "v-scratch", false),
];

const VIEWS: ViewSummary[] = [
  view("v-grid", "Build Board"),
  view("v-canvas", "Sketch Surface"),
  view("v-scratch", "Scratch Pad"),
];

function renderSidebar(slug = SLUG) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AppSidebar workspaceSlug={slug} onOpenSettings={jest.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The rail, once the stored entries have arrived and the rows are drawn. */
async function renderLoadedSidebar() {
  const utils = renderSidebar();
  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  await screen.findByRole("link", { name: "Build Board" });
  return { ...utils, nav };
}

function pinButton(): HTMLElement {
  return screen.getByRole("button", { name: "Pin tab" });
}

function newTabButton(): HTMLElement {
  return screen.getByRole("button", { name: "New tab" });
}

/** The container holding the pair, which is what makes them one footer row. */
function footerControls(): HTMLElement {
  const controls = newTabButton().closest(".app-sidebar-footer-controls");
  if (!controls) throw new Error("New tab is not in the footer control pair");
  return controls as HTMLElement;
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  mockFetchEntries.mockResolvedValue(ENTRIES);
  mockFetchViews.mockResolvedValue(VIEWS);
  mockSetPinned.mockImplementation(async (_slug, entryId, pinned) => ({
    ...(ENTRIES.find((entry) => entry.id === entryId) ?? ENTRIES[0]),
    pinned,
  }));
  mockCreateView.mockResolvedValue(view("v-new", "Fresh"));
});

// AC1 — two adjacent controls in the footer, a pin and a plus.

test("the footer holds Pin tab and New tab, side by side", async () => {
  await renderLoadedSidebar();

  const pair = footerControls();
  expect(within(pair).getByRole("button", { name: "Pin tab" })).toBeInTheDocument();
  expect(within(pair).getByRole("button", { name: "New tab" })).toBeInTheDocument();

  // Adjacency is the point of the ticket: two controls that read as siblings
  // rather than one in a section head and one in the footer. Both being
  // somewhere on the page would pass with them left where 434 and 438 put them.
  const order = Array.from(pair.querySelectorAll("button")).map((button) =>
    button.textContent?.trim(),
  );
  expect(order).toEqual(["Pin tab", "New tab"]);
});

test("the two controls carry different icons", async () => {
  await renderLoadedSidebar();

  // The icons are the only thing telling them apart on a 60px rail, so drawing
  // the same glyph twice is a real failure and not a cosmetic one. The plus is
  // named exactly; the pin only has to differ from it, because its path is a
  // drawing and pinning it here would make this a test of an SVG.
  const plus = newTabButton().querySelector("svg path")?.getAttribute("d");
  const pin = pinButton().querySelector("svg path")?.getAttribute("d");
  expect(plus).toBe("M12 5v14M5 12h14");
  expect(pin).toBeTruthy();
  expect(pin).not.toBe(plus);
});

// AC2 — New tab opens 438's flow, and does not reimplement it.

test("New tab opens the existing New View form", async () => {
  const user = userEvent.setup();
  await renderLoadedSidebar();

  await user.click(newTabButton());

  const dialog = await screen.findByRole("dialog");
  // 438's modal, identified by what it collects: a name and a layout kind. A
  // reimplementation that merely POSTed a default view would open no dialog at
  // all, and one that opened a bare prompt would have no kind to pick.
  expect(within(dialog).getByRole("textbox")).toBeInTheDocument();
  expect(within(dialog).getByText(/canvas/i)).toBeInTheDocument();
  // Nothing is created by opening the form — the create waits for the server's
  // id, and an optimistic create is what 438 was careful not to do.
  expect(mockCreateView).not.toHaveBeenCalled();
});

// AC3 — Pin tab opens the pin menu, with its dismissal and arrow keys intact.

test("Pin tab offers the tabs that are not pinned, and pins the one chosen", async () => {
  const user = userEvent.setup();
  await renderLoadedSidebar();

  await user.click(pinButton());

  const menu = await screen.findByRole("menu", { name: "Tabs to pin" });
  const items = within(menu)
    .getAllByRole("menuitem")
    .map((item) => item.textContent);
  // Build Board is already in Pinned Tabs; offering it would be a write that
  // changes nothing.
  expect(items).toEqual(["Sketch Surface", "Scratch Pad"]);

  await user.click(within(menu).getByRole("menuitem", { name: "Scratch Pad" }));

  await waitFor(() => expect(mockSetPinned).toHaveBeenCalledTimes(1));
  expect(mockSetPinned).toHaveBeenCalledWith(SLUG, "e-scratch", true);
  expect(screen.queryByRole("menu")).toBeNull();
});

test("Pin tab is offered but inert when every tab is already pinned", async () => {
  mockFetchEntries.mockResolvedValue([
    viewEntry("e-grid", 30, "v-grid", true),
    viewEntry("e-canvas", 60, "v-canvas", true),
    viewEntry("e-scratch", 90, "v-scratch", true),
  ]);
  const user = userEvent.setup();
  await renderLoadedSidebar();

  expect(pinButton()).toBeDisabled();
  await user.click(pinButton());
  expect(screen.queryByRole("menu")).toBeNull();
});

test("Escape closes the pin menu", async () => {
  const user = userEvent.setup();
  await renderLoadedSidebar();

  await user.click(pinButton());
  await screen.findByRole("menu");

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("menu")).toBeNull();
  expect(mockSetPinned).not.toHaveBeenCalled();
});

test("pressing New tab beside it dismisses the pin menu", async () => {
  // The control that landed next to the menu is the realistic way outside-click
  // detection breaks: a root ref widened to hold both would treat this press as
  // inside the menu and leave it open behind the dialog.
  const user = userEvent.setup();
  await renderLoadedSidebar();

  await user.click(pinButton());
  await screen.findByRole("menu");

  await user.click(newTabButton());

  await screen.findByRole("dialog");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("the arrow keys move between the pin menu's items", async () => {
  const user = userEvent.setup();
  await renderLoadedSidebar();

  await user.click(pinButton());
  const menu = await screen.findByRole("menu");
  const [first, second] = within(menu).getAllByRole("menuitem");

  first.focus();
  await user.keyboard("{ArrowDown}");
  expect(second).toHaveFocus();

  await user.keyboard("{ArrowUp}");
  expect(first).toHaveFocus();
});

// AC4 — both usable in the collapsed rail, each named in both states.

test("both controls keep a name that is text in the row, collapsed and expanded", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  // Collapsed. The names are clipped by CSS, which jsdom does not apply, so what
  // is asserted is that they are still *there* — the failure this guards is a
  // rail that unmounts its labels or falls back to a `title` tooltip, which is
  // exactly what 434 replaced and what a name-only query cannot tell apart.
  expect(nav).toHaveAttribute("data-expanded", "false");
  for (const control of [pinButton(), newTabButton()]) {
    expect(control).not.toHaveAttribute("title");
    expect(control).not.toHaveAttribute("aria-label");
  }
  expect(pinButton().textContent).toContain("Pin tab");
  expect(newTabButton().textContent).toContain("New tab");

  await user.hover(nav);
  expect(nav).toHaveAttribute("data-expanded", "true");
  expect(pinButton().textContent).toContain("Pin tab");
  expect(newTabButton().textContent).toContain("New tab");
});

test("the collapsed rail's Pin control still opens its menu", async () => {
  // Expansion answers focus as well as hover (434), so reaching the control
  // without a pointer expands the rail around it rather than leaving the menu
  // to open inside a 60px column.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  act(() => pinButton().focus());
  expect(nav).toHaveAttribute("data-expanded", "true");
  await user.click(pinButton());

  expect(await screen.findByRole("menu")).toBeInTheDocument();
});

// AC5 — one tab stop each, and no new stops on the rows.

test("each control is one tab stop, and the rows gain none", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);

  const stops: HTMLElement[] = [];
  for (let press = 0; press < 40; press += 1) {
    await user.tab();
    const active = document.activeElement as HTMLElement;
    if (!nav.contains(active) || stops.includes(active)) break;
    stops.push(active);
  }

  const named = stops.map((stop) => stop.textContent?.trim());
  expect(named.filter((name) => name === "Pin tab")).toHaveLength(1);
  expect(named.filter((name) => name === "New tab")).toHaveLength(1);
  // One stop per entry: the seven Tools rows, three tabs, the pair and
  // Settings — and nothing from a row's own controls, which are reached with
  // Left/Right instead.
  expect(named).toEqual([
    "Home",
    "Chat",
    "Console",
    "Studios",
    "Parallel Execution",
    "MCP Gateway",
    "Branch Triage",
    "Build Board",
    "Sketch Surface",
    "Scratch Pad",
    "Pin tab",
    "New tab",
    "Settings",
  ]);
});

// AC6 — the pair does not cost the footer a second row.

test("the two controls share one footer row, outside anything that scrolls", async () => {
  await renderLoadedSidebar();

  // jsdom measures nothing, so the height claim is structural: both controls are
  // children of one container, rather than two stacked footer buttons, and that
  // container is a sibling of the sections rather than inside one of the lists
  // that scroll. A real height check needs a browser.
  const pair = footerControls();
  expect(pinButton().parentElement?.parentElement).toBe(pair);
  expect(newTabButton().parentElement).toBe(pair);
  expect(pair.closest(".app-sidebar-list")).toBeNull();
  expect(pair.closest(".app-sidebar-section")).toBeNull();
});
