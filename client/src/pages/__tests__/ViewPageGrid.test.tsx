/**
 * What the flex-grid renderer *does* once more than one pane can exist:
 * splitting, resizing, closing, and the writes those make.
 *
 * `ViewPage.test.tsx` pins the host — the route, the not-found state, the seed
 * grid's first frame. The fixtures, the fake server and the DOM contract these
 * tests read the renderer back through are `test/gridHarness`, shared with
 * `ViewPageGridRaces.test.tsx`, which drives the same page against the races
 * its writes lose rather than against the criteria below.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/http";
import { createQueryClient } from "../../api/queryClient";
import { CONTAINER_PRIMITIVES, getPrimitive } from "../../components/views/primitives/registry";
import type { ViewSummary } from "../../lib/viewsApi";
import { useToastStore } from "../../state/toastStore";
import {
  MIN_PANE_PX,
  SLUG,
  POINTER_ID,
  RECT,
  chainLayout,
  childrenOf,
  containersOf,
  control,
  divider,
  dragDivider,
  gridNode,
  gridRoute,
  installGridHarness,
  lastLayout,
  leafLayout,
  mockUpdateView,
  nestedLayout,
  pairLayout,
  panelContainer,
  pointerDown,
  pointerMove,
  pointerUp,
  renderGrid,
  setMeasuredRect,
  settle,
  sizeOf,
  storePatch,
  storedView,
  tallLayout,
  terminalContainer,
  testClient,
  tripleLayout,
  wideLayout,
  type Json,
} from "../../test/gridHarness";
import {
  MAX_CONTAINERS,
  MAX_LAYOUT_NODES,
  MAX_SPLIT_DEPTH,
  assertServerAcceptableLayout,
} from "../../test/viewLayoutContract";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

/**
 * The terminal shell, faked at the one seam the grid does not own.
 *
 * AC8 is about a terminal *surviving* a layout change, and "survived" means
 * "was not unmounted and remounted". A real `TerminalPanel` opens a websocket
 * and an xterm instance, neither of which says anything about remounting; a
 * fake that mints a session id on mount says it directly and counts.
 */
const mockTerminalSessions = { mounts: 0, unmounts: 0 };

function MockTerminalPanel({ workspaceSlug }: { workspaceSlug: string }) {
  // Lazy initialiser, so the id is minted exactly once per mount — a remount is
  // a new id, which is the whole assertion.
  const [session] = useState(() => {
    mockTerminalSessions.mounts += 1;
    return `session-${mockTerminalSessions.mounts}`;
  });
  useEffect(() => {
    return () => {
      mockTerminalSessions.unmounts += 1;
    };
  }, []);
  return (
    <div data-testid="fake-terminal" data-session={session}>
      {`${workspaceSlug} scrollback for ${session}`}
    </div>
  );
}

jest.mock("../../components/TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: (props: { workspaceSlug: string }) => (
    <MockTerminalPanel workspaceSlug={props.workspaceSlug} />
  ),
}));

installGridHarness();

beforeEach(() => {
  mockTerminalSessions.mounts = 0;
  mockTerminalSessions.unmounts = 0;
});

describe("AC1 — a container splits horizontally and vertically, arbitrarily deep", () => {
  it("puts the split controls on each container rather than in one toolbar", async () => {
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");

    // One pair per leaf, each inside that leaf's own node element. A global
    // toolbar has no leaf to be inside, and one pair for two panes cannot say
    // which pane it splits.
    expect(container.querySelectorAll('button[data-grid-action="split-horizontal"]')).toHaveLength(
      2,
    );
    expect(container.querySelectorAll('button[data-grid-action="split-vertical"]')).toHaveLength(2);
    for (const nodeId of ["n-1", "n-2"]) {
      expect(control(container, nodeId, "split-horizontal")).toBeInstanceOf(HTMLButtonElement);
      // A control nobody can name is not operable by keyboard or screen reader.
      // Which words it uses is not pinned — no acceptance criterion picks them.
      expect(control(container, nodeId, "split-horizontal")).toHaveAccessibleName();
      expect(control(container, nodeId, "split-vertical")).toHaveAccessibleName();
    }
  });

  it("replaces the split leaf with a split of two halves and one new container", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "split-horizontal"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const [slug, viewId, patch] = mockUpdateView.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(viewId).toBe("v-grid");
    // `extra="forbid"`: a body carrying the title or the kind alongside is a 422.
    expect(Object.keys(patch as Json)).toEqual(["layout"]);

    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const root = layout.root as Json;
    expect(root.node).toBe("split");
    expect(root.orientation).toBe("horizontal");
    // The split stands where the leaf stood, so it inherits the leaf's slot —
    // and this leaf was the root, whose size is 1.0 by server rule.
    expect(root.size).toBe(1);

    const children = childrenOf(root);
    expect(children).toHaveLength(2);
    // Halves *of the parent slot*, which is what the sibling-sum rule measures.
    expect(children[0].size).toBeCloseTo(0.5, 6);
    expect(children[1].size).toBeCloseTo(0.5, 6);
    // The pane the user split keeps its contents and stays first; the pane that
    // appears is the new one.
    expect(children[0].container_id).toBe("c-seed");
    const added = children[1].container_id as string;
    expect(added).not.toBe("c-seed");

    const containers = containersOf(layout);
    expect(Object.keys(containers)).toHaveLength(2);
    expect(containers["c-seed"]).toEqual(panelContainer());
    // Unconfigured, so the new pane opens on the primitive prompt.
    expect(containers[added]).toEqual(panelContainer());
  });

  it("writes the orientation the control names", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "split-vertical"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    // `SplitOrientation` is horizontal/vertical — not row/column, which is a 422.
    expect((layout.root as Json).orientation).toBe("vertical");
  });

  it("nests inside an existing split without disturbing its siblings", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(nestedLayout());
    await screen.findByTestId("view-host");

    // `n-2` already sits two levels down, inside the 0.4-wide inner split.
    await user.click(control(container, "n-2", "split-horizontal"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);

    const root = layout.root as Json;
    const outer = childrenOf(root);
    expect(outer[0].size).toBeCloseTo(0.6, 6);
    expect(outer[1].size).toBeCloseTo(0.4, 6);
    expect(outer[0].container_id).toBe("c-1");

    const inner = childrenOf(outer[1]);
    // The split took `n-2`'s place and its size; `n-3` never moved.
    expect(inner[0].node).toBe("split");
    expect(inner[0].size).toBeCloseTo(0.5, 6);
    expect(inner[1].container_id).toBe("c-3");
    expect(inner[1].size).toBeCloseTo(0.5, 6);
    expect(childrenOf(inner[0])[0].container_id).toBe("c-2");
    expect(Object.keys(containersOf(layout))).toHaveLength(4);
  });

  it("refuses to send a split deeper than the server accepts", async () => {
    const user = userEvent.setup();
    const atCap = chainLayout(MAX_SPLIT_DEPTH);
    // The fixture is at the cap, not past it — otherwise the refusal below
    // would be right for the wrong reason.
    assertServerAcceptableLayout(atCap);

    const { container } = renderGrid(atCap, createQueryClient());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-tail", "split-horizontal"));
    await settle();

    // Splitting the deepest leaf would put a split at depth 32, which
    // `parse_view_layout` rejects. The failure the ticket rules out is a PATCH
    // that comes back 400 as a silent autosave.
    expect(mockUpdateView).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0);
  });

  it("refuses to send a split that would mint a container past the registry's cap", async () => {
    // Depth is not the only bound the server puts on a grid, and a split grows
    // three of them at once. A renderer that checks only the depth sends the
    // 257th container and takes the refusal as a 400 on a silent autosave.
    const user = userEvent.setup();
    const atCap = wideLayout(MAX_CONTAINERS);
    assertServerAcceptableLayout(atCap);

    const { container } = renderGrid(atCap, createQueryClient());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-0", "split-horizontal"));
    await settle();

    expect(mockUpdateView).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0);
  });

  it("refuses to send a split that would push the layout past the node cap", async () => {
    // The other cardinality bound, and it is reached first by a tree that is
    // mostly interior splits: this one has a container to spare and no nodes.
    const user = userEvent.setup();
    // `1 + 2n` nodes for `n` panes, so `n` is the most that fits under the cap.
    const atCap = tallLayout(Math.floor((MAX_LAYOUT_NODES - 1) / 2));
    assertServerAcceptableLayout(atCap);

    const { container } = renderGrid(atCap, createQueryClient());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-0", "split-horizontal"));
    await settle();

    expect(mockUpdateView).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts.length).toBeGreaterThan(0);
  });
});

describe("AC2 — dragging a divider resizes its neighbours, and proportions survive a resize", () => {
  it("writes the fractions the drag ended on", async () => {
    /*
     * What this does *not* measure: pixels. jsdom lays nothing out, so no pane
     * has a width here and none of them change when a grow factor does. What is
     * measurable, and is the whole of the stored consequence, is the arithmetic:
     * with a 1000px-wide split (RECT) and a divider released at clientX 700, the
     * pane on the left owns 0.7 of the slot and its neighbour 0.3.
     */
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");

    dragDivider(divider(container, "n-root"), "x", 500, 700);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const children = childrenOf(layout.root as Json);
    // Slack for a divider's own thickness, which the renderer may or may not
    // subtract from the track; not enough slack to confuse 0.7 with 0.5.
    expect(children[0].size as number).toBeCloseTo(0.7, 2);
    expect(children[1].size as number).toBeCloseTo(0.3, 2);
    expect(children[0].container_id).toBe("c-1");
  });

  it("resizes along the axis a vertical split runs in", async () => {
    const { container } = renderGrid(pairLayout([0.5, 0.5], "vertical"));
    await screen.findByTestId("view-host");

    // RECT is 800 tall, so 560 is the 0.7 mark on the cross axis.
    dragDivider(divider(container, "n-root"), "y", 400, 560);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(childrenOf(layout.root as Json)[0].size as number).toBeCloseTo(0.7, 2);
  });

  it("moves only the two panes the divider is between", async () => {
    const { container } = renderGrid(tripleLayout());
    await screen.findByTestId("view-host");

    // The second gap, between the 0.25 and the 0.25. The first pane is not
    // adjacent to it and must not pay for the move.
    dragDivider(divider(container, "n-root", 1), "x", 750, 850);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const children = childrenOf(layout.root as Json);
    expect(children[0].size as number).toBeCloseTo(0.5, 6);
    expect(children[1].size as number).toBeCloseTo(0.35, 2);
    expect(children[2].size as number).toBeCloseTo(0.15, 2);
  });

  it("holds its proportions when the window changes size", async () => {
    const { container } = renderGrid(pairLayout([0.3, 0.7]));
    await screen.findByTestId("view-host");
    expect(sizeOf(container, "n-1")).toBeCloseTo(0.3, 6);

    // The viewport shrinks. Fractions are fractions: nothing is rewritten, and
    // nothing is re-measured into pixels that would then drift.
    setMeasuredRect({ ...RECT, width: 600, right: 600 } as DOMRect);
    await act(async () => {
      fireEvent(window, new Event("resize"));
    });

    expect(sizeOf(container, "n-1")).toBeCloseTo(0.3, 6);
    expect(sizeOf(container, "n-2")).toBeCloseTo(0.7, 6);
    expect(mockUpdateView).not.toHaveBeenCalled();
  });
});

describe("AC3 — closing a container collapses its parent and redistributes the space", () => {
  it("renormalizes the survivors when more than two share the split", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(tripleLayout());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-1", "close"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    // The orphan check is the reason the container key has to go with the node:
    // a container nothing references is a 422, not a harmless leftover.
    assertServerAcceptableLayout(layout);
    expect(Object.keys(containersOf(layout)).sort()).toEqual(["c-2", "c-3"]);

    const root = layout.root as Json;
    expect(root.node).toBe("split");
    const children = childrenOf(root);
    expect(children).toHaveLength(2);
    // 0.25 and 0.25 were the survivors; the freed 0.5 is shared in proportion.
    expect(children[0].size as number).toBeCloseTo(0.5, 6);
    expect(children[1].size as number).toBeCloseTo(0.5, 6);
    expect(children.map((child) => child.container_id)).toEqual(["c-2", "c-3"]);
  });

  it("collapses the split when two were left, and gives the survivor its slot", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(nestedLayout());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-2", "close"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(Object.keys(containersOf(layout)).sort()).toEqual(["c-1", "c-3"]);

    const children = childrenOf(layout.root as Json);
    expect(children).toHaveLength(2);
    // A split with one child left is not a split. The survivor is promoted into
    // the slot the split occupied — 0.4 — rather than keeping the 0.5 it held
    // *inside* it, which would leave the root's children summing to 1.1.
    expect(children[1].node).toBe("leaf");
    expect(children[1].container_id).toBe("c-3");
    expect(children[1].size as number).toBeCloseTo(0.4, 6);
    expect(children[0].size as number).toBeCloseTo(0.6, 6);
  });

  it("promotes a survivor to the root at exactly 1.0", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(pairLayout([0.3, 0.7]));
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-1", "close"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    // The root's size is not a default a caller may override: 0.7 here is a 400,
    // and it is exactly what "the survivor inherits the parent's size" produces
    // if the parent that collapsed is not consulted.
    assertServerAcceptableLayout(layout);
    const root = layout.root as Json;
    expect(root.node).toBe("leaf");
    expect(root.container_id).toBe("c-2");
    expect(root.size).toBe(1);
    expect(Object.keys(containersOf(layout))).toEqual(["c-2"]);
  });

  it("resets the last container instead of deleting the root", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(terminalContainer()));
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "close"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    // A grid needs a root and a leaf root needs a real container, so there is no
    // "empty grid" to write. The empty *container* is the state the pane already
    // renders as the primitive prompt.
    assertServerAcceptableLayout(layout);
    const containers = containersOf(layout);
    expect(Object.keys(containers)).toHaveLength(1);
    expect(Object.values(containers)[0]).toEqual(panelContainer());
    expect((layout.root as Json).node).toBe("leaf");
  });

  it("leaves the user on the primitive prompt once that write lands", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(terminalContainer()));
    await screen.findByTestId("view-host");
    expect(screen.getByTestId("fake-terminal")).toBeVisible();

    await user.click(control(container, "n-seed", "close"));

    // Not a blank view, and not a view with no way back into it. Asked
    // structurally rather than by the prompt's wording: the header's own
    // primitive control is a second button whose name may well read
    // "primitive" too, and a name query that matches both throws on the
    // ambiguity instead of failing on the behaviour. `ContainerPane` marks the
    // configured pane with `data-primitive-id` and the prompt without it.
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const prompt = await waitFor(() => {
      const found = container.querySelector<HTMLElement>(
        "[data-container-id]:not([data-primitive-id])",
      );
      if (found === null) throw new Error("The pane is not showing the empty-container prompt");
      return found;
    });
    // The prompt's own control, inside the pane rather than in the header.
    expect(within(prompt).getByRole("button")).toBeVisible();
    expect(screen.queryByTestId("fake-terminal")).toBeNull();
    expect(container.querySelectorAll("[data-container-id]")).toHaveLength(1);
  });
});

describe("AC4 — contents are chosen and changed through the registry picker", () => {
  it("names a configured container by what the registry calls it", async () => {
    const { container } = renderGrid(leafLayout(terminalContainer()));
    await screen.findByTestId("view-host");

    const name = getPrimitive("terminal")?.displayName;
    expect(name).toBeDefined();
    // Read from the registry rather than spelled here: a header that hardcoded
    // "Terminal" would pass a test that hardcoded it too, and would go stale the
    // moment the entry is renamed.
    expect(within(gridNode(container, "n-seed")).getByText(name as string)).toBeVisible();
  });

  it("changes an already-configured container through the same picker", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(terminalContainer()));
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "pick-primitive"));
    // Every option comes from the registry, so a picker offering a hardcoded
    // subset fails here rather than three renames later. `button[…]`, not
    // `[…]`: `ContainerPrimitiveHost` puts `data-primitive-id` on the pane it
    // mounts too, so the bare selector counts the configured pane as a fourth
    // option and this assertion would be off by one against a correct picker.
    await waitFor(() =>
      expect(container.querySelectorAll("button[data-primitive-id]").length).toBe(
        CONTAINER_PRIMITIVES.length,
      ),
    );
    const option = container.querySelector<HTMLElement>('button[data-primitive-id="web_embed"]');
    expect(option).not.toBeNull();
    await user.click(option as HTMLElement);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const container_ = Object.values(containersOf(layout))[0];
    // Replaced, not merged: a `web_embed` primitive left under `kind: "terminal"`
    // is a disagreement `ContainerPrimitiveHost` refuses to mount.
    expect(container_.kind).toBe("web_embed");
    expect((container_.settings as Json).primitive_id).toBe("web_embed");
    expect((layout.root as Json).container_id).toBe("c-seed");
  });

  it("draws an unresolvable primitive as the registry's placeholder, inside its own leaf", async () => {
    // The grid names no primitive of its own, so a container asking for one this
    // build does not have reaches the registry's placeholder — a grid that
    // imported a primitive directly would have something else to fall back to.
    //
    // Asserted *inside* the leaf, and alongside the leaf's own controls,
    // because the placeholder alone is `ContainerPane`'s existing behaviour and
    // says nothing about this renderer: a pane whose contents cannot be drawn
    // must still be one the user can close or re-point, or a view with one bad
    // container is a view with no way out of it.
    const { container } = renderGrid(
      leafLayout({ kind: "panel", settings: { primitive_id: "not-a-primitive" } }),
    );
    await screen.findByTestId("view-host");

    const leaf = gridNode(container, "n-seed");
    expect(leaf.querySelector("[data-primitive-unknown]")).not.toBeNull();
    expect(screen.queryByTestId("fake-terminal")).toBeNull();
    expect(control(container, "n-seed", "close")).toHaveAccessibleName();
    expect(control(container, "n-seed", "pick-primitive")).toHaveAccessibleName();
  });
});

describe("AC5 — layout edits persist, and a resize commits once, on drag end", () => {
  it("sends nothing while the pointer is moving and one PATCH when it stops", async () => {
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");

    pointerDown(handle, "x", 500);
    pointerMove(handle, "x", 540);
    pointerMove(handle, "x", 600);
    pointerMove(handle, "x", 660);
    pointerMove(handle, "x", 700);
    await settle();

    // A PATCH per pointermove is the bug this criterion names outright.
    expect(mockUpdateView).not.toHaveBeenCalled();

    pointerUp(handle, "x", 700);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(mockUpdateView).toHaveBeenCalledTimes(1);
  });

  it("does not snap back while the save is still in flight", async () => {
    /*
     * There is no optimistic update in the codebase today: on pointerup a
     * renderer that drops its drag state re-renders from the unchanged cache and
     * the panes jump back to 0.5/0.5 until the PATCH lands. That jump is what
     * the ticket rules out, and it is only visible while the request is open.
     */
    let land: (updated: ViewSummary) => void = () => {};
    mockUpdateView.mockImplementation(
      (_slug, _viewId, patch) =>
        new Promise<ViewSummary>((resolve) => {
          land = (updated) => resolve(updated);
          storePatch(patch);
        }),
    );

    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");

    dragDivider(divider(container, "n-root"), "x", 500, 700);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();

    expect(sizeOf(container, "n-1")).toBeCloseTo(0.7, 2);
    expect(sizeOf(container, "n-2")).toBeCloseTo(0.3, 2);

    await act(async () => {
      land(storedView());
    });
    // And the server's record agrees with what was on screen the whole time.
    expect(sizeOf(container, "n-1")).toBeCloseTo(0.7, 2);
  });

  it("survives a reload of the same view", async () => {
    const user = userEvent.setup();
    const first = renderGrid(leafLayout());
    await screen.findByTestId("view-host");
    await user.click(control(first.container, "n-seed", "split-horizontal"));
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    first.unmount();

    // A cold mount, a cold cache, and the same id: the split came back from the
    // server rather than from a client-side memory of it.
    const { container } = render(gridRoute(testClient()));
    await screen.findByTestId("view-host");
    await waitFor(() => expect(container.querySelectorAll("[data-container-id]")).toHaveLength(2));
    expect(container.querySelectorAll("[data-grid-divider]")).toHaveLength(1);
  });
});

describe("AC6 — a failed layout save is said out loud", () => {
  it("toasts a rejected split through the app's own mutation path", async () => {
    // The real client, because the toast comes from `MutationCache.onError` plus
    // the mutation's `meta.errorTitle`. Against a bare `QueryClient` this
    // assertion would pass for a write that raises nothing at all.
    const user = userEvent.setup();
    mockUpdateView.mockRejectedValue(new ApiError(400, "Layout is malformed"));

    const { container } = renderGrid(leafLayout(), createQueryClient());
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "split-horizontal"));

    await waitFor(() => expect(useToastStore.getState().toasts.length).toBeGreaterThan(0));
    const [toast] = useToastStore.getState().toasts;
    expect((toast.message ?? "").length + (toast.title ?? "").length).toBeGreaterThan(0);
  });

  it("keeps the dragged sizes on screen after the save fails", async () => {
    mockUpdateView.mockRejectedValue(new ApiError(500, "Internal Server Error"));

    const { container } = renderGrid(pairLayout(), createQueryClient());
    await screen.findByTestId("view-host");

    dragDivider(divider(container, "n-root"), "x", 500, 700);
    await waitFor(() => expect(useToastStore.getState().toasts.length).toBeGreaterThan(0));
    await settle();

    // Reverting silently is the failure mode the ticket names: the drag is the
    // user's intent, and throwing it away without a word looks like a UI bug
    // rather than a server one.
    expect(sizeOf(container, "n-1")).toBeCloseTo(0.7, 2);
    expect(await screen.findByTestId("view-host")).toBeVisible();
  });
});

describe("AC7 — a divider drag does not select text or reach the pane beneath", () => {
  it("swallows the default action of the press that starts it", async () => {
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");

    // `fireEvent` returns false when a handler called `preventDefault`. Without
    // it the browser starts a native text selection (or an image drag) that runs
    // for the whole gesture.
    expect(pointerDown(handle, "x", 500)).toBe(false);
    // Belt and braces on the same failure, wherever the renderer puts it: a
    // handle that is not selectable, or a body that stops being selectable for
    // the duration of the drag. Both are real answers.
    const suppressed =
      handle.style.userSelect === "none" || document.body.style.userSelect === "none";
    expect(suppressed).toBe(true);
    pointerUp(handle, "x", 500);
  });

  it("captures the pointer so the gesture never lands on a pane", async () => {
    const capture = jest.spyOn(Element.prototype, "setPointerCapture");
    const release = jest.spyOn(Element.prototype, "releasePointerCapture");
    const { container } = renderGrid(
      pairLayout([0.5, 0.5], "horizontal", [terminalContainer(), panelContainer()]),
    );
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");

    pointerDown(handle, "x", 500);
    // Capture is what keeps a pointermove over the terminal from reaching the
    // terminal — the whole of "does not steal pointer events from a terminal
    // underneath it", and the only part of it jsdom can be asked about.
    expect(capture).toHaveBeenCalledWith(POINTER_ID);
    expect(capture.mock.instances[0]).toBe(handle);

    pointerMove(handle, "x", 700);
    pointerUp(handle, "x", 700);
    expect(release).toHaveBeenCalledWith(POINTER_ID);
    capture.mockRestore();
    release.mockRestore();
  });
});

describe("AC8 — a terminal survives the layout changing around it", () => {
  it("is not remounted when a sibling pane splits", async () => {
    /*
     * The hazard is reparenting, not resizing. Splitting `n-2` inserts a node
     * *above* that leaf; a renderer that rebuilds the tree — keys by index,
     * remounts a subtree, or re-creates the element that wraps every pane —
     * hands the terminal a brand-new shell with no scrollback in it, and the
     * user's session is gone. Mount count is the direct question.
     */
    const user = userEvent.setup();
    const { container } = renderGrid(
      pairLayout([0.5, 0.5], "horizontal", [terminalContainer(), panelContainer()]),
    );
    await screen.findByTestId("view-host");
    const before = screen.getByTestId("fake-terminal").getAttribute("data-session");
    expect(mockTerminalSessions.mounts).toBe(1);

    await user.click(control(container, "n-2", "split-horizontal"));
    await waitFor(() => expect(container.querySelectorAll("[data-container-id]")).toHaveLength(3));
    await settle();

    expect(mockTerminalSessions.mounts).toBe(1);
    expect(mockTerminalSessions.unmounts).toBe(0);
    // The same shell, still holding what it had printed.
    expect(screen.getByTestId("fake-terminal")).toHaveAttribute("data-session", before as string);
    expect(screen.getByTestId("fake-terminal")).toHaveTextContent(before as string);
  });

  it("is not remounted when an earlier sibling closes and its index shifts", async () => {
    /*
     * The split test above cannot catch the commonest form of this bug on its
     * own. Splitting a leaf never moves its siblings, so a renderer that keys
     * children by array index survives it — every pane is still at the index it
     * was. Closing one *does* move them: the terminal at index 2 becomes the
     * child at index 1, and an index-keyed list hands it the previous
     * occupant's instance, i.e. a fresh terminal with no scrollback. Keying by
     * node id is what makes the pane survive, and only this shape asks for it.
     */
    const user = userEvent.setup();
    const { container } = renderGrid(
      tripleLayout([panelContainer(), panelContainer(), terminalContainer()]),
    );
    await screen.findByTestId("view-host");
    const before = screen.getByTestId("fake-terminal").getAttribute("data-session");
    expect(mockTerminalSessions.mounts).toBe(1);

    await user.click(control(container, "n-1", "close"));
    await waitFor(() => expect(container.querySelectorAll("[data-container-id]")).toHaveLength(2));
    await settle();

    expect(mockTerminalSessions.mounts).toBe(1);
    expect(mockTerminalSessions.unmounts).toBe(0);
    expect(screen.getByTestId("fake-terminal")).toHaveAttribute("data-session", before as string);
    expect(screen.getByTestId("fake-terminal")).toHaveTextContent(before as string);
  });

  it("is not remounted by a drag on the divider beside it", async () => {
    const { container } = renderGrid(
      pairLayout([0.5, 0.5], "horizontal", [terminalContainer(), panelContainer()]),
    );
    await screen.findByTestId("view-host");
    const before = screen.getByTestId("fake-terminal").getAttribute("data-session");

    dragDivider(divider(container, "n-root"), "x", 500, 700);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();

    expect(mockTerminalSessions.mounts).toBe(1);
    expect(screen.getByTestId("fake-terminal")).toHaveAttribute("data-session", before as string);
  });

  it("gives the pane a size to observe rather than a size to obey", async () => {
    /*
     * The PTY half of this criterion is `TerminalPanel`'s own `ResizeObserver`,
     * and jsdom implements neither that nor layout — so no test here can watch a
     * PTY resize. What the grid owes it is a box that is free to change: a
     * fractional grow factor, no pixel width or height nailed on, and
     * `min-width: 0` so a flex child can shrink below its content at all. A
     * pane sized in pixels, or one that cannot shrink, is how a terminal ends up
     * clipping instead of reflowing.
     */
    const { container } = renderGrid(
      pairLayout([0.5, 0.5], "horizontal", [terminalContainer(), panelContainer()]),
    );
    await screen.findByTestId("view-host");

    const pane = gridNode(container, "n-1");
    expect(Number.parseFloat(pane.style.flexGrow)).toBeCloseTo(0.5, 6);
    expect(pane.style.width).toBe("");
    expect(pane.style.height).toBe("");
    expect(pane.style.minWidth).toMatch(/^0(px)?$/);
    expect(pane.style.minHeight).toMatch(/^0(px)?$/);
  });
});

describe("AC9 — resize and close from the keyboard, and no pane becomes a sliver", () => {
  it("moves a divider with the arrow keys", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");

    // Reachable at all: a handle with no tabindex can be dragged and nothing else.
    expect(handle.tabIndex).toBeGreaterThanOrEqual(0);
    handle.focus();
    expect(document.activeElement).toBe(handle);

    await user.keyboard("{ArrowRight>4/}");

    // The pane on the left grew, and the tree still renders the fractions.
    expect(sizeOf(container, "n-1")).toBeGreaterThan(0.5);
    expect(sizeOf(container, "n-1") + sizeOf(container, "n-2")).toBeCloseTo(1, 6);
  });

  it("commits a keyboard resize once, not once per keystroke", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");
    const handle = divider(container, "n-root");
    handle.focus();

    // One press held down through four repeats: four keydowns, one keyup. A
    // renderer that writes on keydown sends four PATCHes for one adjustment.
    await user.keyboard("{ArrowRight>4/}");
    await settle();
    expect(mockUpdateView.mock.calls.length).toBeLessThanOrEqual(1);

    // Committing on keyup and committing on blur are both defensible; sending a
    // request per keystroke is not, and neither is never sending one.
    fireEvent.blur(handle);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(mockUpdateView).toHaveBeenCalledTimes(1);
    assertServerAcceptableLayout(lastLayout());
  });

  it("closes a container from the keyboard", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");

    const close = control(container, "n-1", "close");
    expect(close).toHaveAccessibleName();
    close.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect((layout.root as Json).container_id).toBe("c-2");
  });

  it("clamps a drag that would leave a sliver", async () => {
    /*
     * The clamp belongs in the drag arithmetic, not in a CSS `min-width`: a CSS
     * floor stops the pane shrinking on screen while the stored fraction keeps
     * falling, so what is rendered and what is stored quietly disagree — and the
     * next reload snaps to the stored one.
     */
    const { container } = renderGrid(pairLayout());
    await screen.findByTestId("view-host");

    // All the way into the right-hand edge of a 1000px track.
    dragDivider(divider(container, "n-root"), "x", 500, 1004);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const children = childrenOf(layout.root as Json);
    const smallest = Math.min(children[0].size as number, children[1].size as number);
    // The exact floor is the renderer's to choose; anything thinner than
    // `MIN_PANE_PX` of a 1000px track is a sliver by any of them, and `> 0`
    // alone is only the server's rule, not a usable pane.
    expect(smallest * RECT.width).toBeGreaterThanOrEqual(MIN_PANE_PX);
    expect(children[0].size as number).toBeGreaterThan(children[1].size as number);

    // And what the screen shows is the fraction that was stored — the clamp did
    // not happen in CSS on top of a smaller stored value.
    expect(sizeOf(container, "n-2")).toBeCloseTo(children[1].size as number, 6);
    expect(gridNode(container, "n-2").style.minWidth).toMatch(/^0(px)?$/);
  });
});
