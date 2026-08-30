/**
 * Independent scrolling for each sidebar section (461).
 *
 * Tools, Pinned Tabs and Tabs all grow without bound — Tabs by design, since a
 * user can create as many views as they like. Before this, a long list ran off
 * the bottom of a full-height rail with nothing to scroll, and the first thing
 * it pushed out of reach was the footer, which holds the controls for fixing
 * exactly that.
 *
 * **What this file can and cannot check.** jsdom has no layout engine: every
 * element measures 0px, nothing overflows, and no scrollbar exists. So not one
 * assertion below observes scrolling. What they observe is the structure that
 * makes scrolling happen and the structure that makes it independent — three
 * separate scroll containers, one per section, with the footer outside all of
 * them and the panel around them not scrolling at all.
 *
 * Two of those facts live in CSS, which jest maps to a stub, so `getComputedStyle`
 * would report nothing whatever the stylesheet says. They are therefore read out
 * of the stylesheet source, the way `containerPrimitives.smallSize.test.tsx`
 * does. That is a weaker test than a browser measurement and is not pretending
 * otherwise; the real overflow behaviour is a verify-stage browser check, and is
 * recorded here as a gap.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "fs";
import path from "path";
import { MemoryRouter } from "react-router-dom";

import { AppSidebar } from "../AppSidebar";
import {
  fetchSidebarEntries,
  fetchViews,
  type SidebarEntry,
  type ViewSummary,
} from "../../lib/viewsApi";

// Every test here drives the UI through `userEvent`, whose interaction chains
// are event-loop bound rather than CPU bound. On a loaded machine — the
// pre-push hook runs this suite alongside a full pytest run — those chains
// routinely pass jest's default 5s budget and the file's first timeout
// cascades into "unable to find an element" for every test after it. The work
// still completes (this file runs in ~9s idle, ~45s loaded), so the budget is
// what is wrong, not the tests.
jest.setTimeout(20_000);


jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  createView: jest.fn(),
  fetchViews: jest.fn(),
  fetchSidebarEntries: jest.fn(),
  setEntryPinned: jest.fn(),
  reorderSidebarEntries: jest.fn(),
  updateView: jest.fn(),
  deleteView: jest.fn(),
}));

const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;

const SLUG = "loregarden";

/**
 * More tabs than a rail can show, because the case this ticket is about is the
 * long list. Nothing here measures them — they are what makes the fixture
 * honest about which list would be the one overflowing.
 */
const TAB_COUNT = 24;

function entriesFixture(): SidebarEntry[] {
  const entries: SidebarEntry[] = [
    { id: "e-pin-1", position: 10, entry_kind: "view", page_key: "", view_id: "v-pin-1", pinned: true },
    { id: "e-pin-2", position: 20, entry_kind: "view", page_key: "", view_id: "v-pin-2", pinned: true },
  ];
  for (let index = 0; index < TAB_COUNT; index += 1) {
    entries.push({
      id: `e-tab-${index}`,
      position: 100 + index * 10,
      entry_kind: "view",
      page_key: "",
      view_id: `v-tab-${index}`,
      pinned: false,
    });
  }
  return entries;
}

function viewsFixture(): ViewSummary[] {
  const make = (id: string, title: string): ViewSummary => ({
    id,
    kind: "flex_grid",
    title,
    icon: "",
    layout: { kind: "flex_grid", root: null },
    viewport: {},
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  });
  return [
    make("v-pin-1", "Build Board"),
    make("v-pin-2", "Release Notes"),
    ...Array.from({ length: TAB_COUNT }, (_, index) => make(`v-tab-${index}`, `Tab ${index}`)),
  ];
}

function renderSidebar(path = "/") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AppSidebar workspaceSlug={SLUG} onOpenSettings={jest.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function renderLoadedSidebar(path = "/") {
  const utils = renderSidebar(path);
  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  await screen.findByRole("link", { name: "Build Board" });
  return { ...utils, nav };
}

/** The `ul` for a section, found through the heading that labels it. */
function sectionList(heading: string): HTMLElement {
  return screen.getByRole("list", { name: heading });
}

function panel(): HTMLElement {
  const found = document.querySelector(".app-sidebar-panel");
  if (!found) throw new Error("No sidebar panel");
  return found as HTMLElement;
}

/**
 * `AppSidebar.css`, parsed into selector → declarations.
 *
 * jest maps `*.css` imports to a stub, so no stylesheet ever reaches the
 * document and `getComputedStyle` cannot see a single rule in it. Reading the
 * file is the only way a rule written in CSS counts for anything here — and the
 * alternative, demanding inline styles, is a constraint this ticket does not
 * impose and this repo does not follow.
 */
function sidebarCss(): { selector: string; declarations: Record<string, string> }[] {
  const source = fs
    .readFileSync(path.resolve(__dirname, "..", "AppSidebar.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  const rules: { selector: string; declarations: Record<string, string> }[] = [];
  for (const [, selector, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const declarations: Record<string, string> = {};
    for (const declaration of body.split(";")) {
      const [property, ...rest] = declaration.split(":");
      if (rest.length === 0) continue;
      declarations[property.trim().toLowerCase()] = rest.join(":").trim().toLowerCase();
    }
    rules.push({ selector: selector.trim(), declarations });
  }
  return rules;
}

/** Every declaration any rule whose selector *ends* at this one contributes. */
function declaredFor(selector: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rule of sidebarCss()) {
    for (const part of rule.selector.split(",")) {
      if (part.trim().endsWith(selector)) Object.assign(out, rule.declarations);
    }
  }
  return out;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFetchEntries.mockResolvedValue(entriesFixture());
  mockFetchViews.mockResolvedValue(viewsFixture());
});

// AC1 — each section scrolls on its own.

test("each of the three sections is its own scroll container", async () => {
  await renderLoadedSidebar();

  const lists = [
    sectionList("Tools"),
    sectionList("Pinned Tabs"),
    sectionList("Tabs"),
  ];
  // Three distinct elements, each the list of one section. One shared container
  // around all three would let a long Tabs list carry the others out of view,
  // which is the failure the ticket names.
  expect(new Set(lists).size).toBe(3);
  for (const list of lists) {
    expect(list.tagName).toBe("UL");
    expect(list.className).toContain("app-sidebar-list");
    // No list contains another: nesting them would make one scroll the other.
    for (const other of lists) {
      if (other !== list) expect(list.contains(other)).toBe(false);
    }
  }

  // The rule that makes them scroll. Not observable in jsdom — read from source.
  const list = declaredFor(".app-sidebar-list");
  expect(list["overflow-y"]).toBe("auto");
  // Without this a flex item's minimum is its content, so the list never
  // shrinks, the panel overflows instead, and the footer leaves the rail.
  expect(list["min-height"]).toBe("0");
});

test("the panel around the sections does not scroll", async () => {
  await renderLoadedSidebar();

  // A scroll window around all three lists *and* the footer is the arrangement
  // this replaces: it scrolls the footer away exactly when the lists are long.
  const declared = declaredFor(".app-sidebar-panel");
  expect(declared.overflow).toBe("hidden");
  expect(declared["overflow-y"]).toBeUndefined();
  expect(panel().className).toContain("app-sidebar-panel");
});

// AC2 — the footer stays reachable however long the lists are.

test("the footer controls sit outside every scrolling list", async () => {
  await renderLoadedSidebar();

  const footerNames = ["Pin tab", "New tab", "Settings"];
  for (const name of footerNames) {
    const control = screen.getByRole("button", { name });
    expect(control.closest(".app-sidebar-list")).toBeNull();
    expect(control.closest(".app-sidebar-section")).toBeNull();
  }
  // Baxter is not a control, but it is footer chrome and belongs with them.
  expect(screen.getByText("Baxter").closest(".app-sidebar-list")).toBeNull();
});

test("a rail full of tabs still draws its footer", async () => {
  // The fixture is deliberately longer than any rail: before this ticket the
  // footer was rendered *after* the lists in one overflowing column, so this is
  // the arrangement that used to carry it off the bottom.
  await renderLoadedSidebar();

  expect(within(sectionList("Tabs")).getAllByRole("listitem")).toHaveLength(TAB_COUNT);
  expect(screen.getByRole("button", { name: "Pin tab" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New tab" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
});

// AC3 — the height is shared, rather than split or reserved.

test("a section takes the height its own list needs, and gives it back under pressure", () => {
  const section = declaredFor(".app-sidebar-section");

  // `0 1 auto`: no growth, so a short list reserves nothing it is not using;
  // shrink 1 from an `auto` basis, so when the three together do not fit each
  // gives up space in proportion to what it was taking — the longest list gives
  // up the most rather than starving the others.
  expect(section.flex).toBe("0 1 auto");
  expect(section["min-height"]).toBe("0");
  // A fixed height or a max-height would reserve space a short list is not
  // using, and cap a long one below the room actually available.
  expect(section.height).toBeUndefined();
  expect(section["max-height"]).toBeUndefined();
});

// AC4 — the scroll affordance is visible on the collapsed rail, without hover.

test("the lists declare a scrollbar that does not wait for hover", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "..", "AppSidebar.css"), "utf8");

  // macOS hides overlay scrollbars until something scrolls, and a 60px column of
  // icons offers no other clue that there is more below. Styling the scrollbar
  // opts the list out of overlay behaviour, so the thumb shows whenever the list
  // overflows. Both spellings, because the app ships in a WebKit shell as well.
  const list = declaredFor(".app-sidebar-list");
  expect(list["scrollbar-width"]).toBe("thin");
  expect(list["scrollbar-color"]).toBeTruthy();
  expect(source).toContain(".app-sidebar-list::-webkit-scrollbar-thumb");

  // A rule reached only on hover would be no affordance at all for a user who
  // has not already put a pointer on the rail.
  for (const rule of sidebarCss()) {
    if (rule.selector.includes("scrollbar")) expect(rule.selector).not.toContain(":hover");
  }
});

test("both states use the same lists, so scrolling is not an expanded-only affordance", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  const collapsed = sectionList("Tabs");
  expect(nav).toHaveAttribute("data-expanded", "false");

  await user.hover(nav);

  expect(nav).toHaveAttribute("data-expanded", "true");
  // The same element, with the same classes: nothing about the scroll container
  // is conditional on expansion, so there is no state in which it is missing.
  expect(sectionList("Tabs")).toBe(collapsed);
  expect(collapsed.className).toContain("app-sidebar-list");
});

// AC5 (regression, 434) — expansion still overlays rather than pushing, and
// scrolling does not disturb the rail's own box either.

test("the rail's own box is unchanged by expansion and by a section scrolling", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  const classes = nav.className;
  await user.hover(nav);
  sectionList("Tabs").dispatchEvent(new Event("scroll", { bubbles: true }));
  await user.unhover(nav);

  // jsdom measures nothing, so this is the structural cause rather than a
  // width: the rail's class list never changes and it never takes an inline
  // width, so nothing beside it can be pushed. The 60px box and the panel's
  // `position: absolute` are the other half, and they live in CSS.
  expect(nav.className).toBe(classes);
  expect(nav.getAttribute("style")).toBeNull();
  const rail = declaredFor(".app-sidebar");
  expect(rail.width).toBe("60px");
  expect(rail.flex).toBe("none");
  expect(declaredFor(".app-sidebar-panel").position).toBe("absolute");
});

test("the active-route marker is inside the list that now clips its overflow", async () => {
  await renderLoadedSidebar("/view/v-tab-3");

  // The marker is drawn outside its row, in the rail's 8px gutter. Turning the
  // list into a scroll container turns that gutter into clipped overflow unless
  // the list carries the gutter itself — and the failure is silent: the rail
  // keeps working and simply stops saying which entry the app is on.
  const marker = screen
    .getByRole("link", { name: "Tab 3" })
    .querySelector(".app-sidebar-bar");
  expect(marker).not.toBeNull();

  const inset = Number.parseInt(declaredFor(".app-sidebar-bar").left, 10);
  const gutter = Number.parseInt(declaredFor(".app-sidebar-list")["padding"].split(" ")[1], 10);
  expect(inset).toBeLessThan(0);
  expect(gutter).toBeGreaterThanOrEqual(Math.abs(inset));
});

// AC6 — names stay in the accessibility tree, and no tab stop is added.

test("a scrolling list adds no tab stop and traps no focus", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);

  // A scroll container with focusable children is not itself focusable, and
  // nothing here makes one focusable by hand.
  for (const heading of ["Tools", "Pinned Tabs", "Tabs"]) {
    expect(sectionList(heading)).not.toHaveAttribute("tabindex");
  }

  const stops: HTMLElement[] = [];
  for (let press = 0; press < 80; press += 1) {
    await user.tab();
    const active = document.activeElement as HTMLElement;
    if (!nav.contains(active) || stops.includes(active)) break;
    stops.push(active);
  }

  // Seven Tools rows, two pinned tabs, every unpinned tab, then the footer's
  // three controls — one stop per entry, and not one belonging to a list.
  expect(stops).toHaveLength(7 + 2 + TAB_COUNT + 3);
  expect(stops.some((stop) => stop.tagName === "UL")).toBe(false);
  // Nothing holds focus: the sequence ran to the end and came back round to the
  // first stop, rather than stalling on a row inside a scroll container. It
  // wraps rather than leaving because the rail is the only thing rendered here.
  await user.tab();
  expect(document.activeElement).toBe(stops[0]);
});

test("entry names inside a scrolling list are still text in the row", async () => {
  const { nav } = await renderLoadedSidebar();

  // The clip that hides them while collapsed is CSS on the row's own span. A
  // scroll container that hid its content with `overflow: hidden` and a
  // `title` tooltip instead would satisfy every accessible-name query and be
  // unreadable to a screen reader — that is what 434 replaced.
  expect(nav).toHaveAttribute("data-expanded", "false");
  const row = screen.getByRole("link", { name: "Tab 3" });
  expect(row).not.toHaveAttribute("title");
  expect(row.textContent).toContain("Tab 3");
  expect(row.closest(".app-sidebar-list")).toBe(sectionList("Tabs"));
});

// AC7 — the active entry is scrolled into view on load.

test("the entry for the current route is scrolled into its section's window", async () => {
  // jsdom implements no scrolling at all, so `scrollIntoView` does not exist on
  // an element and has to be supplied. What is checked is that the sidebar asks
  // for the *marked* entry to be revealed, and asks with `nearest` so an entry
  // already in view does not jump. Whether the browser then scrolls far enough
  // is a browser check.
  const scrollIntoView = jest.fn();
  Element.prototype.scrollIntoView = scrollIntoView;

  await renderLoadedSidebar("/view/v-tab-20");

  const active = await screen.findByRole("link", { name: "Tab 20" });
  expect(active).toHaveAttribute("aria-current", "page");
  expect(scrollIntoView.mock.instances).toContain(active);
  expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
});
