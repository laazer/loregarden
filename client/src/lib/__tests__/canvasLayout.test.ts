/**
 * The canvas's own arithmetic: what each edit stores, and that every layout it
 * produces is one `parse_view_layout` accepts.
 *
 * Every case ends at `assertServerAcceptableLayout`, which mirrors the server's
 * structural rules — the exactly-once container rule, the coordinate and extent
 * bounds, `extra="forbid"`, and the integer `z_index`. That mirror has one known
 * gap: it does not model `MAX_LAYOUT_BYTES` (256,000, measured on the exact
 * stored string), so a layout it blesses can still be refused for size. Nothing
 * here can close that gap, because a container's `settings` is user-supplied.
 */

import {
  DEFAULT_ITEM_HEIGHT,
  DEFAULT_ITEM_WIDTH,
  MAX_CONTAINERS,
  MIN_ITEM_PX,
  REACHABLE_EXTENT,
  addItem,
  clampGeometry,
  contentBounds,
  moveItem,
  readCanvasItems,
  removeItem,
  resizeItem,
  restackItem,
  withGeometry,
} from "../canvasLayout";
import type { ViewLayout } from "../viewsApi";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";

type Json = Record<string, unknown>;

const panel = (): Json => ({ kind: "panel", settings: {} });

function canvas(items: Json[], containers: Json): ViewLayout {
  return { kind: "canvas", containers, items };
}

function twoItems(): ViewLayout {
  return canvas(
    [
      { id: "i-1", container_id: "c-1", x: 100, y: 80, width: 400, height: 300, z_index: 0 },
      { id: "i-2", container_id: "c-2", x: 200, y: 140, width: 400, height: 300, z_index: 1 },
    ],
    { "c-1": panel(), "c-2": panel() },
  );
}

const itemsOf = (layout: ViewLayout): Json[] => layout.items as Json[];
const itemById = (layout: ViewLayout, id: string): Json => {
  const found = itemsOf(layout).find((item) => item.id === id);
  if (found === undefined) throw new Error(`no item ${id}`);
  return found;
};

describe("reading a stored canvas", () => {
  it("orders items back to front by z-index, whatever order they were stored in", () => {
    // AC2 is about a stacking order the user controls; the DOM paints in array
    // order, so the model has to hand the renderer the stack and not the record.
    const layout = canvas(
      [
        { id: "i-top", container_id: "c-1", x: 0, y: 0, width: 200, height: 200, z_index: 9 },
        { id: "i-bottom", container_id: "c-2", x: 0, y: 0, width: 200, height: 200, z_index: 1 },
      ],
      { "c-1": panel(), "c-2": panel() },
    );

    expect(readCanvasItems(layout).map((item) => item.id)).toEqual(["i-bottom", "i-top"]);
  });

  it("breaks a z-index tie on stored position, so the later item is on top", () => {
    const layout = canvas(
      [
        { id: "i-first", container_id: "c-1", x: 0, y: 0, width: 200, height: 200, z_index: 3 },
        { id: "i-second", container_id: "c-2", x: 0, y: 0, width: 200, height: 200, z_index: 3 },
      ],
      { "c-1": panel(), "c-2": panel() },
    );

    expect(readCanvasItems(layout).map((item) => item.id)).toEqual(["i-first", "i-second"]);
  });

  it("gives an item with an unreadable size the default rather than zero", () => {
    // A 0px item is one the user has no way to grab and repair, which is the
    // same trap AC10's minimum exists to close.
    const layout = canvas([{ id: "i-1", container_id: "c-1", x: 5, y: 5 }], { "c-1": panel() });

    const [item] = readCanvasItems(layout);
    expect(item.width).toBe(DEFAULT_ITEM_WIDTH);
    expect(item.height).toBe(DEFAULT_ITEM_HEIGHT);
  });

  it("rounds a stored fractional z-index, because the server's field is an int", () => {
    const layout = canvas(
      [{ id: "i-1", container_id: "c-1", x: 0, y: 0, width: 200, height: 200, z_index: 2.6 }],
      { "c-1": panel() },
    );

    expect(readCanvasItems(layout)[0].z_index).toBe(3);
  });

  it("drops an item it cannot read rather than blanking the whole canvas", () => {
    // The page owns "this layout is undrawable". One malformed item among good
    // ones is not that, and drawing it at the origin at 0x0 is worse than not.
    const layout = canvas(
      [
        { id: "i-1", container_id: "c-1", x: 0, y: 0, width: 200, height: 200, z_index: 0 },
        { container_id: "c-2", x: 0, y: 0, width: 200, height: 200 },
      ],
      { "c-1": panel(), "c-2": panel() },
    );

    expect(readCanvasItems(layout).map((item) => item.id)).toEqual(["i-1"]);
  });
});

describe("clamping", () => {
  it("refuses to produce an item below the minimum size", () => {
    // AC10. The server's own rule is only `width > 0`, which accepts a sliver.
    expect(clampGeometry({ x: 0, y: 0, width: 4, height: 1 })).toEqual({
      x: 0,
      y: 0,
      width: MIN_ITEM_PX,
      height: MIN_ITEM_PX,
    });
  });

  it("keeps an item inside the reachable surface, its far corner included", () => {
    // AC9: a container cannot be lost off-surface. The origin is 0,0 and the
    // viewport scrolls from there, so a negative coordinate is precisely the
    // region no amount of scrolling reaches.
    const clamped = clampGeometry({ x: -500, y: -900, width: 300, height: 200 });
    expect(clamped.x).toBe(0);
    expect(clamped.y).toBe(0);

    const far = clampGeometry({ x: 1e9, y: 1e9, width: 300, height: 200 });
    expect(far.x).toBe(REACHABLE_EXTENT - 300);
    expect(far.y).toBe(REACHABLE_EXTENT - 200);
  });

  it("turns a non-finite coordinate into a real one rather than passing it through", () => {
    // `Math.min(Math.max(NaN, low), high)` is `NaN`: every comparison is false.
    // A NaN on the wire is a 422 the user sees as a silently failed autosave.
    const clamped = clampGeometry({ x: Number.NaN, y: Infinity, width: Number.NaN, height: 200 });
    expect(Number.isFinite(clamped.x)).toBe(true);
    expect(Number.isFinite(clamped.y)).toBe(true);
    expect(Number.isFinite(clamped.width)).toBe(true);
  });

  it("clamps the geometry a renderer draws a gesture at, not only the one it commits", () => {
    // `withGeometry` is what the surface draws mid-drag. Drawing the raw pointer
    // arithmetic and clamping only on release makes an item follow the cursor
    // past the edge and then jump back on pointerup.
    const item = readCanvasItems(twoItems())[0];
    expect(withGeometry(item, { x: -400, y: -400, width: 300, height: 200 })).toMatchObject({
      x: 0,
      y: 0,
    });
  });
});

describe("placing a container", () => {
  it("adds one item and one container, on top of the stack", () => {
    // AC1's "added at a point", and AC2's "a new container is on top".
    const next = addItem(twoItems(), 640, 420);
    assertServerAcceptableLayout(next);

    expect(itemsOf(next)).toHaveLength(3);
    const added = readCanvasItems(next)[2];
    expect(added.x).toBe(640);
    expect(added.y).toBe(420);
    expect(added.z_index).toBeGreaterThan(1);
    expect(Object.keys(next.containers as Json)).toHaveLength(3);
    // Unconfigured, so the new container opens on the primitive prompt.
    expect((next.containers as Record<string, Json>)[added.container_id]).toEqual(panel());
  });

  it("places onto an empty canvas, which is a layout the server accepts", () => {
    // Unlike a flex grid, whose smallest legal form still holds one container.
    const next = addItem({ kind: "canvas", containers: {}, items: [] }, 10, 10);
    assertServerAcceptableLayout(next);
    expect(itemsOf(next)).toHaveLength(1);
  });

  it("clamps a placement outside the reachable surface instead of storing it", () => {
    const next = addItem(twoItems(), -900, -900);
    assertServerAcceptableLayout(next);
    expect(readCanvasItems(next)[2]).toMatchObject({ x: 0, y: 0 });
  });

  it("refuses to place past the container ceiling the server enforces", () => {
    const containers: Json = {};
    const items: Json[] = [];
    for (let index = 0; index < MAX_CONTAINERS; index += 1) {
      containers[`c-${index}`] = panel();
      items.push({
        id: `i-${index}`,
        container_id: `c-${index}`,
        x: 0,
        y: 0,
        width: 200,
        height: 200,
        z_index: index,
      });
    }

    // Thrown here rather than discovered as a 400 on a PATCH the user reads as a
    // silently failed autosave.
    expect(() => addItem(canvas(items, containers), 0, 0)).toThrow(/256/);
  });
});

describe("moving and resizing", () => {
  it("moves only the item named, and leaves every other placement alone", () => {
    const next = moveItem(twoItems(), "i-1", 500, 400);
    assertServerAcceptableLayout(next);

    expect(itemById(next, "i-1")).toMatchObject({ x: 500, y: 400, width: 400, height: 300 });
    expect(itemById(next, "i-2")).toMatchObject({ x: 200, y: 140 });
  });

  it("lets two items overlap, which is the canvas's difference from the grid", () => {
    // AC1's last clause. The exactly-once rule the contract checks constrains
    // *containers*, not pixels — two items on the same spot is legal, two items
    // pointing at one container is not.
    const next = moveItem(twoItems(), "i-1", 200, 140);
    assertServerAcceptableLayout(next);
    expect(itemById(next, "i-1")).toMatchObject({ x: 200, y: 140 });
    expect(itemById(next, "i-2")).toMatchObject({ x: 200, y: 140 });
  });

  it("stores position with size, because a north-west resize moves the origin", () => {
    // One write, not two: a resize API taking only a width would make the
    // top-left handle two PATCHes that race each other.
    const next = resizeItem(twoItems(), "i-1", { x: 60, y: 40, width: 440, height: 340 });
    assertServerAcceptableLayout(next);
    expect(itemById(next, "i-1")).toMatchObject({ x: 60, y: 40, width: 440, height: 340 });
  });

  it("refuses to resize below the minimum, whatever the caller asked for", () => {
    const next = resizeItem(twoItems(), "i-1", { x: 100, y: 80, width: 1, height: 1 });
    assertServerAcceptableLayout(next);
    expect(itemById(next, "i-1")).toMatchObject({ width: MIN_ITEM_PX, height: MIN_ITEM_PX });
  });

  it("throws when the item a gesture ends on is no longer in the layout", () => {
    // It was closed in another tab while the drag was open. The write path turns
    // this into the standard failure toast with no request sent.
    expect(() => moveItem(twoItems(), "i-gone", 0, 0)).toThrow(/no longer/);
  });
});

describe("stacking", () => {
  it("renumbers the whole stack densely rather than incrementing forever", () => {
    // Raising by `max + 1` walks z_index up without bound on a container that is
    // focused often, and `int` on the wire stops being exact past 2^53.
    const layout = canvas(
      [
        { id: "i-1", container_id: "c-1", x: 0, y: 0, width: 200, height: 200, z_index: 0 },
        { id: "i-2", container_id: "c-2", x: 0, y: 0, width: 200, height: 200, z_index: 900 },
        { id: "i-3", container_id: "c-3", x: 0, y: 0, width: 200, height: 200, z_index: 5000 },
      ],
      { "c-1": panel(), "c-2": panel(), "c-3": panel() },
    );

    const next = restackItem(layout, "i-1", true);
    assertServerAcceptableLayout(next);
    expect(readCanvasItems(next).map((item) => [item.id, item.z_index])).toEqual([
      ["i-2", 0],
      ["i-3", 1],
      ["i-1", 2],
    ]);
  });

  it("sends an item to the back without disturbing the order of the rest", () => {
    const layout = canvas(
      [
        { id: "i-1", container_id: "c-1", x: 0, y: 0, width: 200, height: 200, z_index: 0 },
        { id: "i-2", container_id: "c-2", x: 0, y: 0, width: 200, height: 200, z_index: 1 },
        { id: "i-3", container_id: "c-3", x: 0, y: 0, width: 200, height: 200, z_index: 2 },
      ],
      { "c-1": panel(), "c-2": panel(), "c-3": panel() },
    );

    const next = restackItem(layout, "i-3", false);
    assertServerAcceptableLayout(next);
    expect(readCanvasItems(next).map((item) => item.id)).toEqual(["i-3", "i-1", "i-2"]);
  });

  it("returns the layout unchanged when the item is already where it is sent", () => {
    // Focus raises on every click, and the front-most container is clicked most
    // — this is what stops each of those clicks being a PATCH.
    const layout = twoItems();
    expect(restackItem(layout, "i-2", true)).toBe(layout);
  });
});

describe("removing a container", () => {
  it("drops the container with the item, because an orphan is refused", () => {
    const next = removeItem(twoItems(), "i-1");
    assertServerAcceptableLayout(next);
    expect(itemsOf(next)).toHaveLength(1);
    expect(Object.keys(next.containers as Json)).toEqual(["c-2"]);
  });

  it("leaves an empty canvas, which the server accepts and a grid has no analogue for", () => {
    let next = removeItem(twoItems(), "i-1");
    next = removeItem(next, "i-2");
    assertServerAcceptableLayout(next);
    expect(itemsOf(next)).toEqual([]);
    expect(next.containers).toEqual({});
  });
});

describe("content bounds", () => {
  it("is the box every item fits inside", () => {
    // What "fit to content" aims at — AC9's way back.
    expect(contentBounds(readCanvasItems(twoItems()))).toEqual({
      x: 100,
      y: 80,
      width: 500,
      height: 360,
    });
  });

  it("has no answer for an empty canvas, rather than a zero-sized one", () => {
    // A zero-sized box divided into a viewport is `Infinity`, and a zoom of
    // Infinity is a surface that cannot be drawn.
    expect(contentBounds([])).toBeUndefined();
  });
});
