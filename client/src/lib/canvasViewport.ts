/**
 * Where a canvas was last looked at: the pan offset and the zoom.
 *
 * **Where it is stored.** In the view record, in its own `viewport` field —
 * a `viewport_json` column beside `layout_json` rather than a key inside it
 * (480). Two reasons, both the server's: `CanvasLayout` is `extra="forbid"`, so
 * a `viewport` key alongside `containers` and `items` is a 422; and the layout
 * column is capped at 256,000 bytes and returned whole by `GET /views`, so
 * folding a value that changes on every pan into it would make each gesture
 * rewrite the column the cap governs.
 *
 * It used to live in `localStorage`, which restored the viewport per *device*.
 * Stored on the record it follows the account, and a canvas opened on another
 * machine opens where it was left. The consequence of the move, stated because
 * nothing migrates it: a viewport written by the old build stays in that
 * machine's `localStorage` and is not read again, so each canvas opens once at
 * its default before the first pan stores a new one.
 *
 * **Reading is total.** A record whose viewport is absent, is not an object, or
 * holds a non-finite number reads as the home viewport rather than throwing. A
 * canvas that will not open because it could not remember where it was is worse
 * than one that opens at the origin, and `{}` — no stored position — is a state
 * the server serves deliberately.
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

function readNumber(source: Record<string, unknown>, field: string): number | undefined {
  const value = source[field];
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return value;
}

/**
 * The stored viewport as the surface's own shape, or the home one.
 *
 * The wire is snake_case because the whole view record is; the surface speaks
 * `panX`/`panY`, so the translation happens here rather than in the renderer.
 * Pan is floored at zero: it is a scroll offset, and no element scrolls to a
 * negative one — a stored negative would be silently clamped by the browser and
 * then written back as the clamped value anyway.
 */
export function readViewport(stored: unknown): CanvasViewport {
  if (typeof stored !== "object" || stored === null || Array.isArray(stored)) {
    return HOME_VIEWPORT;
  }
  const source = stored as Record<string, unknown>;
  const panX = readNumber(source, "pan_x");
  const panY = readNumber(source, "pan_y");
  const zoom = readNumber(source, "zoom");
  return {
    panX: panX === undefined ? HOME_VIEWPORT.panX : Math.max(0, panX),
    panY: panY === undefined ? HOME_VIEWPORT.panY : Math.max(0, panY),
    zoom: zoom === undefined ? HOME_VIEWPORT.zoom : clampZoom(zoom),
  };
}

/**
 * The viewport as the server's body: all three fields, none of them optional.
 *
 * The server requires the three together, because a viewport carrying only a
 * zoom would store a pan of 0 the client never asked for.
 */
export function viewportPatch(viewport: CanvasViewport): {
  pan_x: number;
  pan_y: number;
  zoom: number;
} {
  return { pan_x: viewport.panX, pan_y: viewport.panY, zoom: viewport.zoom };
}
