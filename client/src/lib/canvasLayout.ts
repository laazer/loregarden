/**
 * The edits a canvas makes to its own stored layout: placing an item, moving it,
 * resizing it, restacking it, and removing it.
 *
 * The sibling of `gridLayout` and deliberately the same shape, because the same
 * reasoning applies: `parse_view_layout` refuses a coordinate outside
 * ±`MAX_CANVAS_COORDINATE`, an extent outside `(0, MAX_CANVAS_EXTENT]`, a
 * non-integer `z_index`, a non-finite number anywhere, a layout holding more than
 * `MAX_CONTAINERS` containers or `MAX_LAYOUT_NODES` items, a container nothing
 * places, and a container placed twice. Every function below produces a layout
 * that satisfies all of them or throws, rather than a layout the write path
 * discovers is a 400.
 *
 * `MAX_LAYOUT_BYTES` (256,000, measured on the exact stored string) is *not*
 * checked here, and unlike the grid's case that is a real gap rather than dead
 * code: a canvas container's `settings` is user-supplied — a web embed's URL, a
 * terminal's command — so 256 containers can serialize past the cap in a way no
 * count of items predicts. The write path reports the resulting 400 through the
 * standard failure toast; nothing here can pre-empt it without serializing the
 * whole layout on every pointer gesture.
 *
 * Two shapes are load-bearing, both inherited from `gridLayout`:
 *
 *   - **The stored list is read into a model before it is edited.** A layout
 *     arrives as `unknown` JSON; editing it by index into `Record<string,
 *     unknown>` is how a missing `width` becomes `NaN` and a 422. Reading it once
 *     into `CanvasItemModel` puts every narrowing in one place.
 *   - **Nothing is mutated.** The layout handed in is the record react-query is
 *     holding, and the items on screen are rendered from it.
 *
 * Clamping lives in the arithmetic and never in CSS. A CSS floor stops an item
 * shrinking on screen while the stored width keeps falling, so the screen and the
 * record quietly disagree until the next reload.
 */

import { asJson, freshId } from "./viewLayouts";
import type { ViewLayout } from "./viewsApi";

type Json = Record<string, unknown>;

/** `CanvasItem.width`/`height`: `gt=0, le=MAX_CANVAS_EXTENT`. */
export const MAX_CANVAS_EXTENT = 1_000_000;

/** `CanvasItem.x`/`y`: `ge=-MAX_CANVAS_COORDINATE, le=MAX_CANVAS_COORDINATE`. */
export const MAX_CANVAS_COORDINATE = 10_000_000;

/** `ContainerRegistry`'s `max_length`. One container per item, so: items. */
export const MAX_CONTAINERS = 256;

/** `_StructureWalk.claim_node`'s ceiling. A canvas item is one node. */
export const MAX_LAYOUT_NODES = 512;

/**
 * The smallest item this renderer will produce, in CSS pixels.
 *
 * The server's own rule is only `width > 0`, which accepts an item one pixel
 * wide — a container that cannot be grabbed again to undo the resize that made
 * it. AC10 ("minimum sizes are enforced") is this number, not the schema's.
 */
export const MIN_ITEM_PX = 96;

/** The size a newly placed item opens at. */
export const DEFAULT_ITEM_WIDTH = 420;
export const DEFAULT_ITEM_HEIGHT = 280;

/**
 * The far edge of the reachable surface.
 *
 * AC9 — "a container cannot be lost off-surface" — is answered here rather than
 * by a rescue control alone: every coordinate this module writes is clamped into
 * `[0, REACHABLE_EXTENT]`, so the surface has an origin the viewport can always
 * scroll back to and a far edge it cannot be dragged past. The server would
 * accept negative coordinates; nothing requires the UI to use them, and a
 * negative origin is exactly the region a scroll-based viewport cannot reach.
 *
 * Well inside `MAX_CANVAS_COORDINATE` and `MAX_CANVAS_EXTENT` so that a clamped
 * position plus a clamped size still lands inside both server bounds.
 */
export const REACHABLE_EXTENT = 100_000;

export interface CanvasItemModel {
  id: string;
  container_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  z_index: number;
}

/** Position and size together — what a move or a resize produces. */
export interface ItemGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

function containersOf(layout: ViewLayout): Json {
  return asJson(layout.containers) ?? {};
}

function finite(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return value;
}

/**
 * `value` brought inside `[low, high]`, with a non-finite input treated as the
 * low bound.
 *
 * `Math.min(Math.max(…))` alone passes `NaN` straight through — every comparison
 * against `NaN` is false, so both clamps return it — and `NaN` in a coordinate is
 * a 422 the user sees as a silently failed autosave.
 */
function clamp(value: number, low: number, high: number): number {
  if (!Number.isFinite(value)) return low;
  return Math.min(Math.max(value, low), high);
}

/** A width or height inside both the renderer's floor and the server's ceiling. */
export function clampExtent(value: number): number {
  return clamp(value, MIN_ITEM_PX, Math.min(MAX_CANVAS_EXTENT, REACHABLE_EXTENT));
}

/**
 * A coordinate inside the reachable surface, given the size that starts there.
 *
 * The size is subtracted from the far edge so the item's *far* corner stays
 * reachable too — an item pinned at the extent by its origin extends past it.
 */
export function clampCoordinate(value: number, extent: number): number {
  return clamp(value, 0, Math.max(0, REACHABLE_EXTENT - extent));
}

/** A geometry the server accepts and the viewport can reach. */
export function clampGeometry(geometry: ItemGeometry): ItemGeometry {
  const width = clampExtent(geometry.width);
  const height = clampExtent(geometry.height);
  return {
    width,
    height,
    x: clampCoordinate(geometry.x, width),
    y: clampCoordinate(geometry.y, height),
  };
}

/**
 * The same item under a different geometry, clamped.
 *
 * Exported because a renderer drawing a drag in progress needs exactly what the
 * commit will store — the mirror of `gridLayout`'s `withSize`. Drawing the raw
 * pointer arithmetic and clamping only on release is how an item appears to
 * follow the cursor past the edge and then jumps back on `pointerup`.
 */
export function withGeometry(item: CanvasItemModel, geometry: ItemGeometry): CanvasItemModel {
  return { ...item, ...clampGeometry(geometry) };
}

function parseItem(value: unknown): CanvasItemModel | undefined {
  const data = asJson(value);
  if (data === undefined) return undefined;
  const id = typeof data.id === "string" ? data.id : "";
  const containerId = typeof data.container_id === "string" ? data.container_id : "";
  if (id === "" || containerId === "") return undefined;

  // Sizes fall back to the default rather than to zero: a stored width the
  // client cannot read is a bug in the record, and an item drawn 0px wide is one
  // the user has no way to grab and repair.
  const width = clampExtent(finite(data.width, DEFAULT_ITEM_WIDTH));
  const height = clampExtent(finite(data.height, DEFAULT_ITEM_HEIGHT));
  const zRaw = finite(data.z_index, 0);
  return {
    id,
    container_id: containerId,
    x: clampCoordinate(finite(data.x, 0), width),
    y: clampCoordinate(finite(data.y, 0), height),
    width,
    height,
    // `z_index: int` server-side, so a stored float is rounded rather than
    // carried into a body the server refuses.
    z_index: Math.round(zRaw),
  };
}

/**
 * The stored placements as models, back to front.
 *
 * Sorted by `z_index` because paint order is the array order in the DOM, and the
 * stored list is in no particular order — an item saved with a higher `z_index`
 * must draw above one saved later. Ties break on the stored position, so a
 * freshly placed item sits above the one it was placed on top of.
 *
 * Items the client cannot read are dropped rather than drawn at the origin: a
 * malformed record is the page's `ViewUndrawable` problem when the *layout* is
 * unreadable, and a single bad item is not worth blanking a canvas full of good
 * ones.
 */
export function readCanvasItems(layout: ViewLayout): CanvasItemModel[] {
  const raw = Array.isArray(layout.items) ? layout.items : [];
  const items: { item: CanvasItemModel; at: number }[] = [];
  raw.forEach((value, at) => {
    const item = parseItem(value);
    if (item !== undefined) items.push({ item, at });
  });
  items.sort((a, b) => a.item.z_index - b.item.z_index || a.at - b.at);
  return items.map((entry) => entry.item);
}

function itemToJson(item: CanvasItemModel): Json {
  return {
    id: item.id,
    container_id: item.container_id,
    x: item.x,
    y: item.y,
    width: item.width,
    height: item.height,
    z_index: item.z_index,
  };
}

/**
 * The layout with `items` as its arrangement.
 *
 * The models are written back whole, so an item that was read with a clamped
 * size is stored clamped — the alternative is a record that keeps failing
 * validation every time some *other* item is moved.
 */
function storeItems(layout: ViewLayout, items: CanvasItemModel[], containers: Json): ViewLayout {
  return { ...layout, containers, items: items.map(itemToJson) };
}

function requireItem(items: CanvasItemModel[], itemId: string): CanvasItemModel {
  const found = items.find((item) => item.id === itemId);
  if (found === undefined) {
    throw new Error("The container that was moved is no longer on this canvas.");
  }
  return found;
}

/** One above the highest stored `z_index`, or 0 on an empty canvas. */
function topZ(items: CanvasItemModel[]): number {
  if (items.length === 0) return 0;
  return Math.max(...items.map((item) => item.z_index)) + 1;
}

/** The container a freshly placed item holds: no `primitive_id`, so it prompts. */
function emptyContainer(): Json {
  return { kind: "panel", settings: {} };
}

/**
 * Place a new container at `x`, `y`, on top of the stack.
 *
 * Throws when the canvas is already at the server's ceiling for containers or
 * nodes — a refusal the user sees, rather than a PATCH that comes back 400 as a
 * silent autosave. One placement adds one container and one item, so the two
 * ceilings are reached together on a canvas built only by placing.
 */
export function addItem(layout: ViewLayout, x: number, y: number): ViewLayout {
  const items = readCanvasItems(layout);
  const containers = containersOf(layout);
  if (Object.keys(containers).length + 1 > MAX_CONTAINERS) {
    throw new Error(`A view cannot hold more than ${MAX_CONTAINERS} containers.`);
  }
  if (items.length + 1 > MAX_LAYOUT_NODES) {
    throw new Error(`A view cannot hold more than ${MAX_LAYOUT_NODES} layout nodes.`);
  }

  const containerId = freshId("c");
  const placed: CanvasItemModel = {
    id: freshId("i"),
    container_id: containerId,
    ...clampGeometry({ x, y, width: DEFAULT_ITEM_WIDTH, height: DEFAULT_ITEM_HEIGHT }),
    z_index: topZ(items),
  };
  return storeItems(layout, [...items, placed], { ...containers, [containerId]: emptyContainer() });
}

/** Move `itemId` so its top-left corner sits at `x`, `y`. */
export function moveItem(layout: ViewLayout, itemId: string, x: number, y: number): ViewLayout {
  const items = readCanvasItems(layout);
  const item = requireItem(items, itemId);
  const moved = withGeometry(item, { ...item, x, y });
  return storeItems(
    layout,
    items.map((each) => (each.id === itemId ? moved : each)),
    containersOf(layout),
  );
}

/**
 * Resize `itemId` to `geometry`.
 *
 * Position travels with the size because a drag on the top or left edge moves
 * the origin as well — a resize API that took only a width would make the
 * north-west handle two writes that race each other.
 */
export function resizeItem(
  layout: ViewLayout,
  itemId: string,
  geometry: ItemGeometry,
): ViewLayout {
  const items = readCanvasItems(layout);
  const item = requireItem(items, itemId);
  const resized = withGeometry(item, geometry);
  return storeItems(
    layout,
    items.map((each) => (each.id === itemId ? resized : each)),
    containersOf(layout),
  );
}

/**
 * Restack `itemId` to the front or the back.
 *
 * The whole stack is renumbered from 0 rather than the moved item being given
 * `max + 1`. Raising by increment is unbounded — a container focused a few
 * hundred thousand times walks `z_index` up without limit, and `int` on the wire
 * is a JSON number that stops being exact past 2^53. Renumbering keeps the
 * indices dense and the relative order intact, at the cost of writing every
 * item; the canvas is capped at 256 of them, so that cost is bounded.
 *
 * Returns the layout unchanged when the item is already where it is being sent,
 * so the "focus raises" path does not PATCH on every click of the front-most
 * container.
 */
export function restackItem(layout: ViewLayout, itemId: string, toFront: boolean): ViewLayout {
  const items = readCanvasItems(layout);
  requireItem(items, itemId);
  // `readCanvasItems` already sorted back to front, so position in this array is
  // the stacking order and the moved item's target is one end of it.
  const alreadyThere = toFront
    ? items[items.length - 1].id === itemId
    : items[0].id === itemId;
  if (alreadyThere) return layout;

  const rest = items.filter((item) => item.id !== itemId);
  const moved = requireItem(items, itemId);
  const ordered = toFront ? [...rest, moved] : [moved, ...rest];
  return storeItems(
    layout,
    ordered.map((item, index) => ({ ...item, z_index: index })),
    containersOf(layout),
  );
}

/**
 * Remove `itemId` from the canvas, and drop the container it placed.
 *
 * The container key goes with the item: a container no arrangement references is
 * a 400, not a harmless leftover. Removing the last item leaves an empty canvas,
 * which — unlike an empty grid — is a layout the server accepts.
 */
export function removeItem(layout: ViewLayout, itemId: string): ViewLayout {
  const items = readCanvasItems(layout);
  const item = requireItem(items, itemId);
  const containers = { ...containersOf(layout) };
  delete containers[item.container_id];
  return storeItems(
    layout,
    items.filter((each) => each.id !== itemId),
    containers,
  );
}

/** The box every item fits inside, or `undefined` on an empty canvas. */
export function contentBounds(
  items: CanvasItemModel[],
): { x: number; y: number; width: number; height: number } | undefined {
  if (items.length === 0) return undefined;
  const left = Math.min(...items.map((item) => item.x));
  const top = Math.min(...items.map((item) => item.y));
  const right = Math.max(...items.map((item) => item.x + item.width));
  const bottom = Math.max(...items.map((item) => item.y + item.height));
  return { x: left, y: top, width: right - left, height: bottom - top };
}
