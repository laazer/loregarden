/**
 * The hover-expanding sidebar that replaces the fixed icon rail (ticket 434).
 *
 * Written before the component exists, so every failure here is currently a
 * missing module. The contract these tests pin:
 *
 *   - `client/src/components/AppSidebar.tsx` exports `AppSidebar`, taking an
 *     already-resolved concrete workspace slug (`uiStore.workspace` defaults to
 *     `"all"`, which 404s against every view route, so the resolution stays in
 *     the caller — see decision 1 on the ticket).
 *   - `client/src/lib/viewsApi.ts` wraps the 433 REST surface, following the
 *     `lib/branchTriageApi.ts` / `lib/queueLanesApi.ts` convention.
 *
 * Two limits worth stating plainly rather than dressing up:
 *
 *   1. jsdom has no layout engine — every element measures 0px — so "expansion
 *      does not reflow the screen area" cannot be checked in pixels here. What
 *      is checkable is the structural contract that makes the reflow
 *      impossible: expansion state is carried on a `data-expanded` attribute,
 *      the rail's own box keeps an unchanged class list and no inline width,
 *      and the revealed names live inside the rail's own subtree rather than
 *      being inserted anywhere a sibling could be pushed by them. A naive
 *      implementation that widens the rail by swapping its class or setting a
 *      width fails these. A CSS regression that drops `position: absolute` from
 *      the overlay does not — that one needs a browser, and is recorded as a
 *      gap.
 *   2. Whether a name is *visually* hidden while collapsed is also CSS, and
 *      class-based hiding is invisible to jsdom. So the collapsed state is
 *      asserted on the accessible name (which must resolve in both states) and
 *      the expanded state on `data-expanded`.
 *
 *      An accessible name alone is *not* enough to pin this ticket, though: a
 *      bare `title` attribute resolves one in dom-accessibility-api, so the
 *      tooltip-only rail this ticket replaces satisfies every name-based query
 *      unchanged. The names therefore also have to exist as text nodes in the
 *      row, in both states — present for assistive tech while collapsed,
 *      legible once CSS reveals them. That is the assertion a tooltip cannot
 *      pass, and `{expanded && <span>{name}</span>}` cannot either.
 *
 * NOTE for the implement stage: `AppLayoutChrome.test.tsx:10-11` `jest.mock`s
 * `../AppIconRail` by path. When `AppLayout` swaps in `AppSidebar`, that mock
 * stops intercepting and the real sidebar renders inside those tests against an
 * api mock that knows nothing about views. Add the `../AppSidebar` mock there in
 * the same change.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "../../api/http";
import { createQueryClient } from "../../api/queryClient";
import { useToastStore } from "../../state/toastStore";
import { AppSidebar } from "../AppSidebar";
import {
  deleteView,
  fetchSidebarEntries,
  fetchViews,
  pinPage,
  reorderSidebarEntries,
  unpinEntry,
  updateView,
  type SidebarEntry,
  type ViewSummary,
} from "../../lib/viewsApi";

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

const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;
const mockPinPage = pinPage as jest.MockedFunction<typeof pinPage>;
const mockUnpinEntry = unpinEntry as jest.MockedFunction<typeof unpinEntry>;
const mockReorder = reorderSidebarEntries as jest.MockedFunction<typeof reorderSidebarEntries>;
const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;
const mockDeleteView = deleteView as jest.MockedFunction<typeof deleteView>;

const SLUG = "loregarden";

/**
 * Positions are deliberately non-contiguous: the server ranks relatively, and a
 * delete overlapping an append leaves gaps. Anything deriving an index from a
 * count breaks on this fixture.
 */
function pageEntry(id: string, position: number, pageKey: string): SidebarEntry {
  return { id, position, entry_kind: "page", page_key: pageKey, view_id: "" };
}

function viewEntry(id: string, position: number, viewId: string): SidebarEntry {
  return { id, position, entry_kind: "view", page_key: "", view_id: viewId };
}

const ENTRIES: SidebarEntry[] = [
  pageEntry("e-home", 10, "home"),
  pageEntry("e-queue", 30, "queue"),
  viewEntry("e-grid", 45, "v-grid"),
  viewEntry("e-canvas", 90, "v-canvas"),
];

const VIEWS: ViewSummary[] = [
  {
    id: "v-grid",
    kind: "flex_grid",
    title: "Build Board",
    icon: "",
    layout: { kind: "flex_grid", root: null },
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  },
  {
    id: "v-canvas",
    kind: "canvas",
    title: "Sketch Surface",
    icon: "",
    layout: { kind: "canvas", containers: [] },
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  },
];

/**
 * `client` is for the error-path tests, which need the app's real
 * `MutationCache.onError` rule to observe what a failure reports. `retryDelay`
 * is zeroed on the default client so that a mutation opting into a retry does
 * not spend react-query's ~1s backoff inside a `waitFor`.
 */
function renderSidebar(path = "/", client?: QueryClient) {
  const qc =
    client ??
    new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
    });
  const onOpenSettings = jest.fn();
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AppSidebar workspaceSlug={SLUG} onOpenSettings={onOpenSettings} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, onOpenSettings };
}

/** The sidebar, once its first entry has arrived. */
async function renderLoadedSidebar(path = "/", client?: QueryClient) {
  const utils = renderSidebar(path, client);
  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  await screen.findByRole("link", { name: "Home" });
  return { ...utils, nav };
}

/** The list item carrying the entry whose link has this accessible name. */
function entryRow(name: string): HTMLElement {
  const row = screen.getByRole("link", { name }).closest("li");
  if (!row) throw new Error(`No list row for entry "${name}"`);
  return row;
}

/** The given entry names, re-sorted into the order their links appear in the DOM. */
function inRenderedOrder(names: string[]): string[] {
  return [...names].sort((a, b) => {
    const left = screen.getByRole("link", { name: a });
    const right = screen.getByRole("link", { name: b });
    const following = left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING;
    return following === 0 ? 1 : -1;
  });
}

/**
 * Let every already-scheduled promise settle. Used by the "and then nothing
 * else happens" half of the negative assertions — without it they pass simply
 * because the extra call had not been issued yet.
 */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  mockFetchEntries.mockResolvedValue(ENTRIES);
  mockFetchViews.mockResolvedValue(VIEWS);
  mockPinPage.mockImplementation(async (_slug: string, pageKey: string) =>
    pageEntry(`e-${pageKey}`, 100, pageKey),
  );
  mockUnpinEntry.mockImplementation(async (_slug: string, entryId: string) => ({
    deleted: entryId,
  }));
  mockReorder.mockResolvedValue(ENTRIES);
  mockUpdateView.mockImplementation(async (_slug: string, viewId: string) => ({
    ...VIEWS[0],
    id: viewId,
  }));
  mockDeleteView.mockImplementation(async (_slug: string, viewId: string) => ({
    deleted: viewId,
  }));
});

// AC1 — hover expands, leaving the screen area's width and layout unchanged.

test("hovering expands the sidebar and leaving collapses it back", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  expect(nav).toHaveAttribute("data-expanded", "false");

  await user.hover(nav);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "true"));

  await user.unhover(nav);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "false"));
});

test("expanding changes nothing about the rail's own layout box", async () => {
  // The invariant behind "must not reflow the screen area": the element
  // `AppLayout` lays out beside `.app-main` is sized by CSS from a class list
  // that expansion does not touch, and expansion sets no inline width. jsdom
  // cannot measure the result — see the file header — so this asserts the
  // structural cause rather than the pixel effect.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  const collapsedClasses = nav.className;
  const collapsedInlineStyle = nav.getAttribute("style") ?? "";

  await user.hover(nav);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "true"));

  expect(nav.className).toBe(collapsedClasses);
  expect(nav.getAttribute("style") ?? "").toBe(collapsedInlineStyle);
  expect(nav.style.width).toBe("");
});

test("the names revealed by expansion stay inside the rail's own subtree", async () => {
  // Nothing is portaled next to the screen area, so no expanded content can
  // ever occupy a box that pushes it.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  const bodyChildrenBefore = document.body.childElementCount;

  await user.hover(nav);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "true"));

  for (const name of ["Home", "Parallel Execution", "Build Board", "Sketch Surface"]) {
    expect(nav).toContainElement(screen.getByRole("link", { name }));
  }
  // A React portal lands as a new direct child of `document.body`, which is
  // exactly where an expanded panel would sit beside the screen area rather
  // than over the rail.
  expect(document.body.childElementCount).toBe(bodyChildrenBefore);
  expect(screen.getByText("Pinned Tabs")).toBeInTheDocument();
  expect(nav).toContainElement(screen.getByText("Pinned Tabs"));
  expect(nav).toContainElement(screen.getByText("Tabs"));
});

// AC2 — keyboard focus expands the same way, and every entry is reachable.

test("keyboard focus into the sidebar expands it, and blurring away collapses it", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.tab();
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "true"));
  expect(nav.contains(document.activeElement)).toBe(true);

  // Focus leaving the rail entirely puts it back — an expansion that only
  // hover can undo strands keyboard users in the expanded state.
  await user.click(document.body);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "false"));
});

test("every entry is reachable by tabbing, without a pointer", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  const expected = [
    screen.getByRole("link", { name: "Home" }),
    screen.getByRole("link", { name: "Parallel Execution" }),
    screen.getByRole("link", { name: "Build Board" }),
    screen.getByRole("link", { name: "Sketch Surface" }),
    screen.getByRole("button", { name: "Settings" }),
  ];

  // The budget is a tab count, not a "have we collected N distinct elements"
  // count: every row also carries its own controls, so stopping once the set
  // reaches `expected.length` stops inside the first row.
  const reached = new Set<Element>();
  for (let i = 0; i < 60; i += 1) {
    await user.tab();
    const active = document.activeElement;
    if (!active || active === document.body) break;
    reached.add(active);
    if (expected.every((element) => reached.has(element))) break;
  }

  for (const element of expected) {
    expect(reached).toContain(element);
  }
  // Tabbing is what expanded it; nothing here ever hovered.
  expect(nav).toHaveAttribute("data-expanded", "true");
});

test("a row costs one tab stop, and its controls are reached with the arrow keys", async () => {
  // Every row carries move, unpin/rename and delete buttons. Leaving all of them
  // in the tab sequence costs five stops per entry where the rail this replaces
  // cost one, which is a regression in the thing this ticket is about.
  const user = userEvent.setup();
  await renderLoadedSidebar();

  const home = screen.getByRole("link", { name: "Home" });
  await user.tab();
  expect(document.activeElement).toBe(home);
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("link", { name: "Parallel Execution" }));

  home.focus();
  await user.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(
    within(entryRow("Home")).getByRole("button", { name: /move .*down/i }),
  );
  await user.keyboard("{ArrowLeft}");
  expect(document.activeElement).toBe(home);
});

// AC3 — names exposed to assistive technology in both states.

test("entry names resolve as accessible names while collapsed", async () => {
  const { nav } = await renderLoadedSidebar();

  expect(nav).toHaveAttribute("data-expanded", "false");
  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Parallel Execution" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Build Board" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
});

test("the same names are still exposed once expanded", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "true"));

  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Parallel Execution" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Build Board" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
});

test("entry names are text in the row, not a tooltip, in both states", async () => {
  // The rail this replaces already passes every name-based query above: a bare
  // `title` resolves an accessible name. What it cannot do is put the name in
  // the row as text for expansion to reveal — so that is asserted directly, and
  // in the collapsed state too, since conditional rendering would drop the name
  // out of the accessibility tree the moment the pointer leaves.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  const names = ["Home", "Parallel Execution", "Build Board", "Sketch Surface"];

  expect(nav).toHaveAttribute("data-expanded", "false");
  for (const name of names) {
    expect(within(entryRow(name)).getByText(name)).toBeInTheDocument();
  }

  await user.hover(nav);
  await waitFor(() => expect(nav).toHaveAttribute("data-expanded", "true"));

  for (const name of names) {
    expect(within(entryRow(name)).getByText(name)).toBeInTheDocument();
  }
  expect(within(nav).getByText("Settings")).toBeInTheDocument();
});

// AC4 — two labelled sections, sourced from the API.

test("renders Pinned Tabs and Tabs as distinct labelled lists", async () => {
  await renderLoadedSidebar();

  const pinned = await screen.findByRole("list", { name: "Pinned Tabs" });
  const tabs = screen.getByRole("list", { name: "Tabs" });

  expect(within(pinned).getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(within(pinned).getByRole("link", { name: "Parallel Execution" })).toBeInTheDocument();
  expect(within(tabs).getByRole("link", { name: "Build Board" })).toBeInTheDocument();
  expect(within(tabs).getByRole("link", { name: "Sketch Surface" })).toBeInTheDocument();
});

test("the sections show what the API returned, not the old hardcoded set", async () => {
  await renderLoadedSidebar();

  expect(mockFetchEntries).toHaveBeenCalledWith(SLUG);
  // Two pages are pinned in the fixture; the other five built-ins are not, and
  // a component still rendering its own array would show them anyway.
  for (const absent of ["Chat", "Console", "Studios", "MCP Gateway", "Branch Triage"]) {
    expect(screen.queryByRole("link", { name: absent })).not.toBeInTheDocument();
  }
});

test("entries render in the server's order, not sorted by title or id", async () => {
  // The server returns them ranked; positions are relative and non-contiguous,
  // so any client-side re-sort is a bug that this fixture — reverse-alphabetical
  // within each section, and reverse-position too — makes visible.
  mockFetchEntries.mockResolvedValue([
    pageEntry("e-queue", 30, "queue"),
    pageEntry("e-home", 45, "home"),
    viewEntry("e-canvas", 60, "v-canvas"),
    viewEntry("e-grid", 90, "v-grid"),
  ]);
  await renderLoadedSidebar();

  expect(inRenderedOrder(["Home", "Parallel Execution"])).toEqual([
    "Parallel Execution",
    "Home",
  ]);
  expect(inRenderedOrder(["Build Board", "Sketch Surface"])).toEqual([
    "Sketch Surface",
    "Build Board",
  ]);
});

test("an unresolved workspace slug issues no request", async () => {
  // `uiStore.workspace` is `"all"` until resolved, and every view route 404s on
  // it. The caller resolves; the sidebar must not guess.
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={["/"]}>
        <AppSidebar workspaceSlug="" onOpenSettings={jest.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(screen.getByRole("navigation")).toBeInTheDocument());
  expect(mockFetchEntries).not.toHaveBeenCalled();
  expect(mockFetchViews).not.toHaveBeenCalled();
});

// AC5 — pin, unpin and reorder persist through the API.

test("pinning a built-in page posts that page key", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(await screen.findByRole("button", { name: /pin a page/i }));
  await user.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "Chat" }));

  await waitFor(() => expect(mockPinPage).toHaveBeenCalledWith(SLUG, "chat"));
});

test("Escape and a click outside dismiss the pin menu", async () => {
  // It declares `role="menu"`, which promises a way out that is not its own
  // trigger.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  const openMenu = async () => {
    await user.click(await screen.findByRole("button", { name: /pin a page/i }));
    return screen.getByRole("menu");
  };

  await user.hover(nav);
  await openMenu();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();

  await openMenu();
  await user.click(document.body);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

test("a sidebar told not to seed writes nothing into the workspace it is reading", async () => {
  // The chrome falls back to a workspace when the user has chosen none. Reading
  // one is harmless; pinning seven pages into it is not.
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={["/"]}>
        <AppSidebar workspaceSlug={SLUG} seedDefaults={false} onOpenSettings={jest.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(mockFetchEntries).toHaveBeenCalledWith(SLUG));
  await settle();
  expect(mockPinPage).not.toHaveBeenCalled();
});

test("only unpinned pages are offered for pinning", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(await screen.findByRole("button", { name: /pin a page/i }));

  const menu = screen.getByRole("menu");
  expect(within(menu).getByRole("menuitem", { name: "Chat" })).toBeInTheDocument();
  expect(within(menu).queryByRole("menuitem", { name: "Home" })).not.toBeInTheDocument();
  expect(
    within(menu).queryByRole("menuitem", { name: "Parallel Execution" }),
  ).not.toBeInTheDocument();
});

test("unpinning a page deletes that entry and re-reads the order", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  const before = mockFetchEntries.mock.calls.length;
  await user.click(within(entryRow("Home")).getByRole("button", { name: /unpin/i }));

  await waitFor(() => expect(mockUnpinEntry).toHaveBeenCalledWith(SLUG, "e-home"));
  // Positions are relative and the server closes the gap itself, so the client
  // re-reads rather than mutating its own copy.
  await waitFor(() => expect(mockFetchEntries.mock.calls.length).toBeGreaterThan(before));
});

test("reordering sends the complete permutation across both sections", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Home")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalled());
  // A partial list, a repeat, or a missing id is a 400 with no partial write —
  // views and pinned pages share one ranking, so both sections go in every time.
  expect(mockReorder).toHaveBeenCalledWith(SLUG, ["e-queue", "e-home", "e-grid", "e-canvas"]);
});

test("a move swaps the neighbour in its own section, and still sends every id", async () => {
  // The ranking interleaves the sections, so the entry above `Parallel
  // Execution` in the full list is a *view*. Swapping with that one sends a real
  // PATCH whose only visible effect is in the other section: the pinned list the
  // user was looking at comes back byte-identical. The move has to swap with the
  // neighbour the user can see — the previous entry of the same kind — while the
  // body still names every entry, because the two sections share one ranking.
  mockFetchEntries.mockResolvedValue([
    pageEntry("e-home", 10, "home"),
    viewEntry("e-grid", 20, "v-grid"),
    pageEntry("e-queue", 30, "queue"),
    viewEntry("e-canvas", 40, "v-canvas"),
  ]);
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(
    within(entryRow("Parallel Execution")).getByRole("button", { name: /move .*up/i }),
  );

  await waitFor(() => expect(mockReorder).toHaveBeenCalled());
  const [, entryIds] = mockReorder.mock.calls[0];
  expect([...entryIds].sort()).toEqual(["e-canvas", "e-grid", "e-home", "e-queue"]);
  expect(entryIds).toEqual(["e-queue", "e-grid", "e-home", "e-canvas"]);
});

test("the first entry of a section cannot be moved up past the other section", async () => {
  // `Build Board` is third in the full ranking and first in its section: a
  // move-up derived from the global index would be offered, and would swap it
  // with a pinned page.
  mockFetchEntries.mockResolvedValue([
    pageEntry("e-home", 10, "home"),
    pageEntry("e-queue", 20, "queue"),
    viewEntry("e-grid", 30, "v-grid"),
    viewEntry("e-canvas", 40, "v-canvas"),
  ]);
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);

  expect(within(entryRow("Build Board")).getByRole("button", { name: /move .*up/i })).toBeDisabled();
  expect(
    within(entryRow("Parallel Execution")).getByRole("button", { name: /move .*down/i }),
  ).toBeDisabled();
  expect(
    within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }),
  ).toBeEnabled();
});

test("a reorder re-reads the server's ranking rather than trusting its own", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  const before = mockFetchEntries.mock.calls.length;
  await user.click(within(entryRow("Home")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockFetchEntries.mock.calls.length).toBeGreaterThan(before));
});

// AC6 — an empty workspace seeds the previous hardcoded set, in its old order.

const SEED_ORDER = ["home", "chat", "dashboard", "studio", "queue", "mcp", "branch-triage"];

test("a workspace with no entries seeds the seven default pins in the old order", async () => {
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);
  renderSidebar();

  await waitFor(() => expect(mockPinPage).toHaveBeenCalledTimes(SEED_ORDER.length));
  expect(mockPinPage.mock.calls.map((call) => call[1])).toEqual(SEED_ORDER);
});

test("the seed pins one at a time, because position is assigned when the pin lands", async () => {
  // Firing all seven concurrently ranks them in completion order, which is not
  // the order asked for. Pinning is idempotent and race-safe, so issuing them
  // blind is fine — issuing them in parallel is not.
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);

  let releaseFirst: (() => void) | undefined;
  const firstLanded = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  mockPinPage.mockImplementationOnce(async (_slug: string, pageKey: string) => {
    await firstLanded;
    return pageEntry(`e-${pageKey}`, 10, pageKey);
  });

  renderSidebar();

  await waitFor(() => expect(mockPinPage).toHaveBeenCalledTimes(1));
  await Promise.resolve();
  expect(mockPinPage).toHaveBeenCalledTimes(1);

  releaseFirst?.();
  await waitFor(() => expect(mockPinPage).toHaveBeenCalledTimes(SEED_ORDER.length));
  expect(mockPinPage.mock.calls.map((call) => call[1])).toEqual(SEED_ORDER);
});

test("the seed runs once, even though it refetches an empty list on the way", async () => {
  // The seed's own refetch is the trap: pinning changes the entry list, and an
  // effect keyed on "the list is empty" that has not recorded having run can
  // fire again on the next render pass and pin all seven a second time.
  //
  // Two fixture details are what make that trap reachable at all, and without
  // both this test passes with the once-per-workspace guard deleted:
  //
  //   - a *fresh* `[]` per call. `mockResolvedValue([])` hands back one array
  //     object every time, so there is nothing for a refetch to change.
  //   - `structuralSharing: false`. Given equal bodies react-query keeps the
  //     previous `data` object, which pins `entries` to one identity for the
  //     lifetime of the query — an effect keyed on the list then cannot re-run
  //     no matter how many times the empty refetch lands, and the seeding
  //     effect's own guard is never asked to do anything.
  mockFetchEntries.mockImplementation(async () => []);
  mockFetchViews.mockImplementation(async () => []);
  renderSidebar(
    "/",
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, structuralSharing: false },
        mutations: { retry: false, retryDelay: 0 },
      },
    }),
  );

  // Guard the guard: if the refetch stopped happening, an unguarded effect would
  // have nothing to re-fire on and this test would go quiet again.
  await waitFor(() => expect(mockFetchEntries.mock.calls.length).toBeGreaterThan(1));

  await waitFor(() => expect(mockPinPage).toHaveBeenCalledTimes(SEED_ORDER.length));
  await settle();
  expect(mockPinPage).toHaveBeenCalledTimes(SEED_ORDER.length);
});

test("unpinning the last entry does not re-seed the defaults", async () => {
  // "The list is empty" cannot tell "never set up" from "the user just emptied
  // it", so a guard that re-reads emptiness answers the unpin of the last entry
  // by pinning seven pages back — and does it again on every load of a sidebar
  // somebody deliberately cleared. Only the first read of a workspace can decide
  // this, and that decision has to be latched.
  mockFetchEntries.mockResolvedValueOnce([pageEntry("e-home", 10, "home")]);
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Home")).getByRole("button", { name: /unpin/i }));

  await waitFor(() => expect(mockUnpinEntry).toHaveBeenCalledWith(SLUG, "e-home"));
  await waitFor(() => expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument());
  await settle();
  expect(mockPinPage).not.toHaveBeenCalled();
});

test("a seed that a rejected pin cut short resumes rather than stalling half-done", async () => {
  // The `for … await` loop stops at the first rejection, and the pins that did
  // land make the list non-empty — so a guard that only asks "is it empty" can
  // never finish the job, and the workspace is stuck with three of seven pins.
  // `pinPage` is idempotent, so a resume can simply re-walk the list.
  const landed: string[] = [];
  let rejectedOnce = false;
  mockFetchEntries.mockImplementation(async () =>
    landed.map((pageKey, index) => pageEntry(`e-${pageKey}`, (index + 1) * 10, pageKey)),
  );
  mockFetchViews.mockImplementation(async () => []);
  mockPinPage.mockImplementation(async (_slug: string, pageKey: string) => {
    if (pageKey === "dashboard" && !rejectedOnce) {
      rejectedOnce = true;
      throw new ApiError(503, "pin rejected");
    }
    if (!landed.includes(pageKey)) landed.push(pageKey);
    return pageEntry(`e-${pageKey}`, 10, pageKey);
  });

  renderSidebar("/", appClient());

  await waitFor(() => expect(rejectedOnce).toBe(true));
  await waitFor(() => expect(landed).toEqual(SEED_ORDER));
});

test("a workspace that already has entries is not re-seeded", async () => {
  await renderLoadedSidebar();
  await waitFor(() => expect(mockFetchEntries).toHaveBeenCalled());
  await settle();
  expect(mockPinPage).not.toHaveBeenCalled();
});

// AC7 — view entries carry their kind, and offer rename and delete.

test("view entries show their kind as Grid or Canvas", async () => {
  await renderLoadedSidebar();

  expect(within(entryRow("Build Board")).getByText("Grid")).toBeInTheDocument();
  expect(within(entryRow("Sketch Surface")).getByText("Canvas")).toBeInTheDocument();
  // The wire values are not display strings.
  expect(screen.queryByText("flex_grid")).not.toBeInTheDocument();
});

test("renaming a view patches its title", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /rename/i }));

  const input = within(entryRow("Build Board")).getByRole("textbox");
  await user.clear(input);
  await user.type(input, "Release Board{Enter}");

  await waitFor(() =>
    expect(mockUpdateView).toHaveBeenCalledWith(SLUG, "v-grid", { title: "Release Board" }),
  );
});

test("closing a view tab deletes the view, never its sidebar entry", async () => {
  // Deleting a view's sidebar entry is a 400 server-side — the view would be
  // stored, unranked and unreachable — so view-delete is the only close path.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Sketch Surface")).getByRole("button", { name: /delete/i }));
  // Deleting a view cannot be undone, so it goes through a confirmation (438).
  const confirm = await screen.findByRole("dialog");
  await user.click(within(confirm).getByRole("button", { name: /^delete/i }));

  await waitFor(() => expect(mockDeleteView).toHaveBeenCalledWith(SLUG, "v-canvas"));
  expect(mockUnpinEntry).not.toHaveBeenCalled();
});

test("view entries are not offered an unpin control", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  expect(
    within(entryRow("Sketch Surface")).queryByRole("button", { name: /unpin/i }),
  ).not.toBeInTheDocument();
});

// AC8 — active-route highlighting for built-in pages, extended to views.

test("the built-in page for the current route carries aria-current", async () => {
  await renderLoadedSidebar("/queue");

  expect(screen.getByRole("link", { name: "Parallel Execution" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
});

test("a view route marks its own tab, and no built-in page", async () => {
  // `pageFromPath` falls back to `home` for unknown paths, so a view route
  // lighting up Home is the failure this guards.
  await renderLoadedSidebar("/view/v-grid");

  expect(screen.getByRole("link", { name: "Build Board" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "Sketch Surface" })).not.toHaveAttribute(
    "aria-current",
  );
  expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
});

test("a view route that is not valid percent-encoding still renders the rail", async () => {
  // The active-tab lookup runs during render and above the error boundaries, so
  // a throw on `/view/%` takes the whole shell down rather than matching nothing.
  await renderLoadedSidebar("/view/%");

  expect(screen.getByRole("link", { name: "Build Board" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
});

test("view tabs link to their own route", async () => {
  await renderLoadedSidebar();

  expect(screen.getByRole("link", { name: "Build Board" })).toHaveAttribute(
    "href",
    "/view/v-grid",
  );
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
});

// AC5, failure half — a write that does not land must not look like one that did.
//
// These render against the app's own `createQueryClient`, because
// `MutationCache.onError` is where a failed mutation becomes visible; a bare
// `QueryClient` swallows rejections and every assertion below would be vacuous.
// Retry backoff is zeroed so a mutation that opts into retrying a 409 does not
// spend a second per attempt.

/** The app's client, minus react-query's ~1s retry backoff. */
function appClient(): QueryClient {
  const qc = createQueryClient();
  const defaults = qc.getDefaultOptions();
  qc.setDefaultOptions({
    ...defaults,
    queries: { ...defaults.queries, retry: false },
    mutations: { ...defaults.mutations, retry: false, retryDelay: 0 },
  });
  return qc;
}

test("a failed unpin leaves the entry on screen and reports the failure", async () => {
  mockUnpinEntry.mockRejectedValue(new ApiError(404, "entry is gone"));
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());

  await user.hover(nav);
  await user.click(within(entryRow("Home")).getByRole("button", { name: /unpin/i }));

  await waitFor(() => expect(mockUnpinEntry).toHaveBeenCalled());
  await settle();
  // An optimistic removal with no rollback leaves the user staring at a rail
  // that lost an entry the server still has.
  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(1);
});

test("a reorder that loses a race is retried with the same permutation", async () => {
  // 409 means another window re-ranked first — the request was well formed and
  // is worth re-issuing. Dropping it silently loses the user's drag.
  mockReorder.mockRejectedValueOnce(new ApiError(409, "sidebar entries changed"));
  mockReorder.mockResolvedValue(ENTRIES);

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());

  await user.hover(nav);
  await user.click(within(entryRow("Home")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalledTimes(2));
  expect(mockReorder.mock.calls[1][1]).toEqual(mockReorder.mock.calls[0][1]);
  // The retry succeeded, so nothing failed as far as the user is concerned.
  await settle();
  expect(useToastStore.getState().toasts).toEqual([]);
});

test("a reorder that keeps losing is reported once, not once per attempt", async () => {
  // `api/queryClient.ts` toasts every failed mutation. A retry loop that also
  // reports each attempt itself double-reports — hence `meta.suppressErrorToast`
  // on whichever path is doing its own reporting.
  //
  // Counting the *surviving* toasts cannot see that: `toastStore.push` de-dupes
  // on (tone, title, message), so the cache's report and the hook's own report
  // of the same error collapse into one row and a length check reads 1 either
  // way. What distinguishes one report from two is how many pushes happened, so
  // that is what is counted — every toast id the store ever held.
  mockReorder.mockRejectedValue(new ApiError(409, "sidebar entries changed"));

  const reported: string[] = [];
  const unsubscribe = useToastStore.subscribe((state) => {
    for (const toast of state.toasts) {
      if (!reported.includes(toast.id)) reported.push(toast.id);
    }
  });

  try {
    const user = userEvent.setup();
    const { nav } = await renderLoadedSidebar("/", appClient());

    await user.hover(nav);
    await user.click(within(entryRow("Home")).getByRole("button", { name: /move .*down/i }));

    await waitFor(() => expect(mockReorder.mock.calls.length).toBeGreaterThan(1));
    await settle();

    expect(reported).toHaveLength(1);
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0]).toMatchObject({ tone: "error" });
  } finally {
    unsubscribe();
  }
});

test("a reorder that lost to a peer's pin re-reads before it retries", async () => {
  // The server checks membership before it ranks, and answers a *changed* id set
  // with the same 409. Re-sending the identical body then fails that check as a
  // 400 — a downgrade that reports the user's own request as malformed. So the
  // retry re-reads and re-ranks: their relative order survives, and the entry
  // the peer added comes along.
  mockReorder.mockRejectedValueOnce(new ApiError(409, "sidebar entries changed"));
  mockReorder.mockResolvedValue(ENTRIES);

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());
  mockFetchEntries.mockResolvedValue([...ENTRIES, pageEntry("e-chat", 120, "chat")]);

  await user.hover(nav);
  await user.click(within(entryRow("Home")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalledTimes(2));
  expect(mockReorder.mock.calls[0][1]).toEqual(["e-queue", "e-home", "e-grid", "e-canvas"]);
  expect(mockReorder.mock.calls[1][1]).toEqual([
    "e-queue",
    "e-home",
    "e-grid",
    "e-canvas",
    "e-chat",
  ]);
  await settle();
  expect(useToastStore.getState().toasts).toEqual([]);
});

test("a rejected reorder is not retried, and the server's order stays on screen", async () => {
  // 400 is "fix the request" — a repeat sends the same rejected body. The write
  // was refused whole, so the displayed order is still the server's.
  mockReorder.mockRejectedValue(new ApiError(400, "entry_ids must be a full permutation"));

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());

  await user.hover(nav);
  await user.click(within(entryRow("Home")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalledTimes(1));
  await settle();
  expect(mockReorder).toHaveBeenCalledTimes(1);
  expect(inRenderedOrder(["Home", "Parallel Execution"])).toEqual(["Home", "Parallel Execution"]);
  expect(useToastStore.getState().toasts).toHaveLength(1);
});
