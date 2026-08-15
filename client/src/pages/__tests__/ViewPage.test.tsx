/**
 * The `/view/:viewId` host: what the route resolves to, what it does when the
 * view is gone, and what a brand-new grid looks like on the first frame.
 *
 * Written before `client/src/pages/ViewPage.tsx` exists, so every failure here
 * is currently a missing module.
 *
 * The contract these tests pin, and why each part of it is shaped that way:
 *
 *   - `ViewPage` reads its workspace from `SidebarWorkspaceContext`. The route
 *     lives in a flat table with no workspace in it, and `uiStore.workspace` is
 *     `"all"` until the Dashboard picker moves it — a slug that 404s against
 *     every view route. `AppLayout` already resolves a concrete slug for the
 *     sidebar and publishes it there, so these tests provide the same context
 *     rather than reproducing that resolution. A second intake (a prop) would be
 *     a second answer to one question, and the pane components read the context
 *     directly anyway.
 *   - The rendered view carries `data-testid="view-host"` with `data-view-id`
 *     and `data-view-kind`. The grid (440) and canvas (442) renderers do not
 *     exist yet, so "renders the view matching its stored kind" has to be
 *     asserted on something this ticket actually produces. These attributes are
 *     it, and they stay useful afterwards as the seam those renderers hang off.
 *   - The not-found state carries `data-testid="view-not-found"`, on the same
 *     reasoning and to avoid pinning its wording: AC4 requires *a* not-found
 *     state and a route back, and no acceptance criterion pins the sentence it
 *     says that in. The assertions below are that the state exists, says
 *     something, offers exactly one way out, and that the way out works.
 *
 * The AC6 assertions deliberately go past "something is on screen": a seeded
 * container that fell through to `ContainerPrimitiveHost` renders the
 * "primitive this build does not have" placeholder, which is on screen, is not
 * blank, and is not a prompt. `data-primitive-unknown` is checked absent for
 * exactly that reason.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "../../api/http";
import { createQueryClient } from "../../api/queryClient";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import { newContainerFor } from "../../components/views/primitives/registry";
import { fetchView, updateView, viewsKeys, type ViewSummary } from "../../lib/viewsApi";
import { SidebarWorkspaceProvider } from "../../state/SidebarWorkspaceContext";
import { useToastStore } from "../../state/toastStore";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";
import { ViewPage } from "../ViewPage";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

const mockFetchView = fetchView as jest.MockedFunction<typeof fetchView>;
const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;

const SLUG = "loregarden";

type Json = Record<string, unknown>;

/**
 * Fixtures are factories, not constants.
 *
 * The mocked `fetchView` hands the caller whatever object it is given, so a
 * shared constant is the *same* object in every test: an implementation that
 * edits the loaded layout in place corrupts the fixture for every test that
 * runs after it, and the suite starts depending on its own order. A fresh
 * object per test makes that mutation a failure in the test that caused it.
 */
const emptyGridLayout = (): Json => ({
  kind: "flex_grid",
  containers: { "c-seed": { kind: "panel", settings: {} } },
  root: { node: "leaf", id: "n-seed", size: 1, container_id: "c-seed" },
});

const canvasLayout = (): Json => ({ kind: "canvas", containers: {}, items: [] });

/** Two unconfigured containers, so two picks can be made without waiting. */
const twoPaneLayout = (): Json => ({
  kind: "flex_grid",
  containers: {
    "c-1": { kind: "panel", settings: {} },
    "c-2": { kind: "panel", settings: {} },
  },
  root: {
    node: "split",
    id: "n-root",
    size: 1,
    orientation: "horizontal",
    children: [
      { node: "leaf", id: "n-1", size: 0.5, container_id: "c-1" },
      { node: "leaf", id: "n-2", size: 0.5, container_id: "c-2" },
    ],
  },
});

/** The container `newContainerFor` mints, without the `undefined` in its type. */
function madeContainer(primitiveId: string): Json {
  const made = newContainerFor(primitiveId);
  if (made === undefined) throw new Error(`No primitive named ${primitiveId}`);
  return made as unknown as Json;
}

function view(id: string, kind: "flex_grid" | "canvas", layout: Json): ViewSummary {
  return {
    id,
    kind,
    title: kind === "canvas" ? "Sketch Surface" : "Build Board",
    icon: "",
    layout,
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

const gridView = () => view("v-grid", "flex_grid", emptyGridLayout());
const canvasView = () => view("v-canvas", "canvas", canvasLayout());

/** The record this test's `fetchView` resolves with, so mutation is observable. */
let loadedGridView: ViewSummary;

/**
 * Every history entry the router has been through, newest last.
 *
 * "The pathname is unchanged" is not the same as "nothing navigated": a bounce
 * to `/` that the sidebar's cached entry immediately reverses ends on the URL
 * it started from. `location.key` is fresh per history entry, so a loop that
 * returns to where it began still shows up here as extra visits.
 */
let visits: string[] = [];

function LocationProbe() {
  const location = useLocation();
  useEffect(() => {
    visits.push(`${location.key} ${location.pathname}`);
  }, [location]);
  return <span data-testid="pathname">{location.pathname}</span>;
}

function pathname(): string {
  return screen.getByTestId("pathname").textContent ?? "";
}

/** The not-found state, identified by its testid rather than its wording. */
function notFound(): Promise<HTMLElement> {
  return screen.findByTestId("view-not-found");
}

/**
 * The way out of the not-found state, however it is built.
 *
 * AC4 asks for "a route back" and does not say whether it is a link or a
 * button, nor where it goes, nor what it is called — a `NavLink` to `/` is the
 * honest markup, a button calling `navigateToPage` matches
 * `PageErrorBoundary`'s existing fallback, and both are defensible. So the
 * assertion is that the state offers a way out and that using it leaves the
 * view route; the tag, the label and the destination are not pinned, and the
 * state is free to offer other affordances alongside it.
 *
 * Links are preferred over buttons when both are present because a navigation
 * is a link and a retry is a button — the ambiguity, if a build ever creates
 * one, resolves toward the control AC4 is about.
 */
function routeBack(region: HTMLElement): HTMLElement {
  const scope = within(region);
  const links = scope.queryAllByRole("link");
  const found = links.length > 0 ? links : scope.queryAllByRole("button");
  if (found.length === 0) throw new Error("No route back in the not-found state");
  return found[0];
}

function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
}

function renderRoute(path: string, client?: QueryClient) {
  const qc = client ?? testClient();
  return render(
    <QueryClientProvider client={qc}>
      <SidebarWorkspaceProvider slug={SLUG}>
        <MemoryRouter initialEntries={[path]}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/" element={<h1>Home</h1>} />
            <Route path="/view/:viewId" element={<ViewPage />} />
          </Routes>
          <LocationProbe />
          {/* A way to leave one view for another without a full re-render, so a
              write issued on the first can land while the second is on screen. */}
          <Link to="/view/v-b">Open B</Link>
        </MemoryRouter>
      </SidebarWorkspaceProvider>
    </QueryClientProvider>,
  );
}

/**
 * Let every already-scheduled promise settle. The "and then nothing else
 * happened" assertions need it — without it they pass because the redirect had
 * not been issued yet.
 */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** The "choose a primitive" prompt inside one container's pane. */
function promptIn(root: HTMLElement, containerId: string): HTMLElement {
  const pane = root.querySelector<HTMLElement>(`[data-container-id="${containerId}"]`);
  if (pane === null) throw new Error(`No pane for ${containerId}`);
  return within(pane).getByRole("button", { name: /primitive/i });
}

/** The picker option for one primitive, once the picker is open. */
async function primitiveOption(root: HTMLElement, primitiveId: string): Promise<HTMLElement> {
  return waitFor(() => {
    const found = root.querySelector<HTMLElement>(`[data-primitive-id="${primitiveId}"]`);
    expect(found).not.toBeNull();
    return found as HTMLElement;
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  visits = [];
  useToastStore.getState().clear();
  loadedGridView = gridView();
  mockFetchView.mockResolvedValue(loadedGridView);
  mockUpdateView.mockImplementation(async (_slug, _viewId, patch) => ({
    ...gridView(),
    ...(patch as Partial<ViewSummary>),
  }));
});

describe("AC1 — /view/:viewId renders the view matching its stored kind", () => {
  it("loads the view named in the URL and renders it", async () => {
    renderRoute("/view/v-grid");

    const rendered = await screen.findByTestId("view-host");
    expect(rendered).toHaveAttribute("data-view-id", "v-grid");
    expect(rendered).toHaveAttribute("data-view-kind", "flex_grid");
    expect(mockFetchView).toHaveBeenCalledWith(SLUG, "v-grid");
  });

  it("dispatches on the stored kind, not on the URL", async () => {
    // The URL carries an id and nothing else; the kind comes back with the
    // record. A host that guessed from the path would render a grid here.
    mockFetchView.mockResolvedValue(canvasView());

    renderRoute("/view/v-canvas");

    expect(await screen.findByTestId("view-host")).toHaveAttribute("data-view-kind", "canvas");
  });

  it("survives a reload of the same deep link", async () => {
    // "Deep-linkable across a reload" means the page derives everything from
    // the URL: a fresh mount with a cold cache resolves the same view.
    const first = renderRoute("/view/v-grid");
    await screen.findByTestId("view-host");
    first.unmount();

    renderRoute("/view/v-grid");
    expect(await screen.findByTestId("view-host")).toHaveAttribute("data-view-id", "v-grid");
    expect(mockFetchView).toHaveBeenCalledTimes(2);
  });

  it("decodes an id that had to be escaped into the path", async () => {
    mockFetchView.mockResolvedValue(view("v grid", "flex_grid", emptyGridLayout()));

    renderRoute(`/view/${encodeURIComponent("v grid")}`);

    await screen.findByTestId("view-host");
    expect(mockFetchView).toHaveBeenCalledWith(SLUG, "v grid");
  });
});

describe("AC4 — an unknown or deleted view is a not-found state, not a redirect", () => {
  beforeEach(() => {
    mockFetchView.mockRejectedValue(new ApiError(404, "View not found"));
  });

  it("renders a not-found state with a route back", async () => {
    renderRoute("/view/gone");

    // The state has to *say* something: a blank pane and a spinner that never
    // resolves also fail to redirect-loop, and neither is AC4. The wording is
    // not pinned — no acceptance criterion chooses it — so the assertion is
    // that the region exists, is not empty, and is not the view.
    const region = await notFound();
    expect(region).toBeVisible();
    expect((region.textContent ?? "").trim().length).toBeGreaterThan(0);
    expect(routeBack(region)).toBeInTheDocument();
    expect(screen.queryByTestId("view-host")).toBeNull();
  });

  it("stays on the URL it was asked for, without navigating at all", async () => {
    renderRoute("/view/gone");
    await notFound();
    await settle();
    await settle();

    // An automatic bounce to "/" is the tempting fix and it is the loop: the
    // sidebar entry the user clicked is still in the cache, so they click it
    // again and bounce again. Checking the pathname alone would miss a bounce
    // that came back — hence the history-entry count, which a round trip grows
    // even when it ends where it started.
    expect(pathname()).toBe("/view/gone");
    expect(visits).toHaveLength(1);
    // And the read settled: a page that keeps re-issuing the failing GET is the
    // same loop one layer down, with the same symptom for the server.
    expect(mockFetchView).toHaveBeenCalledTimes(1);
  });

  it("leaves the view route once, when the user asks", async () => {
    const user = userEvent.setup();
    renderRoute("/view/gone");
    const region = await notFound();

    await user.click(routeBack(region));

    await waitFor(() => expect(pathname()).not.toBe("/view/gone"));
    // Wherever it goes, it is not another view route — that would 404 again.
    expect(pathname().startsWith("/view/")).toBe(false);
    await settle();
    await settle();
    // Exactly one navigation followed the click: arriving and then bouncing
    // onward is how a "helpful" redirect turns into a loop the user cannot stop.
    expect(visits).toHaveLength(2);
  });

  it("does not also raise an error toast", async () => {
    // The app's `MutationCache.onError` toasts every unsuppressed failure, so a
    // read modelled as a mutation — or a hand-rolled `toastActionFailed` in the
    // page — turns an expected 404 into an alarm stacked on a state that
    // already explains itself. The real client is used so that rule is live.
    renderRoute("/view/gone", createQueryClient());
    await notFound();
    await settle();

    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("shows the same state for an id that never existed", async () => {
    // AC4 is "unknown **or** deleted". A page that special-cased the deleted
    // case by consulting the sidebar cache would blank on an id typed by hand.
    mockFetchView.mockRejectedValue(new ApiError(404, "View not found"));
    renderRoute("/view/never-existed");
    expect(await notFound()).toBeVisible();
    expect(screen.queryByTestId("view-host")).toBeNull();
  });

  it("does not claim not-found when the read failed for another reason", async () => {
    // A 500 or a dropped connection is not "this view is gone", and rendering
    // the not-found state for it tells the user to stop looking for a view that
    // is still there. Whatever it renders, it is not the not-found state and it
    // is not the view.
    mockFetchView.mockRejectedValue(new ApiError(500, "Internal Server Error"));
    renderRoute("/view/v-grid");
    await settle();
    await settle();

    expect(screen.queryByTestId("view-not-found")).toBeNull();
    expect(screen.queryByTestId("view-host")).toBeNull();
    expect(pathname()).toBe("/view/v-grid");
  });
});

describe("AC6 — a new flex grid opens on a container asking for a primitive", () => {
  it("renders one container that prompts, rather than an unknown-primitive placeholder", async () => {
    const { container } = renderRoute("/view/v-grid");
    await screen.findByTestId("view-host");

    expect(container.querySelectorAll("[data-container-id]")).toHaveLength(1);
    // `ContainerPrimitiveHost` marks a container whose `primitive_id` it cannot
    // resolve. A seeded container has no `primitive_id` at all, so handing it
    // straight to the host produces that placeholder — on screen, and not a
    // prompt.
    expect(container.querySelector("[data-primitive-unknown]")).toBeNull();
    expect(screen.getByRole("button", { name: /primitive/i })).toBeInTheDocument();
  });

  it("reaches the registry-derived picker from that prompt", async () => {
    const user = userEvent.setup();
    const { container } = renderRoute("/view/v-grid");
    await screen.findByTestId("view-host");

    await user.click(screen.getByRole("button", { name: /primitive/i }));

    await primitiveOption(container, "terminal");
    // Derived from the registry rather than authored here, so a picker offering
    // a hardcoded subset fails this.
    expect(container.querySelectorAll("[data-primitive-id]").length).toBeGreaterThanOrEqual(3);
  });

  it("replaces the container with newContainerFor's, rather than merging settings", async () => {
    const user = userEvent.setup();
    const { container } = renderRoute("/view/v-grid");
    await screen.findByTestId("view-host");
    await user.click(screen.getByRole("button", { name: /primitive/i }));

    await user.click(await primitiveOption(container, "terminal"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const [slug, viewId, patch] = mockUpdateView.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(viewId).toBe("v-grid");

    // PATCH is genuinely partial and `extra="forbid"`: a body that also carried
    // the title, or the view's kind, is a 422.
    expect(Object.keys(patch as Json)).toEqual(["layout"]);

    const layout = (patch as { layout: Json }).layout;
    // Whatever it composed, the server has to accept it — this is the one write
    // this ticket makes to a stored layout.
    assertServerAcceptableLayout(layout);

    const containers = layout.containers as Record<string, Json>;
    // The seeded container is *replaced*. Merging `primitive_id` into the
    // placeholder leaves `kind: "panel"` behind, and `ContainerPrimitiveHost`
    // refuses to mount a terminal primitive stored under `panel` — so the pane
    // the user just picked renders a kind-mismatch placeholder instead.
    const expected = newContainerFor("terminal");
    // `newContainerFor` returns `undefined` for an id the registry does not
    // know, and `undefined === undefined` would make a dropped container pass.
    expect(expected).toBeDefined();
    expect(containers["c-seed"]).toEqual(expected);
    // The arrangement is untouched: the pane keeps its place and its id.
    expect(layout.root).toEqual(emptyGridLayout().root);
    expect(Object.keys(containers)).toEqual(["c-seed"]);
  });

  it("does not mutate the loaded view's layout while composing the patch", async () => {
    // The record react-query is holding is the one the pane is rendered from.
    // Editing it in place makes the PATCH body and the screen agree by
    // accident, and hides a write that never reached the server.
    const user = userEvent.setup();
    const before = emptyGridLayout();
    const { container } = renderRoute("/view/v-grid");
    await screen.findByTestId("view-host");
    await user.click(screen.getByRole("button", { name: /primitive/i }));
    await user.click(await primitiveOption(container, "terminal"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(loadedGridView.layout).toEqual(before);
  });

  it("mounts the chosen primitive once the write lands", async () => {
    const user = userEvent.setup();
    const chosen = {
      ...emptyGridLayout(),
      containers: { "c-seed": newContainerFor("terminal") },
    };
    mockUpdateView.mockResolvedValue(view("v-grid", "flex_grid", chosen as Json));

    const { container } = renderRoute("/view/v-grid");
    await screen.findByTestId("view-host");
    await user.click(screen.getByRole("button", { name: /primitive/i }));
    await user.click(await primitiveOption(container, "terminal"));

    // The pane goes through the host's dispatch, which stamps the id it
    // resolved — and stamps `data-primitive-unknown` instead when the stored
    // container and the primitive disagree.
    await waitFor(() => {
      const pane = container.querySelector('[data-container-id="c-seed"]');
      expect(pane).not.toBeNull();
      expect(pane).toHaveAttribute("data-primitive-id", "terminal");
    });
    expect(container.querySelector("[data-primitive-unknown]")).toBeNull();
  });
});

/**
 * The layout write, once more than one of it can exist.
 *
 * These three failures are the same mistake at three depths: a write that reads
 * anything — its target, its base layout, the cache entry it updates — from the
 * render that happened to be on screen rather than from the write itself.
 */
describe("a layout write acts on the view it was issued against", () => {
  const viewB = (): ViewSummary =>
    view("v-b", "flex_grid", {
      kind: "flex_grid",
      containers: { "c-b": { kind: "panel", settings: {} } },
      root: { node: "leaf", id: "n-b", size: 1, container_id: "c-b" },
    });

  /** Pick `primitiveId` in one container's pane, from prompt to option. */
  async function pick(
    user: ReturnType<typeof userEvent.setup>,
    root: HTMLElement,
    containerId: string,
    primitiveId: string,
  ) {
    await user.click(promptIn(root, containerId));
    const pane = root.querySelector<HTMLElement>(`[data-container-id="${containerId}"]`);
    await user.click(await primitiveOption(pane as HTMLElement, primitiveId));
  }

  it("does not write one view's record into another view's cache entry", async () => {
    // `useMutation` re-binds its options every render, in-flight mutations
    // included. A success handler reading `viewId` from the closure therefore
    // acts on whichever view is on screen when the PATCH lands — and
    // `setQueryData` marks the entry fresh, so the pane keeps showing the wrong
    // view's containers and the next pick PATCHes them onto it.
    const user = userEvent.setup();
    const qc = testClient();
    const a = gridView();
    mockFetchView.mockImplementation(async (_slug, id) => (id === "v-b" ? viewB() : a));
    let land: (updated: ViewSummary) => void = () => {};
    mockUpdateView.mockReturnValue(
      new Promise<ViewSummary>((resolve) => {
        land = resolve;
      }),
    );

    const { container } = renderRoute("/view/v-grid", qc);
    await screen.findByTestId("view-host");
    await pick(user, container, "c-seed", "terminal");
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("link", { name: "Open B" }));
    await waitFor(() =>
      expect(screen.getByTestId("view-host")).toHaveAttribute("data-view-id", "v-b"),
    );

    await act(async () => {
      land(view("v-grid", "flex_grid", {
        ...emptyGridLayout(),
        containers: { "c-seed": madeContainer("terminal") },
      }));
    });
    await settle();

    // The record went where it belongs, and nowhere else.
    expect(qc.getQueryData<ViewSummary>(viewsKeys.view(SLUG, "v-grid"))?.id).toBe("v-grid");
    expect(qc.getQueryData<ViewSummary>(viewsKeys.view(SLUG, "v-b"))?.id).toBe("v-b");
    // And the pane on screen is still B's: A's container arriving here is the
    // state from which the next pick PATCHes A's layout over B.
    expect(screen.getByTestId("view-host")).toHaveAttribute("data-view-id", "v-b");
    expect(container.querySelector('[data-container-id="c-b"]')).not.toBeNull();
    expect(container.querySelector('[data-container-id="c-seed"]')).toBeNull();
  });

  it("composes a second pick from the first one's result, not from the stale screen", async () => {
    // Two containers, two picks, one open request. PATCH replaces the layout
    // whole, so a body composed at click time from the layout on screen reverts
    // whatever the open PATCH was writing — and the server accepts it.
    const user = userEvent.setup();
    const two = view("v-two", "flex_grid", twoPaneLayout());
    mockFetchView.mockResolvedValue(two);
    let land: (updated: ViewSummary) => void = () => {};
    mockUpdateView.mockImplementationOnce(
      () =>
        new Promise<ViewSummary>((resolve) => {
          land = resolve;
        }),
    );
    mockUpdateView.mockImplementation(async (_slug, _viewId, patch) => ({
      ...two,
      ...(patch as Partial<ViewSummary>),
    }));

    const { container } = renderRoute("/view/v-two", testClient());
    await screen.findByTestId("view-host");

    await pick(user, container, "c-1", "terminal");
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await pick(user, container, "c-2", "web_embed");
    await settle();

    // Queued, not raced: the second write cannot compose a body until the first
    // one's record is in the cache.
    expect(mockUpdateView).toHaveBeenCalledTimes(1);

    await act(async () => {
      land(
        view("v-two", "flex_grid", {
          ...twoPaneLayout(),
          containers: {
            "c-1": madeContainer("terminal"),
            "c-2": { kind: "panel", settings: {} },
          },
        }),
      );
    });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(2));
    const second = (mockUpdateView.mock.calls[1][2] as { layout: Json }).layout;
    assertServerAcceptableLayout(second);
    const containers = second.containers as Record<string, Json>;
    // Both picks survive. Without serialization the second body carries the
    // unconfigured `c-1` the screen still showed, and the first pick is undone.
    expect(containers["c-1"]).toEqual(madeContainer("terminal"));
    expect(containers["c-2"]).toEqual(madeContainer("web_embed"));
  });
});

describe("one view's surface does not follow the user into the next", () => {
  it("opens an already-cached second view on its own untouched pane", async () => {
    // The pane components hold local state — the open primitive picker is one —
    // and two views put a `ContainerPane` in the same position of the same tree.
    // Reconciled rather than remounted, B inherits whatever A's pane was doing.
    // Seeding B into the cache is the whole point: with a loading frame between
    // the two records the surface unmounts on its own and the bug is invisible,
    // so this navigation has to go straight from A's record to B's.
    const user = userEvent.setup();
    const qc = testClient();
    const viewB = view("v-b", "flex_grid", {
      kind: "flex_grid",
      containers: { "c-b": { kind: "panel", settings: {} } },
      root: { node: "leaf", id: "n-b", size: 1, container_id: "c-b" },
    });
    qc.setQueryData(viewsKeys.view(SLUG, "v-b"), viewB);
    mockFetchView.mockImplementation(async (_slug, id) => (id === "v-b" ? viewB : loadedGridView));

    const { container } = renderRoute("/view/v-grid", qc);
    await screen.findByTestId("view-host");
    await user.click(promptIn(container, "c-seed"));
    // A's picker is genuinely open at the moment the user leaves.
    await primitiveOption(container, "terminal");

    await user.click(screen.getByRole("link", { name: "Open B" }));
    await waitFor(() =>
      expect(screen.getByTestId("view-host")).toHaveAttribute("data-view-id", "v-b"),
    );
    await settle();

    // B's own pane, and only B's.
    const pane = container.querySelector<HTMLElement>('[data-container-id="c-b"]');
    expect(pane).not.toBeNull();
    expect(container.querySelector('[data-container-id="c-seed"]')).toBeNull();
    // B is unconfigured, so it opens on the prompt — not on A's open picker,
    // which would offer to configure a container B has never heard of.
    expect(container.querySelector("[data-primitive-id]")).toBeNull();
    expect(within(pane as HTMLElement).getByRole("button", { name: /primitive/i })).toBeVisible();
  });
});

describe("a failed refresh does not take a working view down with it", () => {
  it("keeps the rendered view when a background refetch fails", async () => {
    // `refetchOnWindowFocus` is on: tabbing away and back re-reads. An error
    // branch checked before "is anything loaded" replaces a good record with a
    // full-page failure — and `retry: false` means it stays that way.
    const qc = testClient();
    renderRoute("/view/v-grid", qc);
    await screen.findByTestId("view-host");

    mockFetchView.mockRejectedValue(new ApiError(500, "Internal Server Error"));
    await act(async () => {
      await qc.refetchQueries({ queryKey: viewsKeys.view(SLUG, "v-grid") });
    });

    // Said, rather than swallowed: the pane is stale and the user is told so.
    await screen.findByTestId("view-refresh-failed");
    expect(screen.getByTestId("view-host")).toHaveAttribute("data-view-id", "v-grid");
    expect(screen.queryByTestId("view-load-failed")).toBeNull();
    expect(screen.getByTestId("view-host")).toHaveAttribute("data-view-kind", "flex_grid");
  });

  it("still reports a 404 that arrives on a refetch", async () => {
    // The other half of the same branch, and it is not symmetric: a 404 says
    // the view the user is looking at has just been deleted, and keeping it on
    // screen behind a banner is a lie.
    const qc = testClient();
    renderRoute("/view/v-grid", qc);
    await screen.findByTestId("view-host");

    mockFetchView.mockRejectedValue(new ApiError(404, "View not found"));
    await act(async () => {
      await qc.refetchQueries({ queryKey: viewsKeys.view(SLUG, "v-grid") });
    });

    expect(await notFound()).toBeVisible();
    expect(screen.queryByTestId("view-host")).toBeNull();
  });
});

describe("a record that loaded is still drawn as something", () => {
  it("gives an empty canvas an empty state rather than a blank screen", async () => {
    // What `New View → Canvas` creates. Drawn as literally nothing, it is a
    // screen with no text, no control and no way to tell a new view from a
    // broken one — the blank AC4 rules out, reached through the success path.
    mockFetchView.mockResolvedValue(canvasView());

    renderRoute("/view/v-canvas");

    const host = await screen.findByTestId("view-host");
    expect(within(host).getByTestId("view-canvas-empty")).toBeVisible();
    expect((host.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("explains a record whose layout is not a layout", async () => {
    mockFetchView.mockResolvedValue({
      ...gridView(),
      layout: null,
    } as unknown as ViewSummary);

    renderRoute("/view/v-grid");

    const region = await screen.findByTestId("view-undrawable");
    expect((region.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("explains a record whose kind has no renderer", async () => {
    mockFetchView.mockResolvedValue({
      ...gridView(),
      kind: "",
    } as unknown as ViewSummary);

    renderRoute("/view/v-grid");

    expect(await screen.findByTestId("view-undrawable")).toBeVisible();
  });
});

describe("the workspace the page reads its views from", () => {
  it("waits rather than telling the user to pick one before the chrome knows", async () => {
    // A deep-link reload with `uiStore.workspace` still `"all"`: the slug is
    // empty because the workspace list has not landed, not because there is no
    // workspace. This route has no picker on it, so "pick a workspace" is
    // advice the user cannot act on.
    render(
      <QueryClientProvider client={testClient()}>
        <SidebarWorkspaceProvider slug="" isResolved={false}>
          <MemoryRouter initialEntries={["/view/v-grid"]}>
            <Routes>
              <Route path="/view/:viewId" element={<ViewPage />} />
            </Routes>
          </MemoryRouter>
        </SidebarWorkspaceProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByText(/pick a workspace/i)).toBeNull();
    await settle();
    expect(mockFetchView).not.toHaveBeenCalled();
  });

  it("asks for one once the chrome has answered", async () => {
    render(
      <QueryClientProvider client={testClient()}>
        <SidebarWorkspaceProvider slug="" isResolved>
          <MemoryRouter initialEntries={["/view/v-grid"]}>
            <Routes>
              <Route path="/view/:viewId" element={<ViewPage />} />
            </Routes>
          </MemoryRouter>
        </SidebarWorkspaceProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText(/pick a workspace/i)).toBeInTheDocument();
    await settle();
    expect(mockFetchView).not.toHaveBeenCalled();
  });
});
