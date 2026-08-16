/**
 * Where a canvas was last looked at, and what happens when that record is
 * missing, corrupt, or unwritable.
 *
 * AC8 asks that pan and zoom be restored when returning to a canvas. These cases
 * pin the storage contract that makes it possible; that the surface then applies
 * the restored values is `ViewPageCanvas.test.tsx`.
 *
 * The scope this satisfies is deliberate and worth stating: `CanvasLayout` is
 * `extra="forbid"` server-side, so the viewport cannot ride in the layout, and no
 * per-user column exists. It is therefore restored **per device**, not per
 * account.
 */

import {
  HOME_VIEWPORT,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  readViewport,
  writeViewport,
} from "../canvasViewport";

const SLUG = "loregarden";
const VIEW_ID = "v-canvas";

beforeEach(() => {
  window.localStorage.clear();
});

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
  it("round-trips what was written", () => {
    writeViewport(SLUG, VIEW_ID, { panX: 320, panY: 180, zoom: 1.5 });
    expect(readViewport(SLUG, VIEW_ID)).toEqual({ panX: 320, panY: 180, zoom: 1.5 });
  });

  it("keeps one canvas's viewport out of another's", () => {
    writeViewport(SLUG, "v-a", { panX: 100, panY: 0, zoom: 2 });
    expect(readViewport(SLUG, "v-b")).toEqual(HOME_VIEWPORT);
  });

  it("keeps one workspace's viewport out of another's", () => {
    // View ids are unique per workspace, not globally: two workspaces can hold
    // the same id, and a shared key would restore the wrong pan.
    writeViewport("blobert", VIEW_ID, { panX: 100, panY: 0, zoom: 2 });
    expect(readViewport(SLUG, VIEW_ID)).toEqual(HOME_VIEWPORT);
  });

  it("starts at the origin when nothing was ever stored", () => {
    expect(readViewport(SLUG, VIEW_ID)).toEqual(HOME_VIEWPORT);
  });

  it("starts at the origin rather than throwing on a record that is not JSON", () => {
    // A canvas that will not open because it could not remember where it was is
    // worse than one that opens at the origin.
    window.localStorage.setItem(`loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`, "{not json");
    expect(readViewport(SLUG, VIEW_ID)).toEqual(HOME_VIEWPORT);
  });

  it("ignores a stored value of the wrong shape", () => {
    window.localStorage.setItem(`loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`, "[1,2,3]");
    expect(readViewport(SLUG, VIEW_ID)).toEqual(HOME_VIEWPORT);
  });

  it("replaces a non-finite stored number rather than restoring it", () => {
    // `JSON.stringify(Infinity)` is `null`, so this is reachable from a write
    // that went wrong as well as from a hand-edited store.
    window.localStorage.setItem(
      `loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`,
      '{"panX":null,"panY":"far","zoom":null}',
    );
    expect(readViewport(SLUG, VIEW_ID)).toEqual(HOME_VIEWPORT);
  });

  it("clamps a stored zoom outside the drawable range", () => {
    window.localStorage.setItem(
      `loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`,
      '{"panX":0,"panY":0,"zoom":9999}',
    );
    expect(readViewport(SLUG, VIEW_ID).zoom).toBe(MAX_ZOOM);
  });

  it("refuses a negative pan, which no scroll position can be", () => {
    window.localStorage.setItem(
      `loregarden.canvas-viewport.${SLUG}.${VIEW_ID}`,
      '{"panX":-40,"panY":-40,"zoom":1}',
    );
    expect(readViewport(SLUG, VIEW_ID)).toMatchObject({ panX: 0, panY: 0 });
  });
});

describe("when the store itself fails", () => {
  it("survives a read that throws", () => {
    // Safari private browsing, a disabled origin, a quota. The user loses their
    // scroll position; they must not lose the canvas.
    const getItem = jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    try {
      expect(readViewport(SLUG, VIEW_ID)).toEqual(HOME_VIEWPORT);
    } finally {
      getItem.mockRestore();
    }
  });

  it("survives a write that throws", () => {
    const setItem = jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    try {
      expect(() => writeViewport(SLUG, VIEW_ID, { panX: 1, panY: 1, zoom: 1 })).not.toThrow();
    } finally {
      setItem.mockRestore();
    }
  });

  it("writes nothing at all without a view to key it by", () => {
    // Outside the view route there is no id, and a shared key would have every
    // canvas restore the last one's pan.
    writeViewport(SLUG, "", { panX: 50, panY: 50, zoom: 2 });
    expect(window.localStorage.length).toBe(0);
  });
});
