/**
 * The hover-expanding sidebar: Tools, Pinned Tabs, Tabs (tickets 434 and 472).
 *
 * 434's contract still holds and most of it is retested here as regression,
 * because 472 rebuilds the section structure underneath it and the first thing
 * that breaks is the behaviour nobody re-checked.
 *
 * What 472 changes, and what these tests are mainly about:
 *
 *   - **Tools is derived, not stored.** The seven built-in pages come from
 *     `appSidebarPages.tsx`. No read can come back short, no write is needed to
 *     populate it, and no control removes an entry from it. The tests that
 *     matter most therefore assert what happens when the *store* is empty,
 *     unhelpful, or failing outright, and find all seven pages anyway.
 *   - **Pinned Tabs holds pinned views.** Both tab sections are `entry_kind:
 *     "view"`; `pinned` is what separates them, and they share one ranking.
 *   - **Nothing seeds.** The `SeedState` latch, its attempt limit and its
 *     resume path are gone with the thing they populated.
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
 *      An accessible name alone is *not* enough, though: a bare `title`
 *      attribute resolves one in dom-accessibility-api, so the tooltip-only
 *      rail 434 replaced satisfies every name-based query unchanged. The names
 *      therefore also have to exist as text nodes in the row, in both states.
 *      That is the assertion a tooltip cannot pass, and
 *      `{expanded && <span>{name}</span>}` cannot either.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
  reorderSidebarEntries,
  setEntryPinned,
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
  setEntryPinned: jest.fn(),
  reorderSidebarEntries: jest.fn(),
  updateView: jest.fn(),
  deleteView: jest.fn(),
}));

const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;
const mockSetPinned = setEntryPinned as jest.MockedFunction<typeof setEntryPinned>;
const mockReorder = reorderSidebarEntries as jest.MockedFunction<typeof reorderSidebarEntries>;
const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;
const mockDeleteView = deleteView as jest.MockedFunction<typeof deleteView>;

const SLUG = "loregarden";

/** The seven built-in pages, in the order the fixed rail drew them. */
const TOOL_LABELS = [
  "Home",
  "Chat",
  "Console",
  "Studios",
  "Parallel Execution",
  "MCP Gateway",
  "Branch Triage",
];

/**
 * An entry left over from before Tools became static.
 *
 * Every workspace 434 seeded still holds seven of these, so they are in the
 * fixture rather than out of it: the sidebar has to draw none of them, and none
 * of them may be mistaken for an unpinned tab by the section split or by a
 * move.
 */
function pageEntry(id: string, position: number, pageKey: string): SidebarEntry {
  return { id, position, entry_kind: "page", page_key: pageKey, view_id: "", pinned: false };
}

/**
 * Positions are deliberately non-contiguous: the server ranks relatively, and a
 * delete overlapping an append leaves gaps. Anything deriving an index from a
 * count breaks on this fixture.
 */
function viewEntry(
  id: string,
  position: number,
  viewId: string,
  pinned: boolean,
): SidebarEntry {
  return { id, position, entry_kind: "view", page_key: "", view_id: viewId, pinned };
}

const ENTRIES: SidebarEntry[] = [
  pageEntry("e-home", 10, "home"),
  viewEntry("e-grid", 30, "v-grid", true),
  viewEntry("e-notes", 45, "v-notes", true),
  viewEntry("e-canvas", 60, "v-canvas", false),
  viewEntry("e-scratch", 90, "v-scratch", false),
];

function view(id: string, title: string, kind: "flex_grid" | "canvas"): ViewSummary {
  return {
    id,
    kind,
    title,
    icon: "",
    layout: kind === "flex_grid" ? { kind, root: null } : { kind, containers: [] },
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

const VIEWS: ViewSummary[] = [
  view("v-grid", "Build Board", "flex_grid"),
  view("v-notes", "Release Notes", "flex_grid"),
  view("v-canvas", "Sketch Surface", "canvas"),
  view("v-scratch", "Scratch Pad", "canvas"),
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

/** The sidebar, once its first *stored* entry has arrived. */
async function renderLoadedSidebar(path = "/", client?: QueryClient) {
  const utils = renderSidebar(path, client);
  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  // A Tools link is there before any read lands, so it cannot be the signal;
  // wait on a row that only the store can produce.
  await screen.findByRole("link", { name: "Build Board" });
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

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  mockFetchEntries.mockResolvedValue(ENTRIES);
  mockFetchViews.mockResolvedValue(VIEWS);
  mockSetPinned.mockImplementation(async (_slug: string, entryId: string, pinned: boolean) => ({
    ...(ENTRIES.find((entry) => entry.id === entryId) ?? ENTRIES[1]),
    pinned,
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

// 434 AC1 (regression) — hover expands, leaving the screen area's width and
// layout unchanged.

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
  // 472 AC8. The invariant behind "must not reflow the screen area": the
  // element `AppLayout` lays out beside `.app-main` is sized by CSS from a
  // class list that expansion does not touch, and expansion sets no inline
  // width. jsdom cannot measure the result — see the file header — so this
  // asserts the structural cause rather than the pixel effect.
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
  // 472 AC8. Nothing is portaled next to the screen area, so no expanded
  // content can ever occupy a box that pushes it. A third section does not
  // change that, and this is where it would show if it did.
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
  for (const heading of ["Tools", "Pinned Tabs", "Tabs"]) {
    expect(nav).toContainElement(screen.getByText(heading, { exact: true }));
  }
});

// 434 AC2 (regression) — keyboard focus expands the same way, and every entry
// is reachable.

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
    screen.getByRole("link", { name: "Branch Triage" }),
    screen.getByRole("link", { name: "Build Board" }),
    screen.getByRole("link", { name: "Sketch Surface" }),
    screen.getByRole("button", { name: "Settings" }),
  ];

  // The budget is a tab count, not a "have we collected N distinct elements"
  // count: every view row also carries its own controls, so stopping once the
  // set reaches `expected.length` stops inside the first row.
  const reached = new Set<Element>();
  for (let i = 0; i < 80; i += 1) {
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

test("a view row costs one tab stop, and its controls are reached with the arrow keys", async () => {
  // 472 AC8. Every view row carries move, pin, rename, duplicate and delete
  // buttons. Leaving all of them in the tab sequence costs six stops per entry
  // where the rail this replaces cost one.
  const user = userEvent.setup();
  await renderLoadedSidebar();

  const board = screen.getByRole("link", { name: "Build Board" });
  board.focus();
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("link", { name: "Release Notes" }));

  board.focus();
  await user.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(
    within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }),
  );
  await user.keyboard("{ArrowLeft}");
  expect(document.activeElement).toBe(board);
});

test("a Tools row is one tab stop and carries no controls at all", async () => {
  // 472 AC3: there is no affordance to remove or reorder a built-in page. A row
  // that kept its unpin or move buttons would still be a way to lose one.
  const user = userEvent.setup();
  await renderLoadedSidebar();

  const home = screen.getByRole("link", { name: "Home" });
  expect(within(entryRow("Home")).queryAllByRole("button")).toHaveLength(0);

  home.focus();
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("link", { name: "Chat" }));
});

// 434 AC3 (regression) — names exposed to assistive technology in both states.

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
  // 472 AC8. The rail 434 replaced already passes every name-based query above:
  // a bare `title` resolves an accessible name. What it cannot do is put the
  // name in the row as text for expansion to reveal — so that is asserted
  // directly, and in the collapsed state too, since conditional rendering would
  // drop the name out of the accessibility tree the moment the pointer leaves.
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

// 472 AC1 — three labelled sections in order: Tools, Pinned Tabs, Tabs.

test("renders Tools, Pinned Tabs and Tabs as distinct labelled lists", async () => {
  await renderLoadedSidebar();

  const tools = await screen.findByRole("list", { name: "Tools" });
  const pinned = screen.getByRole("list", { name: "Pinned Tabs" });
  const tabs = screen.getByRole("list", { name: "Tabs" });

  expect(within(tools).getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(within(pinned).getByRole("link", { name: "Build Board" })).toBeInTheDocument();
  expect(within(pinned).getByRole("link", { name: "Release Notes" })).toBeInTheDocument();
  expect(within(tabs).getByRole("link", { name: "Sketch Surface" })).toBeInTheDocument();
  expect(within(tabs).getByRole("link", { name: "Scratch Pad" })).toBeInTheDocument();
});

test("the three sections appear top to bottom in that order", async () => {
  // Document order, which is the structural cause: jsdom has no layout engine,
  // so nothing here can measure that Tools is drawn above the two tab sections.
  // The rail is a plain column — no `order:` and no `column-reverse` in
  // `AppSidebar.css` — so DOM order is what decides what the user sees.
  await renderLoadedSidebar();

  expect(inRenderedOrder(["Build Board", "Home", "Sketch Surface"])).toEqual([
    "Home",
    "Build Board",
    "Sketch Surface",
  ]);
});

// 472 AC2 and AC5 — Tools comes from the static catalog, so it is complete on a
// workspace with no stored entries and cannot drift from the app's routes.

test("all seven built-in pages are in Tools when the store is empty", async () => {
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);
  renderSidebar();

  const tools = await screen.findByRole("list", { name: "Tools" });
  for (const label of TOOL_LABELS) {
    expect(within(tools).getByRole("link", { name: label })).toBeInTheDocument();
  }
  expect(tools.querySelectorAll("li")).toHaveLength(TOOL_LABELS.length);
});

test("Tools does not come from sidebar_entries", async () => {
  // The fixture stores exactly one page entry, `home`. A Tools section read
  // from the store would draw one row; a derived one draws seven regardless.
  await renderLoadedSidebar();

  const tools = screen.getByRole("list", { name: "Tools" });
  expect(tools.querySelectorAll("li")).toHaveLength(TOOL_LABELS.length);
});

test("a leftover page entry is drawn in no section", async () => {
  // 434 seeded seven of these into every workspace and this ticket does not
  // migrate them away. A section split that treated a `page` entry as an
  // unpinned tab would draw a row for it here.
  await renderLoadedSidebar();

  expect(screen.getByRole("list", { name: "Pinned Tabs" }).querySelectorAll("li")).toHaveLength(2);
  expect(screen.getByRole("list", { name: "Tabs" }).querySelectorAll("li")).toHaveLength(2);
});

// 472 AC3 — no built-in page can be unpinned, removed or reordered out of Tools.

test("no Tools row offers a way to remove or move it", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);

  const tools = screen.getByRole("list", { name: "Tools" });
  // Both halves matter: that the section really drew its seven rows, and that
  // none of them carries a control. "No buttons" alone would also pass on a
  // section that rendered nothing.
  expect(tools.querySelectorAll("li")).toHaveLength(TOOL_LABELS.length);
  expect(within(tools).queryAllByRole("button")).toHaveLength(0);
});

test("no request is issued that could remove a built-in page", async () => {
  // AC5's other half: Tools needs no seeding, and nothing writes it either.
  mockFetchEntries.mockResolvedValue([]);
  mockFetchViews.mockResolvedValue([]);
  renderSidebar();

  await screen.findByRole("list", { name: "Tools" });
  await waitFor(() => expect(mockFetchEntries).toHaveBeenCalledWith(SLUG));
  await settle();
  expect(mockSetPinned).not.toHaveBeenCalled();
  expect(mockReorder).not.toHaveBeenCalled();
});

// 472 AC6 — the client-side seven-pin seed is removed, with its latch, its
// attempt limit and its resume path.

test("an empty workspace is left empty, however many times its list is re-read", async () => {
  // The seed's tell was a burst of writes against a workspace whose first read
  // came back empty. A fresh `[]` per call and `structuralSharing: false` are
  // what made the old seeding effect re-fire; keeping them here means a
  // reintroduced seed cannot hide behind a stable `data` identity.
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

  await screen.findByRole("list", { name: "Tools" });
  await settle();
  expect(mockSetPinned).not.toHaveBeenCalled();
  expect(mockReorder).not.toHaveBeenCalled();
  expect(screen.getByRole("list", { name: "Pinned Tabs" }).querySelectorAll("li")).toHaveLength(0);
  expect(screen.getByRole("list", { name: "Tabs" }).querySelectorAll("li")).toHaveLength(0);
});

test("an entries request that fails leaves Tools intact and writes nothing", async () => {
  // The seed's resume path existed to finish a run that a failure cut short.
  // With nothing to seed there is nothing to resume, and a failed read must not
  // provoke a write of any kind.
  mockFetchEntries.mockRejectedValue(new ApiError(503, "sidebar unavailable"));
  renderSidebar("/", appClient());

  const tools = await screen.findByRole("list", { name: "Tools" });
  await settle();
  expect(tools.querySelectorAll("li")).toHaveLength(TOOL_LABELS.length);
  expect(mockSetPinned).not.toHaveBeenCalled();
});

test("an unresolved workspace slug issues no request, and still draws Tools", async () => {
  // `uiStore.workspace` is `"all"` until resolved, and every view route 404s on
  // it. The caller resolves; the sidebar must not guess — but the app's own
  // pages do not depend on a workspace at all, so they are drawn anyway.
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={["/"]}>
        <AppSidebar workspaceSlug="" onOpenSettings={jest.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  const tools = await screen.findByRole("list", { name: "Tools" });
  expect(tools.querySelectorAll("li")).toHaveLength(TOOL_LABELS.length);
  expect(mockFetchEntries).not.toHaveBeenCalled();
  expect(mockFetchViews).not.toHaveBeenCalled();
});

// 472 AC7 — Pinned Tabs holds pinned views, and a view moves between it and Tabs.

test("unpinning a view sends the write that moves it to Tabs", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /^unpin/i }));

  await waitFor(() => expect(mockSetPinned).toHaveBeenCalledWith(SLUG, "e-grid", false));
});

test("pinning a view sends the write that moves it to Pinned Tabs", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Sketch Surface")).getByRole("button", { name: /^pin/i }));

  await waitFor(() => expect(mockSetPinned).toHaveBeenCalledWith(SLUG, "e-canvas", true));
});

test("the control's label says which way it moves the tab", async () => {
  // One toggle for both directions, so the label is the only thing telling a
  // screen-reader user which section the row is currently in.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);

  expect(
    within(entryRow("Build Board")).getByRole("button", { name: "Unpin Build Board" }),
  ).toBeInTheDocument();
  expect(
    within(entryRow("Sketch Surface")).getByRole("button", { name: "Pin Sketch Surface" }),
  ).toBeInTheDocument();
});

test("a pinned view moves section before the server answers", async () => {
  // The tab visibly jumps sections, so the move is optimistic; a round trip of
  // nothing happening reads as the control being broken.
  let release: (() => void) | undefined;
  const landed = new Promise<void>((resolve) => {
    release = resolve;
  });
  mockSetPinned.mockImplementation(async (_slug, entryId, pinned) => {
    await landed;
    return { ...(ENTRIES.find((entry) => entry.id === entryId) ?? ENTRIES[1]), pinned };
  });

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);
  await user.click(within(entryRow("Sketch Surface")).getByRole("button", { name: /^pin/i }));

  await waitFor(() =>
    expect(
      within(screen.getByRole("list", { name: "Pinned Tabs" })).getByRole("link", {
        name: "Sketch Surface",
      }),
    ).toBeInTheDocument(),
  );
  release?.();
});

test("a refused pin puts the tab back and reports the failure", async () => {
  // The server still has it in Tabs; leaving it in Pinned Tabs would report a
  // move that did not happen.
  mockSetPinned.mockRejectedValue(new ApiError(400, "Only a view's tab can be pinned"));
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());

  await user.hover(nav);
  await user.click(within(entryRow("Sketch Surface")).getByRole("button", { name: /^pin/i }));

  await waitFor(() => expect(mockSetPinned).toHaveBeenCalled());
  await settle();
  expect(
    within(screen.getByRole("list", { name: "Tabs" })).getByRole("link", {
      name: "Sketch Surface",
    }),
  ).toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(1);
});

test("a pin re-reads the server's ranking rather than trusting its own", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  const before = mockFetchEntries.mock.calls.length;
  await user.click(within(entryRow("Sketch Surface")).getByRole("button", { name: /^pin/i }));

  await waitFor(() => expect(mockFetchEntries.mock.calls.length).toBeGreaterThan(before));
});

// 434 AC5 (regression) — reordering persists as a whole permutation.

test("entries render in the server's order, not sorted by title or id", async () => {
  // The server returns them ranked; positions are relative and non-contiguous,
  // so any client-side re-sort is a bug that this fixture — reverse-position
  // within each section — makes visible.
  mockFetchEntries.mockResolvedValue([
    viewEntry("e-notes", 30, "v-notes", true),
    viewEntry("e-grid", 45, "v-grid", true),
    viewEntry("e-scratch", 60, "v-scratch", false),
    viewEntry("e-canvas", 90, "v-canvas", false),
  ]);
  await renderLoadedSidebar();

  expect(inRenderedOrder(["Build Board", "Release Notes"])).toEqual([
    "Release Notes",
    "Build Board",
  ]);
  expect(inRenderedOrder(["Sketch Surface", "Scratch Pad"])).toEqual([
    "Scratch Pad",
    "Sketch Surface",
  ]);
});

test("reordering sends the complete permutation across both tab sections", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalled());
  // A partial list, a repeat, or a missing id is a 400 with no partial write —
  // both sections share one ranking, and the leftover page entry is ranked in
  // it too, so every id goes every time.
  expect(mockReorder).toHaveBeenCalledWith(SLUG, [
    "e-home",
    "e-notes",
    "e-grid",
    "e-canvas",
    "e-scratch",
  ]);
});

test("a move swaps the neighbour in its own section, and still sends every id", async () => {
  // The ranking interleaves the sections, so the entry above `Scratch Pad` in
  // the full list is a *pinned* tab. Swapping with that one sends a real PATCH
  // whose only visible effect is in the other section: the list the user was
  // looking at comes back byte-identical. The move has to swap with the
  // neighbour the user can see — the previous entry drawn in the same section —
  // while the body still names every entry.
  mockFetchEntries.mockResolvedValue([
    viewEntry("e-canvas", 10, "v-canvas", false),
    viewEntry("e-grid", 20, "v-grid", true),
    viewEntry("e-scratch", 30, "v-scratch", false),
    viewEntry("e-notes", 40, "v-notes", true),
  ]);
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Scratch Pad")).getByRole("button", { name: /move .*up/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalled());
  const [, entryIds] = mockReorder.mock.calls[0];
  expect([...entryIds].sort()).toEqual(["e-canvas", "e-grid", "e-notes", "e-scratch"]);
  expect(entryIds).toEqual(["e-scratch", "e-grid", "e-canvas", "e-notes"]);
});

test("the first entry of a section cannot be moved up past the other section", async () => {
  // `Sketch Surface` is fourth in the full ranking and first in its section: a
  // move-up derived from the global index would be offered, and would swap it
  // with a pinned tab.
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();
  await user.hover(nav);

  expect(
    within(entryRow("Sketch Surface")).getByRole("button", { name: /move .*up/i }),
  ).toBeDisabled();
  expect(
    within(entryRow("Release Notes")).getByRole("button", { name: /move .*down/i }),
  ).toBeDisabled();
  expect(
    within(entryRow("Sketch Surface")).getByRole("button", { name: /move .*down/i }),
  ).toBeEnabled();
});

// 472 AC7 — pinning is what moves a tab between the sections. A drag is a
// reorder, and both sections now hold view rows, so the pointer path needs the
// same-section rule the arrow buttons have.

/**
 * A drag from one row onto another.
 *
 * jsdom has no drag-and-drop implementation and `userEvent` has no drag API, so
 * this fires the handlers the rows actually carry. What it can prove is the
 * wiring — which handler runs, and what it calls — not the pointer gesture.
 */
function dragRowOnto(source: HTMLElement, target: HTMLElement): { offered: boolean } {
  fireEvent.dragStart(source);
  const over = createEvent.dragOver(target);
  fireEvent(target, over);
  fireEvent.drop(target);
  fireEvent.dragEnd(source);
  // `preventDefault` on dragover is what tells the browser this is a drop
  // target at all; without it the drop is never offered to the user.
  return { offered: over.defaultPrevented };
}

test("dragging a tab onto one in the same section reorders across the whole ranking", async () => {
  await renderLoadedSidebar();

  const { offered } = dragRowOnto(entryRow("Scratch Pad"), entryRow("Sketch Surface"));

  expect(offered).toBe(true);
  await waitFor(() => expect(mockReorder).toHaveBeenCalled());
  expect(mockReorder).toHaveBeenCalledWith(SLUG, [
    "e-home",
    "e-grid",
    "e-notes",
    "e-scratch",
    "e-canvas",
  ]);
});

test("dragging a tab into the other section is refused rather than silently reordering", async () => {
  // Splicing across the sections re-ranks rows in the section the user is not
  // pointing at and leaves the one they are pointing at unchanged — a real
  // PATCH whose only visible effect is somewhere else. The drop is not offered,
  // and nothing is written if one arrives anyway.
  await renderLoadedSidebar();

  const { offered } = dragRowOnto(entryRow("Sketch Surface"), entryRow("Build Board"));

  expect(offered).toBe(false);
  await settle();
  expect(mockReorder).not.toHaveBeenCalled();
  // Nor is it quietly turned into a pin: that is a control of its own.
  expect(mockSetPinned).not.toHaveBeenCalled();
});

test("a leftover page entry is never a move's neighbour", async () => {
  // It is ranked between the two pinned tabs here and is drawn nowhere, so
  // swapping with it would re-rank two rows and change nothing on screen.
  mockFetchEntries.mockResolvedValue([
    viewEntry("e-grid", 10, "v-grid", true),
    pageEntry("e-home", 20, "home"),
    viewEntry("e-notes", 30, "v-notes", true),
  ]);
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalled());
  expect(mockReorder.mock.calls[0][1]).toEqual(["e-notes", "e-home", "e-grid"]);
});

test("a reorder re-reads the server's ranking rather than trusting its own", async () => {
  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar();

  await user.hover(nav);
  const before = mockFetchEntries.mock.calls.length;
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockFetchEntries.mock.calls.length).toBeGreaterThan(before));
});

// 434 AC7 (regression) — view entries carry their kind, and offer rename and
// delete.

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
  await user.click(within(entryRow("Sketch Surface")).getByRole("button", { name: /^delete/i }));
  // Deleting a view cannot be undone, so it goes through a confirmation (438).
  const confirm = await screen.findByRole("dialog");
  await user.click(within(confirm).getByRole("button", { name: /^delete/i }));

  await waitFor(() => expect(mockDeleteView).toHaveBeenCalledWith(SLUG, "v-canvas"));
  // The "never its sidebar entry" half: the entry has one write of its own now,
  // and closing a tab is not it.
  expect(mockSetPinned).not.toHaveBeenCalled();
});

// 472 AC4 — the current route marks its Tools entry, and no entry elsewhere.

test("the built-in page for the current route carries aria-current", async () => {
  await renderLoadedSidebar("/queue");

  expect(screen.getByRole("link", { name: "Parallel Execution" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  for (const name of ["Build Board", "Release Notes", "Sketch Surface", "Scratch Pad"]) {
    expect(screen.getByRole("link", { name })).not.toHaveAttribute("aria-current");
  }
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
  for (const label of TOOL_LABELS) {
    expect(screen.getByRole("link", { name: label })).not.toHaveAttribute("aria-current");
  }
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

// 434 AC5, failure half (regression) — a write that does not land must not look
// like one that did.
//
// These render against the app's own `createQueryClient`, because
// `MutationCache.onError` is where a failed mutation becomes visible; a bare
// `QueryClient` swallows rejections and every assertion below would be vacuous.

test("a reorder that loses a race is retried with the same permutation", async () => {
  // 409 means another window re-ranked first — the request was well formed and
  // is worth re-issuing. Dropping it silently loses the user's drag.
  mockReorder.mockRejectedValueOnce(new ApiError(409, "sidebar entries changed"));
  mockReorder.mockResolvedValue(ENTRIES);

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());

  await user.hover(nav);
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }));

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
    await user.click(
      within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }),
    );

    await waitFor(() => expect(mockReorder.mock.calls.length).toBeGreaterThan(1));
    await settle();

    expect(reported).toHaveLength(1);
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0]).toMatchObject({ tone: "error" });
  } finally {
    unsubscribe();
  }
});

test("a reorder that lost to a peer's new tab re-reads before it retries", async () => {
  // The server checks membership before it ranks, and answers a *changed* id set
  // with the same 409. Re-sending the identical body then fails that check as a
  // 400 — a downgrade that reports the user's own request as malformed. So the
  // retry re-reads and re-ranks: their relative order survives, and the entry
  // the peer added comes along.
  mockReorder.mockRejectedValueOnce(new ApiError(409, "sidebar entries changed"));
  mockReorder.mockResolvedValue(ENTRIES);

  const user = userEvent.setup();
  const { nav } = await renderLoadedSidebar("/", appClient());
  mockFetchEntries.mockResolvedValue([...ENTRIES, viewEntry("e-new", 120, "v-new", false)]);

  await user.hover(nav);
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalledTimes(2));
  expect(mockReorder.mock.calls[0][1]).toEqual([
    "e-home",
    "e-notes",
    "e-grid",
    "e-canvas",
    "e-scratch",
  ]);
  expect(mockReorder.mock.calls[1][1]).toEqual([
    "e-home",
    "e-notes",
    "e-grid",
    "e-canvas",
    "e-scratch",
    "e-new",
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
  await user.click(within(entryRow("Build Board")).getByRole("button", { name: /move .*down/i }));

  await waitFor(() => expect(mockReorder).toHaveBeenCalledTimes(1));
  await settle();
  expect(mockReorder).toHaveBeenCalledTimes(1);
  expect(inRenderedOrder(["Build Board", "Release Notes"])).toEqual([
    "Build Board",
    "Release Notes",
  ]);
  expect(useToastStore.getState().toasts).toHaveLength(1);
});
