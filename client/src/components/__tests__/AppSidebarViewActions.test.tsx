/**
 * The sidebar's write affordances: the New View flow — reached from the footer's
 * New tab control since 473 moved it out of the Tabs section head — and the
 * rename / duplicate / delete trio on a row.
 *
 * `AppSidebar` exists (434), so these failures are a missing button and a
 * missing `createView`, not a missing module. Rename and the active-tab
 * highlight already work; they are written here as regression tests and
 * labelled as such, because this ticket adds a second way to reach the same
 * rows and the first thing a rework of the row controls breaks is the one
 * behaviour nobody re-checked.
 *
 * Three things these tests hold to, each of which a plausible implementation
 * gets wrong:
 *
 *   1. **Create is not optimistic.** The id is server-assigned, and
 *      `AppSidebar` skips any entry whose view is missing from `viewsById` — so
 *      an optimistic entry draws nothing at all, and an optimistic *navigation*
 *      goes to a URL with no view behind it, which is AC4's not-found state
 *      arriving on the happy path. Create waits.
 *   2. **Create invalidates two caches.** The server writes the view and its
 *      sidebar entry in one transaction; the client reads them as two queries.
 *      Refreshing only `["views", slug]` leaves the entry list stale, and the
 *      new tab does not appear until something unrelated refetches.
 *   3. **Duplicate re-POSTs a copy with fresh container ids.** There is no copy
 *      endpoint. Reusing the source's container ids is accepted by the server
 *      and aliases the two views in every cache keyed by container id.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { ApiError } from "../../api/http";
import { createQueryClient } from "../../api/queryClient";
import {
  createView,
  deleteView,
  fetchSidebarEntries,
  fetchViews,
  reorderSidebarEntries,
  updateView,
  type SidebarEntry,
  type ViewSummary,
} from "../../lib/viewsApi";
import { useToastStore } from "../../state/toastStore";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";
import { AppSidebar } from "../AppSidebar";
import { RouterBridgeSync } from "../RouterBridgeSync";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchViews: jest.fn(),
  fetchView: jest.fn(),
  fetchSidebarEntries: jest.fn(),
  setEntryPinned: jest.fn(),
  reorderSidebarEntries: jest.fn(),
  createView: jest.fn(),
  updateView: jest.fn(),
  deleteView: jest.fn(),
}));

const mockFetchViews = fetchViews as jest.MockedFunction<typeof fetchViews>;
const mockFetchEntries = fetchSidebarEntries as jest.MockedFunction<typeof fetchSidebarEntries>;
const mockReorder = reorderSidebarEntries as jest.MockedFunction<typeof reorderSidebarEntries>;
const mockCreateView = createView as jest.MockedFunction<typeof createView>;
const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;
const mockDeleteView = deleteView as jest.MockedFunction<typeof deleteView>;

const SLUG = "loregarden";

type Json = Record<string, unknown>;

/**
 * A leftover entry from before Tools became static. Nothing creates these any
 * more and the sidebar draws none of them; they stay in the fixture because
 * every workspace seeded by 434 still has seven of them, and a view-row query
 * that accidentally matched one would find it here.
 */
function pageEntry(id: string, position: number, pageKey: string): SidebarEntry {
  return { id, position, entry_kind: "page", page_key: pageKey, view_id: "", pinned: false };
}

function viewEntry(id: string, position: number, viewId: string): SidebarEntry {
  return { id, position, entry_kind: "view", page_key: "", view_id: viewId, pinned: false };
}

const ENTRIES: SidebarEntry[] = [
  pageEntry("e-home", 10, "home"),
  pageEntry("e-queue", 30, "queue"),
  viewEntry("e-grid", 45, "v-grid"),
  viewEntry("e-canvas", 90, "v-canvas"),
];

/**
 * A configured grid, so duplicate has real container ids and settings to copy.
 *
 * A factory rather than a constant: the mocked `fetchViews` hands out the same
 * objects to every test, so a duplicate implemented by mutating the cached
 * record in place would corrupt the fixture for every test after it and turn
 * this suite into an order-dependent one. Rebuilt per test, that mutation fails
 * the test that caused it and nothing else.
 */
const gridLayout = (): Json => ({
  kind: "flex_grid",
  containers: {
    "c-left": { kind: "terminal", settings: { primitive_id: "terminal", workspace_slug: SLUG } },
    "c-right": { kind: "panel", settings: { primitive_id: "run_ledger", ticket_id: "t-1" } },
  },
  root: {
    node: "split",
    id: "n-root",
    size: 1,
    orientation: "horizontal",
    children: [
      { node: "leaf", id: "n-left", size: 0.5, container_id: "c-left" },
      { node: "leaf", id: "n-right", size: 0.5, container_id: "c-right" },
    ],
  },
});

/** The seeded grid a create returns — AC6's shape, not an empty registry. */
const seededGridLayout = (): Json => ({
  kind: "flex_grid",
  containers: { "c-new": { kind: "panel", settings: {} } },
  root: { node: "leaf", id: "n-new", size: 1, container_id: "c-new" },
});

const views = (): ViewSummary[] => [
  {
    id: "v-grid",
    kind: "flex_grid",
    title: "Build Board",
    icon: "",
    layout: gridLayout(),
    viewport: {},
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  },
  {
    id: "v-canvas",
    kind: "canvas",
    title: "Sketch Surface",
    icon: "",
    layout: { kind: "canvas", containers: {}, items: [] },
    viewport: {},
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  },
];

const created = (): ViewSummary => ({
  id: "v-new",
  kind: "flex_grid",
  title: "Roadmap",
  icon: "",
  // `{containers: {}, root: null}` is not a response the server can produce —
  // `FlexGridLayout` requires a root, and `_StructureWalk` refuses an empty
  // grid. A fixture the server could never send teaches the implementation the
  // wrong shape for the thing it is about to render.
  layout: seededGridLayout(),
  viewport: {},
  created_at: "2026-08-14T00:00:00",
  updated_at: "2026-08-14T00:00:00",
});

/** The records this test's mocked reads resolve with, so mutation is observable. */
let VIEWS: ViewSummary[];
let CREATED: ViewSummary;

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="pathname">{location.pathname}</span>;
}

function pathname(): string {
  return screen.getByTestId("pathname").textContent ?? "";
}

/**
 * The app's own client, minus react-query's ~1s retry backoff.
 *
 * `MutationCache.onError` is where a failed mutation becomes a toast, and a bare
 * `QueryClient` has none — so any assertion about what a refusal did or did not
 * report is vacuous unless the tree is wired to this one.
 */
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

function renderSidebar(path = "/", client?: QueryClient) {
  const qc =
    client ??
    new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
    });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <RouterBridgeSync />
        <AppSidebar workspaceSlug={SLUG} onOpenSettings={jest.fn()} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function renderLoadedSidebar(path = "/", client?: QueryClient) {
  const utils = renderSidebar(path, client);
  await screen.findByRole("link", { name: "Build Board" });
  return utils;
}

/**
 * The footer pair — where 473 moved the create affordance.
 *
 * It used to live in the Tabs section head. Creating and pinning are the two
 * ways a tab appears, so they are drawn side by side in the footer instead.
 */
function footerControls(): HTMLElement {
  const controls = screen
    .getByRole("button", { name: /new tab/i })
    .closest(".app-sidebar-footer-controls");
  if (!controls) throw new Error("No footer controls in the sidebar");
  return controls as HTMLElement;
}

/**
 * A control by accessible name, whatever role it was built as.
 *
 * The kind choice is a two-option pick; radio, button and option are all
 * defensible markup for it and the ticket does not choose. What is not
 * negotiable is that both kinds are offered and reachable by name.
 */
function control(name: RegExp, scope?: HTMLElement): HTMLElement {
  const root = scope ? within(scope) : screen;
  const found = [
    ...root.queryAllByRole("radio", { name }),
    ...root.queryAllByRole("button", { name }),
    ...root.queryAllByRole("option", { name }),
    ...root.queryAllByRole("menuitem", { name }),
  ];
  if (found.length === 0) throw new Error(`No control named ${name}`);
  return found[0];
}

/** Open the New View form and fill it in, leaving submission to the caller. */
async function openNewView(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(footerControls()).getByRole("button", { name: /new tab/i }));
  return await screen.findByRole("dialog");
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.getState().clear();
  VIEWS = views();
  CREATED = created();
  mockFetchEntries.mockResolvedValue(ENTRIES);
  mockFetchViews.mockResolvedValue(VIEWS);
  mockReorder.mockResolvedValue(ENTRIES);
  mockCreateView.mockResolvedValue(CREATED);
  mockUpdateView.mockImplementation(async (_slug, viewId, patch) => ({
    ...VIEWS[0],
    id: viewId,
    ...(patch as Partial<ViewSummary>),
  }));
  mockDeleteView.mockImplementation(async (_slug, viewId) => ({ deleted: viewId }));
});

describe("AC2 (regression, 434) — a view route highlights its own Tabs entry", () => {
  it("marks the routed view active and leaves Home alone", async () => {
    await renderLoadedSidebar("/view/v-grid");

    const active = screen.getByRole("link", { name: "Build Board" });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active.className).toContain("app-sidebar-link--active");

    // `pageFromPath` answers "home" for every path it does not recognise, so a
    // view route lighting up Home is the failure this guards.
    const home = screen.getByRole("link", { name: "Home" });
    expect(home).not.toHaveAttribute("aria-current", "page");
    expect(home.className).not.toContain("app-sidebar-link--active");
  });

  it("does not light up a different view's tab", async () => {
    await renderLoadedSidebar("/view/v-grid");

    const other = screen.getByRole("link", { name: "Sketch Surface" });
    expect(other.className).not.toContain("app-sidebar-link--active");
    // Both mechanisms, because they are independent: the class comes from
    // `viewIdFromPath`, `aria-current` from `NavLink`'s own path match. A change
    // that keeps one and drops the other leaves the rail looking right to a
    // sighted user and wrong to a screen reader, or the reverse.
    expect(other).not.toHaveAttribute("aria-current", "page");
  });

  it("still highlights the routed view when its id had to be escaped", async () => {
    // `viewPath` encodes, so the link's href and the location agree; a
    // highlight that compared the raw path segment against the id would miss.
    const escaped = views();
    escaped[0] = { ...escaped[0], id: "v grid" };
    mockFetchViews.mockResolvedValue(escaped);
    mockFetchEntries.mockResolvedValue([
      pageEntry("e-home", 10, "home"),
      viewEntry("e-grid", 45, "v grid"),
    ]);

    await renderLoadedSidebar(`/view/${encodeURIComponent("v grid")}`);

    const active = screen.getByRole("link", { name: "Build Board" });
    expect(active.className).toContain("app-sidebar-link--active");
    expect(screen.getByRole("link", { name: "Home" }).className).not.toContain(
      "app-sidebar-link--active",
    );
  });
});

describe("AC5 — New View picks a kind, names it, creates it and lands on it", () => {
  it("offers the affordance in the footer, beside Pin tab", async () => {
    await renderLoadedSidebar();

    // 473 moved it out of the Tabs section head. Asserting only that a button
    // exists somewhere would pass with it left where it was.
    expect(within(footerControls()).getByRole("button", { name: /new tab/i })).toBeInTheDocument();
    // The head, not the whole section: the rows below it carry their own
    // controls, and those are not what moved.
    const head = screen.getByText("Tabs").closest(".app-sidebar-section-head");
    expect(within(head as HTMLElement).queryByRole("button")).toBeNull();
  });

  it("does not offer it when there is no workspace to create in", async () => {
    // With no slug the create POSTs to `/api/workspaces//views` — a 404 the
    // user asked for by pressing a control the chrome offered them. The rows
    // are gated on the same slug; the control that writes has to be too.
    render(
      <QueryClientProvider client={appClient()}>
        <MemoryRouter>
          <AppSidebar workspaceSlug="" onOpenSettings={jest.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await settle();

    expect(screen.getByText("Tabs")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new tab/i })).toBeNull();
    // Pin writes to the same unresolved path, so it is gated with it.
    expect(screen.queryByRole("button", { name: /pin tab/i })).toBeNull();
    expect(mockCreateView).not.toHaveBeenCalled();
  });

  it("offers both kinds and a name", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();

    const dialog = await openNewView(user);

    expect(within(dialog).getByRole("textbox")).toBeInTheDocument();
    expect(control(/flex grid/i, dialog)).toBeInTheDocument();
    expect(control(/canvas/i, dialog)).toBeInTheDocument();
  });

  it("creates a flex grid without sending a top-level kind", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();
    const dialog = await openNewView(user);

    await user.click(control(/flex grid/i, dialog));
    await user.type(within(dialog).getByRole("textbox"), "Roadmap");
    await user.click(within(dialog).getByRole("button", { name: /^(create|add|save)/i }));

    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));
    const [slug, body] = mockCreateView.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(body).not.toHaveProperty("kind");
    expect(body.title).toBe("Roadmap");
    // The picked kind selects the *layout* to seed, which is the only place the
    // kind is allowed to appear. `ViewCreate` is `extra="forbid"`; a body
    // carrying the kind twice is a 422 with nothing on screen to explain it.
    expect((body.layout as Json).kind).toBe("flex_grid");
    // And the seed has to be one the server accepts. Omitting no top-level
    // `kind` is necessary and not sufficient: `{containers: {}, root: null}`
    // also sends no `kind`, and is a 422 for a different reason.
    assertServerAcceptableLayout(body.layout);
    // AC6, at the point the view is made rather than the point it is opened:
    // the grid arrives with the container it will prompt on.
    expect(Object.keys((body.layout as Json).containers as Json)).toHaveLength(1);
  });

  it("seeds a canvas layout when canvas is picked", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();
    const dialog = await openNewView(user);

    await user.click(control(/canvas/i, dialog));
    await user.type(within(dialog).getByRole("textbox"), "Sketchpad");
    await user.click(within(dialog).getByRole("button", { name: /^(create|add|save)/i }));

    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));
    const layout = mockCreateView.mock.calls[0][1].layout as Json;
    // The picked kind reaches the body — a form that ignored the choice and
    // always seeded a grid would satisfy every other assertion in this suite.
    expect(layout.kind).toBe("canvas");
    assertServerAcceptableLayout(layout);
    expect(layout.containers).toEqual({});
  });

  it("navigates to the created view, and only once the server has assigned its id", async () => {
    const user = userEvent.setup();
    let release: (view: ViewSummary) => void = () => {};
    mockCreateView.mockReturnValue(
      new Promise<ViewSummary>((resolve) => {
        release = resolve;
      }),
    );

    await renderLoadedSidebar();
    const dialog = await openNewView(user);
    await user.click(control(/flex grid/i, dialog));
    await user.type(within(dialog).getByRole("textbox"), "Roadmap");
    await user.click(within(dialog).getByRole("button", { name: /^(create|add|save)/i }));

    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));
    await settle();
    // Nowhere to navigate to yet: the id is the server's. An optimistic hop
    // lands on a URL whose view does not exist, which renders AC4's not-found
    // state as the *success* path.
    expect(pathname()).toBe("/");

    mockFetchViews.mockResolvedValue([...VIEWS, CREATED]);
    mockFetchEntries.mockResolvedValue([...ENTRIES, viewEntry("e-new", 120, "v-new")]);
    await act(async () => {
      release(CREATED);
    });

    await waitFor(() => expect(pathname()).toBe("/view/v-new"));
  });

  it("refreshes both the view list and the entry list", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();
    const viewReads = mockFetchViews.mock.calls.length;
    const entryReads = mockFetchEntries.mock.calls.length;

    const dialog = await openNewView(user);
    await user.click(control(/flex grid/i, dialog));
    await user.type(within(dialog).getByRole("textbox"), "Roadmap");
    await user.click(within(dialog).getByRole("button", { name: /^(create|add|save)/i }));

    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));
    // One transaction on the server, two queries on the client. Invalidating
    // only the views leaves the sidebar without the tab it just made.
    await waitFor(() => expect(mockFetchViews.mock.calls.length).toBeGreaterThan(viewReads));
    await waitFor(() => expect(mockFetchEntries.mock.calls.length).toBeGreaterThan(entryReads));
  });
});

// AC5, failure half — the two refusals a create actually gets, which are not the
// same kind of news. A 409 says a peer took the sidebar position this create was
// appending to: the body was well formed and is worth re-issuing, and a user who
// never learns a race happened is a user who was told nothing wrong. A 400/422
// says the body is wrong, and re-sending it changes nothing, so it is shown once,
// in the form that produced it.
describe("AC5 — a create that is refused", () => {
  /** Fill the New View form and submit it. */
  async function submitNewView(user: ReturnType<typeof userEvent.setup>) {
    const dialog = await openNewView(user);
    await user.click(control(/flex grid/i, dialog));
    await user.type(within(dialog).getByRole("textbox"), "Roadmap");
    await user.click(within(dialog).getByRole("button", { name: /^(create|add|save)/i }));
    return dialog;
  }

  it("retries a lost race and lands on the view, quietly", async () => {
    mockCreateView.mockRejectedValueOnce(new ApiError(409, "sidebar entries changed"));
    mockCreateView.mockResolvedValue(CREATED);

    const user = userEvent.setup();
    await renderLoadedSidebar("/", appClient());
    mockFetchViews.mockResolvedValue([...VIEWS, CREATED]);
    mockFetchEntries.mockResolvedValue([...ENTRIES, viewEntry("e-new", 120, "v-new")]);

    await submitNewView(user);

    // Twice: the second attempt re-sends the same body, because unlike the
    // reorder nothing in it depended on the ranking it lost to.
    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(2));
    expect(mockCreateView.mock.calls[1][1]).toEqual(mockCreateView.mock.calls[0][1]);
    // The view exists and the user is on it — dropping the create on the first
    // 409 loses a well-formed request to a race the user never caused.
    await waitFor(() => expect(pathname()).toBe("/view/v-new"));
    await settle();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("does not retry a refused body, and says why in the dialog", async () => {
    mockCreateView.mockRejectedValue(new ApiError(422, "layout.root: unknown container id"));

    const user = userEvent.setup();
    await renderLoadedSidebar("/", appClient());

    const dialog = await submitNewView(user);

    await waitFor(() => expect(mockCreateView).toHaveBeenCalled());
    await settle();
    // Exactly once. Re-sending a body the server called malformed cannot
    // succeed; it only delays the explanation by the length of the retry loop.
    expect(mockCreateView).toHaveBeenCalledTimes(1);
    // In place, and with the server's own reason: a modal that closes on a view
    // that was never made is the one failure worse than one that will not close.
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/unknown container id/)).toBeInTheDocument();
    expect(pathname()).toBe("/");
  });

  it("leaves the refusal in the dialog rather than also toasting it", async () => {
    mockCreateView.mockRejectedValue(new ApiError(422, "layout.root: unknown container id"));

    const user = userEvent.setup();
    await renderLoadedSidebar("/", appClient());

    await submitNewView(user);

    await waitFor(() => expect(mockCreateView).toHaveBeenCalled());
    await settle();
    // `api/queryClient.ts` toasts every rejected mutation that does not opt out.
    // Without `meta.suppressErrorToast` this create explains itself twice — once
    // in the modal and once in a toast drawn over it.
    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

describe("AC7 — rename, duplicate and delete", () => {
  it("renames through the API (regression, 434)", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();

    await user.click(screen.getByRole("button", { name: "Rename Build Board" }));
    const field = screen.getByRole("textbox", { name: "View title" });
    await user.clear(field);
    await user.type(field, "Roadmap{Enter}");

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledWith(SLUG, "v-grid", { title: "Roadmap" }));
  });

  it("duplicates through create, with container ids that are not the original's", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();

    await user.click(screen.getByRole("button", { name: "Duplicate Build Board" }));

    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));
    const [slug, body] = mockCreateView.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(body).not.toHaveProperty("kind");

    const layout = body.layout as Json;
    expect(layout.kind).toBe("flex_grid");
    assertServerAcceptableLayout(layout);
    const keys = Object.keys(layout.containers as Json);
    expect(keys).toHaveLength(2);
    // Fresh keys, and the arrangement pointing at them — a copy that kept
    // "c-left"/"c-right" is accepted by the server and aliases the two views;
    // a copy that regenerated the keys without rewriting the leaves is refused
    // outright as an unknown container reference.
    expect(keys).not.toContain("c-left");
    expect(keys).not.toContain("c-right");
    const leaves = ((layout.root as Json).children as Json[]).map((child) => child.container_id);
    expect([...leaves].sort()).toEqual([...keys].sort());
    // The contents came with it: a "duplicate" that posted a fresh empty grid
    // has fresh container ids and a sound structure too.
    const settings = Object.values(layout.containers as Record<string, Json>).map(
      (container) => (container.settings as Json).primitive_id,
    );
    expect([...settings].sort()).toEqual(["run_ledger", "terminal"]);
  });

  it("makes one copy when the control is double-clicked", async () => {
    // A duplicate takes a round trip and gives no feedback of its own, so a
    // second click before the first lands is realistic — and it creates a second
    // view and navigates to *that*, leaving a stray copy the user never asked
    // for on a tab they cannot see. New View is already guarded this way.
    const user = userEvent.setup();
    let release: (view: ViewSummary) => void = () => {};
    mockCreateView.mockReturnValue(
      new Promise<ViewSummary>((resolve) => {
        release = resolve;
      }),
    );
    await renderLoadedSidebar();

    // Both presses inside one act, with no render between them: that is what a
    // double-click is. A guard that lives only in the `disabled` attribute has
    // not been applied yet when the second one arrives, and two views are made.
    const control = screen.getByRole("button", { name: "Duplicate Build Board" });
    await act(async () => {
      fireEvent.click(control);
      fireEvent.click(control);
    });
    await settle();

    expect(mockCreateView).toHaveBeenCalledTimes(1);
    // And the control says so, for the click that comes after a render.
    expect(screen.getByRole("button", { name: "Duplicate Build Board" })).toBeDisabled();

    // And the guard lifts once the write settles, rather than disabling the
    // control for the rest of the session.
    mockFetchViews.mockResolvedValue([...VIEWS, CREATED]);
    mockFetchEntries.mockResolvedValue([...ENTRIES, viewEntry("e-new", 120, "v-new")]);
    await act(async () => {
      release(CREATED);
    });
    await settle();
    mockCreateView.mockResolvedValue(CREATED);

    await user.click(screen.getByRole("button", { name: "Duplicate Build Board" }));
    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(2));
  });

  it("duplicates a canvas view as a canvas", async () => {
    // The duplicate path reads the kind off the source layout. One written
    // against the grid alone posts a grid for a canvas source, which the server
    // stores — under the wrong discriminator, on a view the canvas renderer
    // then cannot open.
    const user = userEvent.setup();
    await renderLoadedSidebar();

    await user.click(screen.getByRole("button", { name: "Duplicate Sketch Surface" }));

    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));
    const layout = mockCreateView.mock.calls[0][1].layout as Json;
    expect(layout.kind).toBe("canvas");
    assertServerAcceptableLayout(layout);
  });

  it("leaves the source view's layout untouched when duplicating", async () => {
    const user = userEvent.setup();
    const before = gridLayout();
    await renderLoadedSidebar();

    await user.click(screen.getByRole("button", { name: "Duplicate Build Board" }));
    await waitFor(() => expect(mockCreateView).toHaveBeenCalledTimes(1));

    // A copy built by mutating the cached record in place corrupts the open view.
    expect(VIEWS[0].layout).toEqual(before);
  });

  it("confirms before deleting", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();

    await user.click(screen.getByRole("button", { name: "Delete Build Board" }));

    // Deleting a view is not recoverable: there is no undo endpoint, and the
    // layout goes with it.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Build Board/)).toBeInTheDocument();
    await settle();
    expect(mockDeleteView).not.toHaveBeenCalled();
  });

  it("does not delete when the confirmation is dismissed", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();
    await user.click(screen.getByRole("button", { name: "Delete Build Board" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: /^(cancel|keep|never mind)/i }));

    await settle();
    expect(mockDeleteView).not.toHaveBeenCalled();
  });

  it("deletes once confirmed", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar();
    await user.click(screen.getByRole("button", { name: "Delete Build Board" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: /^(delete|remove)/i }));

    await waitFor(() => expect(mockDeleteView).toHaveBeenCalledWith(SLUG, "v-grid"));
  });

  it("navigates away when the deleted view is the one on screen", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar("/view/v-grid");
    await user.click(screen.getByRole("button", { name: "Delete Build Board" }));
    const dialog = await screen.findByRole("dialog");

    mockFetchViews.mockResolvedValue([VIEWS[1]]);
    mockFetchEntries.mockResolvedValue(ENTRIES.filter((entry) => entry.view_id !== "v-grid"));
    await user.click(within(dialog).getByRole("button", { name: /^(delete|remove)/i }));

    // Otherwise the delete succeeds and strands the user on the 404 their own
    // action just created.
    await waitFor(() => expect(pathname()).not.toBe("/view/v-grid"));
    expect(pathname()).toBe("/");
  });

  it("stays put when the deleted view is not the one on screen", async () => {
    const user = userEvent.setup();
    await renderLoadedSidebar("/view/v-canvas");
    await user.click(screen.getByRole("button", { name: "Delete Build Board" }));
    const dialog = await screen.findByRole("dialog");

    mockFetchViews.mockResolvedValue([VIEWS[1]]);
    mockFetchEntries.mockResolvedValue(ENTRIES.filter((entry) => entry.view_id !== "v-grid"));
    await user.click(within(dialog).getByRole("button", { name: /^(delete|remove)/i }));

    await waitFor(() => expect(mockDeleteView).toHaveBeenCalled());
    await settle();
    // "Navigate away on delete" written without the condition kicks the user
    // off a view they were reading because a different tab was closed.
    expect(pathname()).toBe("/view/v-canvas");
  });
});
