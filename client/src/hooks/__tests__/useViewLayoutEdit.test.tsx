/**
 * The layout write's identity, at the seam where it is actually lost.
 *
 * `useMutation` re-binds its options on every render — `MutationObserver`
 * re-applies them to a *pending* mutation as well — so a callback that reads the
 * view it is writing to from the render closure follows the screen instead of
 * the request. These tests drive that directly by re-rendering the hook with a
 * different view while a write is open, which is what a user switching tabs
 * does to whichever pane component React reuses.
 *
 * The page keys its renderers by view id, so it does not reach this state
 * through that path today. That is a second defence and not the fix: the hook is
 * the thing 440 and 442 will both mount, and it has to be correct on its own.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { newContainerFor } from "../../components/views/primitives/registry";
import { updateView, viewsKeys, type ViewSummary } from "../../lib/viewsApi";
import { useViewLayoutEdit, useViewLayoutWrite } from "../useViewLayoutEdit";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  updateView: jest.fn(),
}));

const mockUpdateView = updateView as jest.MockedFunction<typeof updateView>;

const SLUG = "loregarden";

type Json = Record<string, unknown>;

function gridLayout(containerId: string): Json {
  return {
    kind: "flex_grid",
    containers: { [containerId]: { kind: "panel", settings: {} } },
    root: { node: "leaf", id: `n-${containerId}`, size: 1, container_id: containerId },
  };
}

function view(id: string, layout: Json): ViewSummary {
  return {
    id,
    kind: "flex_grid",
    title: id,
    icon: "",
    layout,
    viewport: {},
    created_at: "2026-08-01T00:00:00",
    updated_at: "2026-08-01T00:00:00",
  };
}

/** One pane's worth of the hook: a control that picks, and nothing else. */
function Harness({ slug, viewId }: { slug: string; viewId: string }) {
  const pickPrimitive = useViewLayoutWrite(slug, viewId);
  return (
    <button type="button" onClick={() => pickPrimitive(`c-${viewId}`, "terminal")}>
      pick
    </button>
  );
}

function testClient(): QueryClient {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } },
  });
  qc.setQueryData(viewsKeys.view(SLUG, "v-a"), view("v-a", gridLayout("c-v-a")));
  qc.setQueryData(viewsKeys.view(SLUG, "v-b"), view("v-b", gridLayout("c-v-b")));
  return qc;
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("useViewLayoutWrite", () => {
  it("PATCHes the view it was handed, from that view's own layout", async () => {
    const user = userEvent.setup();
    mockUpdateView.mockImplementation(async (_slug, viewId) => view(viewId, gridLayout("c-v-a")));
    render(
      <QueryClientProvider client={testClient()}>
        <Harness slug={SLUG} viewId="v-a" />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "pick" }));

    expect(mockUpdateView).toHaveBeenCalledTimes(1);
    const [slug, viewId, patch] = mockUpdateView.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(viewId).toBe("v-a");
    // Partial, and only the layout: `ViewPatch` is `extra="forbid"` server-side.
    expect(Object.keys(patch)).toEqual(["layout"]);
    const containers = (patch.layout as Json).containers as Record<string, Json>;
    expect(containers["c-v-a"]).toEqual(newContainerFor("terminal"));
  });

  it("stores the result under the view it was issued against, not the one now on screen", async () => {
    const user = userEvent.setup();
    const qc = testClient();
    let land: (updated: ViewSummary) => void = () => {};
    mockUpdateView.mockReturnValue(
      new Promise<ViewSummary>((resolve) => {
        land = resolve;
      }),
    );

    const tree = render(
      <QueryClientProvider client={qc}>
        <Harness slug={SLUG} viewId="v-a" />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "pick" }));
    expect(mockUpdateView).toHaveBeenCalledTimes(1);

    // The user moves to another view while the PATCH is open. React re-renders
    // this component, and react-query re-applies the new options to the mutation
    // that is still in flight.
    tree.rerender(
      <QueryClientProvider client={qc}>
        <Harness slug={SLUG} viewId="v-b" />
      </QueryClientProvider>,
    );

    const written = view("v-a", {
      ...gridLayout("c-v-a"),
      containers: { "c-v-a": newContainerFor("terminal") as unknown as Json },
    });
    await act(async () => {
      land(written);
    });

    // A's record went into A's entry.
    expect(qc.getQueryData<ViewSummary>(viewsKeys.view(SLUG, "v-a"))).toEqual(written);
    // And B's entry still holds B. Overwriting it here is not a display glitch:
    // `setQueryData` marks the entry fresh, so B's pane renders A's containers
    // and the next pick PATCHes A's layout onto B, destroying it server-side.
    const b = qc.getQueryData<ViewSummary>(viewsKeys.view(SLUG, "v-b"));
    expect(b?.id).toBe("v-b");
    expect(b?.layout).toEqual(gridLayout("c-v-b"));
  });

  it("refuses to compose a PATCH for a view that is no longer loaded", async () => {
    // A queued write whose view was deleted meanwhile has no base layout. A
    // body built from nothing would store a view made of this one container.
    const user = userEvent.setup();
    const qc = testClient();
    qc.removeQueries({ queryKey: viewsKeys.view(SLUG, "v-a") });
    render(
      <QueryClientProvider client={qc}>
        <Harness slug={SLUG} viewId="v-a" />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "pick" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockUpdateView).not.toHaveBeenCalled();
  });
});

describe("useViewLayoutEdit — an edit that asks for nothing", () => {
  /** One control whose edit hands the layout straight back. */
  function NoOpHarness({ slug, viewId }: { slug: string; viewId: string }) {
    const edit = useViewLayoutEdit(slug, viewId);
    return (
      <button type="button" onClick={() => edit((layout) => layout)}>
        no-op
      </button>
    );
  }

  it("sends no request, and leaves the caches it would have disturbed alone", async () => {
    // The canvas reaches this on every click of the front-most container: raising
    // an item that is already at the front returns the layout untouched. Skipping
    // only the PATCH is not enough — resolving the mutation with the record it
    // already had would still cancel every in-flight read of this view and
    // refetch the sidebar's whole view list, so a click that sent nothing would
    // cost two requests instead of one.
    const user = userEvent.setup();
    const qc = testClient();
    const cancelQueries = jest.spyOn(qc, "cancelQueries");
    const invalidateQueries = jest.spyOn(qc, "invalidateQueries");

    render(
      <QueryClientProvider client={qc}>
        <NoOpHarness slug={SLUG} viewId="v-a" />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "no-op" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockUpdateView).not.toHaveBeenCalled();
    expect(cancelQueries).not.toHaveBeenCalled();
    expect(invalidateQueries).not.toHaveBeenCalled();
    // And the record it was composed from is still exactly the record.
    expect(qc.getQueryData<ViewSummary>(viewsKeys.view(SLUG, "v-a"))?.layout).toEqual(
      gridLayout("c-v-a"),
    );
  });

  it("leaves a pan that landed while the write was open in the cache", async () => {
    // The viewport is written by a separate mutation (480), so a settled pan can
    // commit between this PATCH committing server-side and its response landing
    // here. The record in hand still carries the position from before that pan,
    // and storing it whole would move the canvas back on the next open.
    const user = userEvent.setup();
    const qc = testClient();
    const key = viewsKeys.view(SLUG, "v-a");
    let land: (updated: ViewSummary) => void = () => {};
    mockUpdateView.mockReturnValue(
      new Promise<ViewSummary>((resolve) => {
        land = resolve;
      }),
    );

    render(
      <QueryClientProvider client={qc}>
        <Harness slug={SLUG} viewId="v-a" />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "pick" }));

    // The pan settles and its own write lands first.
    const panned = { ...qc.getQueryData<ViewSummary>(key)!, viewport: { pan_x: 80, pan_y: 40, zoom: 1 } };
    qc.setQueryData(key, panned);

    await act(async () => {
      // The server's answer to the *layout* PATCH, which knows nothing of the pan.
      land(view("v-a", gridLayout("c-v-a")));
    });

    expect(qc.getQueryData<ViewSummary>(key)?.viewport).toEqual({ pan_x: 80, pan_y: 40, zoom: 1 });
  });

  it("still writes when the edit rebuilt an equal layout, because that edit decided to", async () => {
    // Identity, not deep equality. A caller that returned a *new* object asked
    // for a write, and second-guessing it with a deep compare would silently drop
    // edits whose difference this hook cannot see.
    const user = userEvent.setup();
    mockUpdateView.mockImplementation(async (_slug, viewId) => view(viewId, gridLayout("c-v-a")));

    function RebuildHarness() {
      const edit = useViewLayoutEdit(SLUG, "v-a");
      return (
        <button type="button" onClick={() => edit((layout) => ({ ...layout }))}>
          rebuild
        </button>
      );
    }

    render(
      <QueryClientProvider client={testClient()}>
        <RebuildHarness />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "rebuild" }));

    expect(mockUpdateView).toHaveBeenCalledTimes(1);
  });
});
