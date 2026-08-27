/**
 * Reading a view record's stored viewport, and composing the body that stores
 * one.
 *
 * The viewport lives on the record now — its own `viewport` field, from the
 * `viewport_json` column 480 adds — rather than in `localStorage`, so it is
 * restored per account rather than per device. What these cases pin is the
 * translation at that boundary: the wire is snake_case and total-read, the
 * surface speaks `panX`/`panY`, and `{}` is the server's spelling of "no stored
 * position".
 *
 * That the *surface* then applies the restored values, and writes a new one when
 * it stops moving, is `ViewPageCanvas.test.tsx`. That the two fields are
 * independently settable is the server's, in `test_views_api.py`.
 */

import {
  HOME_VIEWPORT,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  readViewport,
  viewportPatch,
} from "../canvasViewport";

describe("clampZoom", () => {
  it("holds zoom inside a range a surface can be drawn at", () => {
    expect(clampZoom(0.0001)).toBe(MIN_ZOOM);
    expect(clampZoom(1000)).toBe(MAX_ZOOM);
    expect(clampZoom(1)).toBe(1);
  });

  it("answers 100% for a zoom that is not a number at all", () => {
    // A zoom computation that divided by a zero-sized viewport produces this,
    // and `Infinity` in a transform is a surface with nothing on it.
    expect(clampZoom(Number.NaN)).toBe(1);
    expect(clampZoom(Infinity)).toBe(1);
  });
});

describe("reading a stored viewport", () => {
  it("reads the record's pan and zoom as the surface's own shape", () => {
    expect(readViewport({ pan_x: 320, pan_y: 180, zoom: 1.5 })).toEqual({
      panX: 320,
      panY: 180,
      zoom: 1.5,
    });
  });

  it("opens at the origin for a view with no stored position", () => {
    // `{}` is what the server stores for a canvas nobody has panned, and what
    // every view composed before this column holds.
    expect(readViewport({})).toEqual(HOME_VIEWPORT);
  });

  it("opens at the origin rather than throwing on a value of the wrong shape", () => {
    // A canvas that will not open because it could not read where it was is
    // worse than one that opens at the origin. Reachable from a hand-edited
    // row, and from any future build that widens the field.
    expect(readViewport(undefined)).toEqual(HOME_VIEWPORT);
    expect(readViewport(null)).toEqual(HOME_VIEWPORT);
    expect(readViewport([1, 2, 3])).toEqual(HOME_VIEWPORT);
    expect(readViewport("far left")).toEqual(HOME_VIEWPORT);
  });

  it("replaces a non-finite stored number rather than restoring it", () => {
    // JSON has no `Infinity`, so a value that went wrong arrives as `null` or as
    // a string. The server refuses to store either; a row predating it, or one
    // edited by hand, still has to open.
    expect(readViewport({ pan_x: null, pan_y: "far", zoom: null })).toEqual(HOME_VIEWPORT);
  });

  it("clamps a stored zoom outside the range this surface draws", () => {
    // The server's ceiling is deliberately wider than the canvas's own range, so
    // a stored zoom the surface cannot draw is a value it must narrow, not a
    // value it can assume away.
    expect(readViewport({ pan_x: 0, pan_y: 0, zoom: 99 }).zoom).toBe(MAX_ZOOM);
    expect(readViewport({ pan_x: 0, pan_y: 0, zoom: 0.001 }).zoom).toBe(MIN_ZOOM);
  });

  it("floors a negative pan, which no scroll position can be", () => {
    expect(readViewport({ pan_x: -40, pan_y: -40, zoom: 1 })).toMatchObject({ panX: 0, panY: 0 });
  });

  it("keeps the fields it can read when one of them is missing", () => {
    // A partial object is not something the server stores — the three fields are
    // required together — but the read is total, and dropping a good pan because
    // the zoom was absent would move the user for no reason.
    expect(readViewport({ pan_x: 90, pan_y: 40 })).toEqual({ panX: 90, panY: 40, zoom: 1 });
  });
});

describe("the body that stores a viewport", () => {
  it("sends all three fields under the names the server requires", () => {
    // The server's model is `extra="forbid"` with three required fields: a body
    // spelled `panX` is a 422, and one carrying only the zoom is refused rather
    // than stored with a pan the client never asked for.
    expect(viewportPatch({ panX: 320, panY: 180, zoom: 1.5 })).toEqual({
      pan_x: 320,
      pan_y: 180,
      zoom: 1.5,
    });
  });

  it("round-trips through a read", () => {
    const viewport = { panX: 512, panY: 64, zoom: 2 };
    expect(readViewport(viewportPatch(viewport))).toEqual(viewport);
  });
});
