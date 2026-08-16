/**
 * What the canvas renderer *does*: placing, dragging, resizing, restacking, and
 * the writes those make.
 *
 * `ViewPage.test.tsx` pins the host — the route, the not-found state, the empty
 * canvas's first frame. The fixtures, the fake server and the DOM contract these
 * tests read the renderer back through are `test/canvasHarness`, whose header
 * also states what jsdom makes unaskable. The short version, because it bears on
 * how AC5 is covered here:
 *
 * **jsdom has no layout engine.** Nothing is measured below. AC5 — "at 100% zoom
 * every primitive is pixel-accurate and hit-testing lands on the correct element,
 * including inside embedded frames and terminals" — is covered here only as its
 * *structural cause*: that at `zoom === 1` the surface carries no CSS transform
 * at all, so there is no coordinate mapping and no rasterised layer between the
 * user and a live terminal or a cross-document iframe. Whether the glyphs are
 * then crisp and the click lands where it looks like it lands is browser
 * behaviour, and it is browser work to verify. This suite does not claim it.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "../../api/http";
import { MIN_ITEM_PX, REACHABLE_EXTENT } from "../../lib/canvasLayout";
import { MAX_ZOOM, readViewport } from "../../lib/canvasViewport";
import {
  SLUG,
  VIEW_ID,
  canvasItemEl,
  canvasRoute,
  containersOf,
  drag,
  dragHandle,
  drawnBox,
  emptyCanvas,
  itemAction,
  itemById,
  itemsOf,
  installCanvasHarness,
  lastLayout,
  mockUpdateView,
  oneItemCanvas,
  overlappingCanvas,
  pointerDown,
  pointerMove,
  pointerUp,
  renderCanvas,
  resizeHandle,
  settle,
  surfaceEl,
  terminalContainer,
  testClient,
  threeItemCanvas,
  toolbarAction,
  viewportEl,
  type Json,
} from "../../test/canvasHarness";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

/**
 * The terminal shell, faked at the one seam the canvas does not own.
 *
 * A real `TerminalPanel` opens a websocket and an xterm instance, neither of
 * which this page has an opinion about.
 */
jest.mock("../../components/TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: () => <div data-testid="fake-terminal" />,
}));

installCanvasHarness();

/**
 * The keyboard step, spelled here rather than imported from the renderer.
 *
 * No acceptance criterion names a number — "move and resize are operable by
 * keyboard" is the whole of it — so this is the step the tests expect and the
 * renderer is free to choose. Importing the constant would make every assertion
 * below agree with the implementation by construction.
 */
const KEY_STEP_PX = 8;

async function shown(layout: Json) {
  const rendered = renderCanvas(layout);
  await screen.findByTestId("view-host");
  return rendered;
}

describe("AC1 — containers are placed at a point, and may overlap", () => {
  it("draws each item where its stored geometry says, in the CSS that places it", async () => {
    const { container } = await shown(overlappingCanvas());

    expect(drawnBox(container, "i-1")).toMatchObject({ x: 100, y: 80, width: 400, height: 300 });
    // Overlapping by construction: i-2 starts inside i-1's box. That is the
    // canvas's difference from the grid, not a defect to design away.
    expect(drawnBox(container, "i-2")).toMatchObject({ x: 200, y: 140, width: 400, height: 300 });
  });

  it("places a container from the toolbar", async () => {
    const user = userEvent.setup();
    const { container } = await shown(emptyCanvas());

    await user.click(toolbarAction(container, "add-container"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const [slug, viewId, patch] = mockUpdateView.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(viewId).toBe(VIEW_ID);
    // `extra="forbid"`: a body carrying the title or the kind alongside is a 422.
    expect(Object.keys(patch as Json)).toEqual(["layout"]);

    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(itemsOf(layout)).toHaveLength(1);
    // One container, unconfigured, so the new item opens on the primitive prompt.
    expect(Object.values(containersOf(layout))).toEqual([{ kind: "panel", settings: {} }]);
  });

  it("places a container at the point a double-click landed on the bare surface", async () => {
    const { container } = await shown(emptyCanvas());

    // The stubbed rect puts the viewport's origin at client 0,0, so a click at
    // 500,400 is the surface point 500,400 — the item is centred on it.
    fireEvent.doubleClick(surfaceEl(container), { clientX: 500, clientY: 400 });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const placed = itemsOf(layout)[0];
    // Centred, so the point the user aimed at is inside the container they got.
    expect(Number(placed.x) + Number(placed.width) / 2).toBeCloseTo(500, 6);
    expect(Number(placed.y) + Number(placed.height) / 2).toBeCloseTo(400, 6);
  });

  it("does not place a container when the double-click was inside an existing one", async () => {
    const { container } = await shown(oneItemCanvas());

    fireEvent.doubleClick(canvasItemEl(container, "i-1"), { clientX: 200, clientY: 200 });

    await settle();
    expect(mockUpdateView).not.toHaveBeenCalled();
  });
});

describe("AC1 — dragging moves a container, and the write lands on gesture end", () => {
  it("stores the new position once, when the gesture ends", async () => {
    const { container } = await shown(oneItemCanvas());

    drag(dragHandle(container, "i-1"), [300, 300], [420, 360]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    // "Committed on gesture end rather than per pointer move" is the criterion,
    // and two intermediate moves is what makes the count meaningful.
    expect(mockUpdateView).toHaveBeenCalledTimes(1);
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(itemById(layout, "i-1")).toMatchObject({ x: 220, y: 140, width: 400, height: 300 });
  });

  it("follows the pointer while the gesture is open, before anything is written", async () => {
    const { container } = await shown(oneItemCanvas());
    const handle = dragHandle(container, "i-1");

    pointerDown(handle, 300, 300);
    pointerMove(handle, 350, 330);

    // Drawn from the local draft: there is no optimistic update in the write
    // path, so an item that waited for the PATCH would lag the cursor.
    expect(drawnBox(container, "i-1")).toMatchObject({ x: 150, y: 110 });
    expect(mockUpdateView).not.toHaveBeenCalled();

    pointerUp(handle, 350, 330);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
  });

  it("covers the surface while the gesture is open, so an embed cannot swallow it", async () => {
    // Pointer capture routes moves back to the handle, but an `<iframe>` is a
    // separate document and will take them anyway. The shield is what makes a
    // drag over a web embed survive — and it is absolutely positioned, never
    // fixed: a view is drawn inside a pane and owns no part of the screen
    // outside it. (`containerPrimitives.smallSize` enforces the second half
    // across every source under `components/views`.)
    const { container } = await shown(oneItemCanvas());
    const handle = dragHandle(container, "i-1");

    expect(container.querySelector("[data-canvas-shield]")).toBeNull();
    pointerDown(handle, 300, 300);
    const shield = container.querySelector<HTMLElement>("[data-canvas-shield]");
    expect(shield).not.toBeNull();
    expect(shield?.style.position).toBe("absolute");

    pointerUp(handle, 300, 300);
    expect(container.querySelector("[data-canvas-shield]")).toBeNull();
    await settle();
  });

  it("prevents the browser's own text selection when the gesture starts", async () => {
    const { container } = await shown(oneItemCanvas());

    // `fireEvent` returns false when a handler called `preventDefault`. Without
    // it the browser starts a native selection that runs for the whole drag.
    expect(pointerDown(dragHandle(container, "i-1"), 300, 300)).toBe(false);
  });

  it("ignores moves from a pointer that does not own the gesture", async () => {
    // A second finger crossing a handle that is already captured delivers moves
    // against a drag it is no part of.
    const { container } = await shown(oneItemCanvas());
    const handle = dragHandle(container, "i-1");

    pointerDown(handle, 300, 300);
    fireEvent.pointerMove(handle, { pointerId: 99, buttons: 1, clientX: 900, clientY: 900 });

    expect(drawnBox(container, "i-1")).toMatchObject({ x: 100, y: 80 });
  });

  it("does not start a drag from a press on a header button", async () => {
    // A press that lands on the close control must still be able to become a
    // click rather than a one-pixel move.
    const { container } = await shown(oneItemCanvas());

    pointerDown(itemAction(container, "i-1", "close"), 300, 300);
    pointerMove(dragHandle(container, "i-1"), 500, 500);

    expect(drawnBox(container, "i-1")).toMatchObject({ x: 100, y: 80 });
  });

  it("keeps a drag that was cancelled rather than reverting it without a word", async () => {
    const { container } = await shown(oneItemCanvas());
    const handle = dragHandle(container, "i-1");

    pointerDown(handle, 300, 300);
    pointerMove(handle, 380, 300);
    fireEvent.pointerCancel(handle, { pointerId: 7, clientX: 380, clientY: 300 });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(itemById(lastLayout(), "i-1")).toMatchObject({ x: 180 });
  });
});

describe("AC1 — resizing from edges and corners", () => {
  it("offers a handle on every edge and every corner", async () => {
    const { container } = await shown(oneItemCanvas());

    for (const direction of ["n", "s", "e", "w", "nw", "ne", "sw", "se"]) {
      // A control nobody can name is not operable by keyboard or screen reader.
      // Which words it uses is not pinned — no acceptance criterion picks them.
      expect(resizeHandle(container, "i-1", direction)).toHaveAccessibleName();
    }
  });

  it("grows the item from the south-east corner without moving its origin", async () => {
    const { container } = await shown(oneItemCanvas());

    drag(resizeHandle(container, "i-1", "se"), [500, 380], [560, 430]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(itemById(layout, "i-1")).toMatchObject({ x: 100, y: 80, width: 460, height: 350 });
  });

  it("moves the origin with the size when the north-west corner is dragged", async () => {
    // One write, not two: an API taking only a width would make this handle two
    // PATCHes that race each other.
    const { container } = await shown(oneItemCanvas());

    drag(resizeHandle(container, "i-1", "nw"), [100, 80], [140, 110]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(itemById(layout, "i-1")).toMatchObject({ x: 140, y: 110, width: 360, height: 270 });
  });

  it("changes only the axis an edge handle owns", async () => {
    const { container } = await shown(oneItemCanvas());

    drag(resizeHandle(container, "i-1", "e"), [500, 200], [560, 400]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    // The vertical travel is 200px and the height must not have followed it.
    expect(itemById(lastLayout(), "i-1")).toMatchObject({ width: 460, height: 300, y: 80 });
  });

  it("stops at the minimum size instead of producing a sliver", async () => {
    // AC10. The server's own rule is `width > 0`, which accepts one pixel.
    const { container } = await shown(oneItemCanvas());

    drag(resizeHandle(container, "i-1", "se"), [500, 380], [-2000, -2000]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(itemById(layout, "i-1")).toMatchObject({ width: MIN_ITEM_PX, height: MIN_ITEM_PX });
  });

  it("stops the moving corner when a north-west drag hits the minimum", async () => {
    // Past the floor the size stops; if the origin kept going the box inverts.
    const { container } = await shown(oneItemCanvas());

    drag(resizeHandle(container, "i-1", "nw"), [100, 80], [3000, 3000]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const stored = itemById(lastLayout(), "i-1");
    expect(stored).toMatchObject({ width: MIN_ITEM_PX, height: MIN_ITEM_PX });
    // The far corner stays put: origin + size is where the box's other side was.
    expect(Number(stored.x) + MIN_ITEM_PX).toBe(500);
    expect(Number(stored.y) + MIN_ITEM_PX).toBe(380);
  });
});

describe("AC2 — z-order is user-controllable, and focus raises", () => {
  it("paints each item at the z-index its layout stores", async () => {
    const { container } = await shown(threeItemCanvas());

    expect(drawnBox(container, "i-1").zIndex).toBe(0);
    expect(drawnBox(container, "i-3").zIndex).toBe(2);
  });

  it("raises a container to the front when it is clicked into", async () => {
    // `pointerdown` in the capture phase, not focus alone: a click inside a
    // terminal or an iframe never focuses the item element, and that container
    // is exactly the one to raise.
    const { container } = await shown(threeItemCanvas());

    fireEvent.pointerDown(canvasItemEl(container, "i-1"), { pointerId: 3, clientX: 10, clientY: 10 });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(Number(itemById(layout, "i-1").z_index)).toBeGreaterThan(
      Number(itemById(layout, "i-3").z_index),
    );
  });

  it("writes nothing when the container clicked into is already at the front", async () => {
    // Focus raises on every click, and the front-most container is clicked most.
    const { container } = await shown(threeItemCanvas());

    fireEvent.pointerDown(canvasItemEl(container, "i-3"), { pointerId: 3, clientX: 10, clientY: 10 });

    await settle();
    expect(mockUpdateView).not.toHaveBeenCalled();
  });

  it("sends a container to the back on request", async () => {
    const user = userEvent.setup();
    const { container } = await shown(threeItemCanvas());

    await user.click(itemAction(container, "i-3", "send-to-back"));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalled());
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(Number(itemById(layout, "i-3").z_index)).toBeLessThan(
      Number(itemById(layout, "i-1").z_index),
    );
  });
});

describe("AC3 — the surface pans, and a container's own scrolling wins", () => {
  it("leaves a plain wheel over a container's own content entirely alone", async () => {
    // AC3's substance, expressed as its structural cause. The wheel is dispatched
    // on the *terminal inside the container* — the element the criterion is about
    // — and it bubbles all the way to the viewport, which is the only place a
    // canvas handler could sit. Nothing consumes it and the zoom does not move,
    // so the browser's own scroll chaining is free to give it to the terminal's
    // scrollback before the surface ever sees it. jsdom neither lays out nor
    // chains, so what is verified here is that the canvas does not *take* the
    // event; that the browser then routes it to the terminal is the browser's job.
    const { container } = await shown(oneItemCanvas(terminalContainer()));
    const viewport = viewportEl(container);
    expect(viewport.style.overflow).toBe("auto");

    const terminal = container.querySelector<HTMLElement>('[data-testid="fake-terminal"]');
    expect(terminal).not.toBeNull();

    const before = surfaceEl(container).dataset.zoom;
    const wheel = new WheelEvent("wheel", { deltaY: 120, bubbles: true, cancelable: true });
    await act(async () => {
      terminal?.dispatchEvent(wheel);
    });

    expect(wheel.defaultPrevented).toBe(false);
    // And the canvas did not pan or zoom behind the terminal's back.
    expect(surfaceEl(container).dataset.zoom).toBe(before);
  });

  it("zooms on ctrl+wheel, which is the one wheel the surface does claim", async () => {
    const { container } = await shown(oneItemCanvas());
    const viewport = viewportEl(container);

    const wheel = new WheelEvent("wheel", {
      deltaY: -120,
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    await act(async () => {
      viewport.dispatchEvent(wheel);
    });

    // Claimed, so the browser's own page zoom does not also fire.
    expect(wheel.defaultPrevented).toBe(true);
    expect(Number(surfaceEl(container).dataset.zoom)).toBeGreaterThan(1);
  });
});

describe("AC5 — 100% zoom carries no transform at all", () => {
  it("renders the surface with no transform while zoom is 100%", async () => {
    // This is the structural cause of pixel-accuracy and correct hit-testing for
    // a live terminal and a cross-document iframe: with no `scale()` there is no
    // rasterised layer and no coordinate mapping in front of them. jsdom cannot
    // measure the consequence; it can pin the cause.
    const { container } = await shown(oneItemCanvas(terminalContainer()));

    expect(surfaceEl(container).style.transform).toBe("");
    expect(Number(surfaceEl(container).dataset.zoom)).toBe(1);
  });

  it("scales the surface once zoom leaves 100%, and stops again on reset", async () => {
    const user = userEvent.setup();
    const { container } = await shown(oneItemCanvas());

    await user.click(toolbarAction(container, "zoom-in"));
    expect(surfaceEl(container).style.transform).toMatch(/^scale\(/);

    await user.click(toolbarAction(container, "zoom-reset"));
    expect(surfaceEl(container).style.transform).toBe("");
  });

  it("refuses to zoom past the range a surface can be drawn at", async () => {
    const user = userEvent.setup();
    const { container } = await shown(oneItemCanvas());

    for (let step = 0; step < 20; step += 1) {
      const button = toolbarAction(container, "zoom-in") as HTMLButtonElement;
      if (button.disabled) break;
      await user.click(button);
    }

    expect(Number(surfaceEl(container).dataset.zoom)).toBeLessThanOrEqual(MAX_ZOOM);
  });

  it("divides a pointer delta by the zoom, so a drag tracks the cursor", async () => {
    // At 200% a 100px pointer travel is 50 surface pixels. Without the division
    // the item runs away from the cursor at every zoom but 100%.
    const user = userEvent.setup();
    const { container } = await shown(oneItemCanvas());

    await user.click(toolbarAction(container, "zoom-reset"));
    await user.click(toolbarAction(container, "zoom-in"));
    const zoom = Number(surfaceEl(container).dataset.zoom);
    mockUpdateView.mockClear();

    drag(dragHandle(container, "i-1"), [300, 300], [400, 300]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(Number(itemById(lastLayout(), "i-1").x)).toBeCloseTo(100 + 100 / zoom, 6);
  });
});

describe("AC4 — containers come from the shared registry", () => {
  it("renders the registry's primitive for a configured container", async () => {
    const { container } = await shown(oneItemCanvas(terminalContainer()));

    expect(canvasItemEl(container, "i-1").querySelector('[data-testid="fake-terminal"]')).not.toBeNull();
  });

  it("offers the prompt, not a primitive, for a container that has chosen none", async () => {
    const { container } = await shown(oneItemCanvas());

    expect(
      canvasItemEl(container, "i-1").querySelector('[data-container-id="c-1"]'),
    ).not.toBeNull();
  });

  it("replaces the container when a primitive is picked, rather than merging into it", async () => {
    // A terminal primitive stored under the placeholder's `kind: "panel"` is a
    // disagreement `ContainerPrimitiveHost` refuses to mount.
    const user = userEvent.setup();
    const { container } = await shown(oneItemCanvas());

    await user.click(itemAction(container, "i-1", "pick-primitive"));
    await user.click(await screen.findByRole("button", { name: /terminal/i }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalled());
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(containersOf(layout)["c-1"].kind).not.toBe("panel");
  });
});

describe("AC7 — geometry survives a reload", () => {
  it("draws a re-read canvas from what was stored, not from what was on screen", async () => {
    const { container, unmount } = await shown(oneItemCanvas());

    drag(dragHandle(container, "i-1"), [300, 300], [420, 360]);
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    unmount();

    // A cold mount against the fake server's record, which the PATCH updated —
    // and a fresh query client, so nothing survives in a cache. Not
    // `renderCanvas`, which would reset the fake server to the fixture and
    // assert nothing but that the renderer can draw a literal.
    const reloaded = render(canvasRoute(testClient()));
    await reloaded.findByTestId("view-host");
    expect(drawnBox(reloaded.container, "i-1")).toMatchObject({ x: 220, y: 140 });
  });
});

describe("AC7 — a refused write does not leave the screen lying", () => {
  it("draws the item from the record again once a rejected PATCH settles", async () => {
    // The draft covers the gap between letting go and the write landing. Kept
    // past a *refused* write it becomes a lie the user cannot see: the item sits
    // where they dropped it while the record holds the old position, and the next
    // edit composes from that record and quietly re-stores the old one. Dropping
    // it on settled — not on success — is what makes the screen agree again.
    const { container } = await shown(oneItemCanvas());
    mockUpdateView.mockRejectedValue(new ApiError(400, "Layout too large"));

    drag(dragHandle(container, "i-1"), [300, 300], [420, 360]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(drawnBox(container, "i-1")).toMatchObject({ x: 100, y: 80 });
  });

  it("keeps drawing the committed geometry when the PATCH succeeds", async () => {
    // The same drop, on the path where the record now agrees — so it changes
    // nothing on screen. Asserted because a reconciliation that snapped back on
    // success too would be the reverse bug.
    const { container } = await shown(oneItemCanvas());

    drag(dragHandle(container, "i-1"), [300, 300], [420, 360]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(drawnBox(container, "i-1")).toMatchObject({ x: 220, y: 140 });
  });
});

describe("AC8 — the viewport is restored", () => {
  it("applies a stored zoom on a cold mount", async () => {
    window.localStorage.setItem(
      `loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`,
      '{"panX":0,"panY":0,"zoom":2}',
    );

    const { container } = await shown(oneItemCanvas());

    expect(Number(surfaceEl(container).dataset.zoom)).toBe(2);
    expect(surfaceEl(container).style.transform).toBe("scale(2)");
  });

  it("applies a stored pan to the viewport on a cold mount", async () => {
    // jsdom never scrolls — nothing overflows because nothing is laid out — so
    // `scrollLeft` reads 0 no matter what is assigned to it. Watching the setter
    // is what makes the assignment observable at all; whether the browser then
    // scrolls that far is the browser's business, and this asserts only that the
    // canvas asked it to.
    window.localStorage.setItem(
      `loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`,
      '{"panX":640,"panY":320,"zoom":1}',
    );
    const scrolledTo: { left: number[]; top: number[] } = { left: [], top: [] };
    const original = Object.getOwnPropertyDescriptor(Element.prototype, "scrollLeft");
    Object.defineProperty(Element.prototype, "scrollLeft", {
      configurable: true,
      get: () => 0,
      set(value: number) {
        scrolledTo.left.push(value);
      },
    });
    const originalTop = Object.getOwnPropertyDescriptor(Element.prototype, "scrollTop");
    Object.defineProperty(Element.prototype, "scrollTop", {
      configurable: true,
      get: () => 0,
      set(value: number) {
        scrolledTo.top.push(value);
      },
    });

    try {
      await shown(oneItemCanvas());
      expect(scrolledTo.left).toContain(640);
      expect(scrolledTo.top).toContain(320);
    } finally {
      if (original !== undefined) Object.defineProperty(Element.prototype, "scrollLeft", original);
      if (originalTop !== undefined) {
        Object.defineProperty(Element.prototype, "scrollTop", originalTop);
      }
    }
  });

  it("remembers a zoom change once the viewport stops moving", async () => {
    jest.useFakeTimers();
    try {
      renderCanvas(oneItemCanvas());
      const host = await screen.findByTestId("view-host");
      act(() => {
        (toolbarAction(host, "zoom-in") as HTMLButtonElement).click();
      });
      act(() => {
        jest.advanceTimersByTime(1000);
      });

      expect(readViewport(SLUG, VIEW_ID).zoom).toBeGreaterThan(1);
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("AC9 — a container cannot be lost off-surface", () => {
  it("clamps a drag that would carry a container off the reachable origin", async () => {
    const { container } = await shown(oneItemCanvas());

    drag(dragHandle(container, "i-1"), [300, 300], [-5000, -5000]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    // The origin is 0,0 and the viewport scrolls from there, so a negative
    // coordinate is precisely the region no amount of scrolling reaches.
    expect(itemById(layout, "i-1")).toMatchObject({ x: 0, y: 0 });
  });

  it("clamps a drag that would carry a container past the far edge", async () => {
    const { container } = await shown(oneItemCanvas());

    drag(dragHandle(container, "i-1"), [300, 300], [5_000_000, 5_000_000]);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    const stored = itemById(layout, "i-1");
    // The *far* corner is what must stay reachable — an item pinned by its
    // origin at the extent extends past it.
    expect(Number(stored.x) + Number(stored.width)).toBeLessThanOrEqual(REACHABLE_EXTENT);
  });

  it("fits the content, and the next zoom step continues from the fitted zoom", async () => {
    // The second clause is the assertion that matters: the zoom a handler
    // outside the render reads is held in a ref beside the state (side effects
    // cannot live in a `setState` updater — `StrictMode` runs those twice), and
    // a fit that moved one without the other would have this step compute its
    // anchor from the zoom before the fit.
    const user = userEvent.setup();
    const { container } = await shown(oneItemCanvas());

    await user.click(toolbarAction(container, "fit-to-content"));
    // The stubbed viewport is 1000x800 and the item's box is 400x300, so the
    // fit is the tighter of the two ratios: 1000/400 = 2.5, not 800/300.
    const fitted = Number(surfaceEl(container).dataset.zoom);
    expect(fitted).toBeCloseTo(2.5, 6);

    await user.click(toolbarAction(container, "zoom-in"));
    expect(Number(surfaceEl(container).dataset.zoom)).toBeGreaterThan(fitted);
  });

  it("offers a way back that does not depend on finding anything first", async () => {
    const { container } = await shown(oneItemCanvas());
    // AC9's alternative to bounds, and it is offered as well as them: a control
    // that is always on the toolbar, never on a container the user has lost.
    expect(toolbarAction(container, "fit-to-content")).toHaveAccessibleName();
  });
});

describe("AC10 — move and resize are operable by keyboard", () => {
  it("moves a focused container with the arrow keys, writing once on release", async () => {
    const { container } = await shown(oneItemCanvas());
    const item = canvasItemEl(container, "i-1");
    item.focus();
    mockUpdateView.mockClear();

    fireEvent.keyDown(item, { key: "ArrowRight" });
    fireEvent.keyDown(item, { key: "ArrowRight" });
    // Drawn but not yet written: one adjustment, one PATCH — a held key repeats.
    expect(mockUpdateView).not.toHaveBeenCalled();
    fireEvent.keyUp(item, { key: "ArrowRight" });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    // Both presses, not one: `> 100` would pass on a handler that applied a
    // single step, or one whose repeat overwrote the previous draft.
    expect(itemById(layout, "i-1")).toMatchObject({
      x: 100 + 2 * KEY_STEP_PX,
      y: 80,
      width: 400,
      height: 300,
    });
    await settle();
  });

  it("resizes with shift and the arrow keys", async () => {
    const { container } = await shown(oneItemCanvas());
    const item = canvasItemEl(container, "i-1");
    item.focus();
    mockUpdateView.mockClear();

    fireEvent.keyDown(item, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyUp(item, { key: "ArrowRight", shiftKey: true });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    // The size grew and the origin did not: shift+arrow resizes from the corner
    // that leaves the item where it is.
    expect(Number(itemById(layout, "i-1").width)).toBeGreaterThan(400);
    expect(itemById(layout, "i-1")).toMatchObject({ x: 100, y: 80 });
  });

  it("writes an unsaved keyboard adjustment when the container loses focus", async () => {
    const { container } = await shown(oneItemCanvas());
    const item = canvasItemEl(container, "i-1");
    item.focus();
    mockUpdateView.mockClear();

    fireEvent.keyDown(item, { key: "ArrowDown" });
    fireEvent.blur(item);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(Number(itemById(lastLayout(), "i-1").y)).toBeGreaterThan(80);
  });

  it("commits a shift-resize as a resize even when Shift comes up first", async () => {
    // The keyup of the *Shift key itself* reports `shiftKey: false`. Deriving the
    // write from the modifier state at settle time therefore sent an entirely
    // ordinary key ordering — release Shift, then the arrow — through the move
    // path, which stores x and y only and reverted the size the user had just
    // set, while the draft carried on showing the resized box.
    const { container } = await shown(oneItemCanvas());
    const item = canvasItemEl(container, "i-1");
    item.focus();
    mockUpdateView.mockClear();

    fireEvent.keyDown(item, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyUp(item, { key: "Shift", shiftKey: false });

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(itemById(layout, "i-1")).toMatchObject({ width: 400 + KEY_STEP_PX, x: 100, y: 80 });
    await settle();
  });

  it("commits a shift-resize as a resize when the container is blurred instead", async () => {
    const { container } = await shown(oneItemCanvas());
    const item = canvasItemEl(container, "i-1");
    item.focus();
    mockUpdateView.mockClear();

    fireEvent.keyDown(item, { key: "ArrowDown", shiftKey: true });
    fireEvent.blur(item);

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    await settle();
    expect(itemById(lastLayout(), "i-1")).toMatchObject({ height: 300 + KEY_STEP_PX });
    await settle();
  });

  it("leaves an arrow key pressed inside a container to the container", async () => {
    // The handler sits on the item's frame and keyboard events bubble, so without
    // a target guard a Left in a terminal's prompt would lose the caret move to
    // `preventDefault` and slide the terminal sideways instead. This is AC5's
    // "hit-testing lands on the correct element" in its keyboard form.
    const { container } = await shown(oneItemCanvas(terminalContainer()));
    const terminal = container.querySelector<HTMLElement>('[data-testid="fake-terminal"]');
    expect(terminal).not.toBeNull();
    mockUpdateView.mockClear();

    const arrow = new KeyboardEvent("keydown", {
      key: "ArrowLeft",
      bubbles: true,
      cancelable: true,
    });
    await act(async () => {
      terminal?.dispatchEvent(arrow);
    });
    fireEvent.keyUp(canvasItemEl(container, "i-1"), { key: "ArrowLeft" });

    // Neither swallowed nor acted on: the container did not move, and the key is
    // still the terminal's to handle.
    expect(arrow.defaultPrevented).toBe(false);
    expect(drawnBox(container, "i-1")).toMatchObject({ x: 100, y: 80 });
    await settle();
    expect(mockUpdateView).not.toHaveBeenCalled();
  });

  it("reaches every container's controls with the keyboard", async () => {
    const { container } = await shown(oneItemCanvas());

    for (const action of ["pick-primitive", "bring-to-front", "send-to-back", "close"]) {
      const control = itemAction(container, "i-1", action);
      expect(control.tagName).toBe("BUTTON");
      expect(control).toHaveAccessibleName();
    }
  });
});

describe("removing a container", () => {
  it("drops the container with the item, because an orphan is refused", async () => {
    const user = userEvent.setup();
    const { container } = await shown(overlappingCanvas());

    // `i-1` sits at the back here, so the press that opens the click raises it
    // first — two writes, and the last one is the removal. Asserting a count of
    // one would be asserting that focus does *not* raise, which is AC2 inverted.
    await user.click(itemAction(container, "i-1", "close"));

    await waitFor(() => expect(itemsOf(lastLayout())).toHaveLength(1));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    expect(Object.keys(containersOf(layout))).toEqual(["c-2"]);
  });

  it("leaves the empty state behind, not a blank screen", async () => {
    const user = userEvent.setup();
    const { container } = await shown(oneItemCanvas());

    await user.click(itemAction(container, "i-1", "close"));

    expect(await screen.findByTestId("view-canvas-empty")).toBeVisible();
  });
});
