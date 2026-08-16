/**
 * Where a canvas was last looked at: the pan offset and the zoom.
 *
 * **Why this is not in the layout.** `CanvasLayout` is `extra="forbid"`
 * server-side, so a `viewport` key alongside `containers` and `items` is a 422 —
 * persisting the viewport in the layout would take a server model change. It is
 * also the wrong home: pan and zoom are where *this person, on this screen* is
 * looking, the same category as a scroll position. Stored in the shared layout
 * they would yank a second viewer's viewport on every pan, and they would spend
 * bytes against the 256,000-byte cap that a container's `settings` already
 * competes for.
 *
 * So it lives in `localStorage`, keyed by workspace and view. The consequence,
 * stated plainly because AC8 does not distinguish them: the viewport is restored
 * per **device**, not per account. Opening the same canvas on another machine
 * starts at the origin at 100%.
 *
 * Every read is total — a key that is missing, unparseable, holds the wrong
 * shape, or holds a non-finite number returns the home viewport rather than
 * throwing. `localStorage` itself can throw (Safari private browsing, a storage
 * quota, a disabled origin), and a canvas that will not open because it could not
 * remember where it was is worse than one that opens at the origin.
 */

/** The zoom range the surface offers. Below 10% nothing is legible. */
export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 4;

/** The multiplier one zoom-in or zoom-out step applies. */
export const ZOOM_STEP = 1.2;

export interface CanvasViewport {
  /** How far the surface is scrolled, in surface pixels (pre-zoom). */
  panX: number;
  panY: number;
  zoom: number;
}

export const HOME_VIEWPORT: CanvasViewport = { panX: 0, panY: 0, zoom: 1 };

export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.min(Math.max(zoom, MIN_ZOOM), MAX_ZOOM);
}

function storageKey(slug: string, viewId: string): string {
  return `loregarden.canvas-viewport.${slug}.${viewId}`;
}

function readNumber(source: Record<string, unknown>, field: string): number | undefined {
  const value = source[field];
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return value;
}

/** The stored viewport for this view, or the home one. */
export function readViewport(slug: string, viewId: string): CanvasViewport {
  if (slug === "" || viewId === "") return HOME_VIEWPORT;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(storageKey(slug, viewId));
  } catch {
    return HOME_VIEWPORT;
  }
  if (raw === null) return HOME_VIEWPORT;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return HOME_VIEWPORT;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return HOME_VIEWPORT;
  }

  const source = parsed as Record<string, unknown>;
  const panX = readNumber(source, "panX");
  const panY = readNumber(source, "panY");
  const zoom = readNumber(source, "zoom");
  return {
    panX: panX === undefined ? HOME_VIEWPORT.panX : Math.max(0, panX),
    panY: panY === undefined ? HOME_VIEWPORT.panY : Math.max(0, panY),
    zoom: zoom === undefined ? HOME_VIEWPORT.zoom : clampZoom(zoom),
  };
}

/** Remember where this canvas is being looked at. Failure is not the user's problem. */
export function writeViewport(slug: string, viewId: string, viewport: CanvasViewport): void {
  if (slug === "" || viewId === "") return;
  try {
    window.localStorage.setItem(storageKey(slug, viewId), JSON.stringify(viewport));
  } catch {
    // A full or disabled store costs the user their scroll position on the next
    // visit. It must not cost them the pan they are in the middle of.
  }
}
