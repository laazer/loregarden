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
import { useViewLayoutWrite } from "../useViewLayoutEdit";

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
