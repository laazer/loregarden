/**
 * Adding a configured pane to a layout that already exists.
 *
 * Every result is checked against the server's own rules through
 * `assertServerAcceptableLayout` — the same mirror the rest of the view suites
 * use — because the failure this replaces is a PATCH that comes back 400 as a
 * silent autosave, and a hand-written expectation about tree shape cannot catch
 * that.
 */

import { withPrimitivePane } from "../addPrimitivePane";
import { emptyLayoutFor } from "../viewLayouts";
import type { ViewLayout } from "../viewsApi";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";
import {
  containerWithSettings,
  newContainerFor,
} from "../../components/views/primitives/registry";

const noValues = new Map<string, unknown>();

/** The containers a layout holds, keyed as the wire keys them. */
function containers(layout: ViewLayout): Record<string, { settings: Record<string, unknown> }> {
  return layout.containers as unknown as Record<string, { settings: Record<string, unknown> }>;
}

function primitiveIds(layout: ViewLayout): string[] {
  return Object.values(containers(layout))
    .map((container) => container.settings.primitive_id)
    .filter((id): id is string => typeof id === "string")
    .sort();
}

describe("a grid", () => {
  it("fills the empty pane a fresh tab opens with, rather than splitting it", () => {
    // A new tab is exactly one empty pane. Splitting it would open the board
    // beside a pane nobody asked for.
    const seed = emptyLayoutFor("flex_grid");
    const next = withPrimitivePane(seed, "chat_kanban", noValues);

    expect(primitiveIds(next)).toEqual(["chat_kanban"]);
    expect(Object.keys(containers(next))).toHaveLength(1);
    expect(next.root).toMatchObject({ node: "leaf" });
    assertServerAcceptableLayout(next);
  });

  it("splits the root when the only pane is already configured", () => {
    const configured = withPrimitivePane(emptyLayoutFor("flex_grid"), "chat_kanban", noValues);
    const next = withPrimitivePane(configured, "queue_lane", new Map([["slot", "2"]]));

    expect(primitiveIds(next)).toEqual(["chat_kanban", "queue_lane"]);
    expect(next.root).toMatchObject({ node: "split", orientation: "horizontal" });
    assertServerAcceptableLayout(next);
  });

  it("stores the settings the surface passed in", () => {
    const next = withPrimitivePane(
      emptyLayoutFor("flex_grid"),
      "queue_lane",
      new Map([["slot", "3"]]),
    );
    expect(Object.values(containers(next))[0].settings).toMatchObject({
      primitive_id: "queue_lane",
      slot: "3",
    });
  });

  it("stamps the kind the primitive belongs to, not the seed's", () => {
    // The seed pane is stored as `panel`; a terminal stored under `panel` is a
    // disagreement `ContainerPrimitiveHost` refuses to mount. The registry
    // composes the whole container for exactly this reason.
    const next = withPrimitivePane(emptyLayoutFor("flex_grid"), "terminal", noValues);
    expect(Object.values(next.containers as Record<string, { kind: string }>)[0].kind).toBe(
      "terminal",
    );
    assertServerAcceptableLayout(next);
  });

  it("keeps adding without breaking the sibling-sum rule", () => {
    let layout = emptyLayoutFor("flex_grid");
    for (const id of ["chat_kanban", "queue_lane", "terminal", "chat_ticket"]) {
      layout = withPrimitivePane(layout, id, noValues);
      assertServerAcceptableLayout(layout);
    }
    expect(primitiveIds(layout)).toHaveLength(4);
  });

  it("leaves the layout it was handed untouched", () => {
    // The input is the record react-query is holding and the panes are drawn
    // from.
    const seed = withPrimitivePane(emptyLayoutFor("flex_grid"), "chat_kanban", noValues);
    const before = JSON.stringify(seed);
    withPrimitivePane(seed, "terminal", noValues);
    expect(JSON.stringify(seed)).toBe(before);
  });
});

describe("a canvas", () => {
  it("places the pane rather than filling anything", () => {
    const next = withPrimitivePane(emptyLayoutFor("canvas"), "chat_kanban", noValues);
    expect(primitiveIds(next)).toEqual(["chat_kanban"]);
    expect(next.items).toHaveLength(1);
    assertServerAcceptableLayout(next);
  });

  it("cascades each addition clear of the last", () => {
    // Every addition from elsewhere in the app arrives at the same coordinate
    // otherwise, and each one hides the one before it.
    let layout = emptyLayoutFor("canvas");
    layout = withPrimitivePane(layout, "chat_kanban", noValues);
    layout = withPrimitivePane(layout, "queue_lane", noValues);

    const items = layout.items as unknown as { x: number; y: number }[];
    expect(items[0]).toMatchObject({ x: 0, y: 0 });
    expect(items[1].x).toBeGreaterThan(items[0].x);
    expect(items[1].y).toBeGreaterThan(items[0].y);
    assertServerAcceptableLayout(layout);
  });
});

describe("a primitive this build does not have", () => {
  it("throws rather than returning the layout unchanged", () => {
    // The caller named it, so an unknown id is a bug in the caller — and a
    // silent no-op would read as a save that worked.
    expect(() => withPrimitivePane(emptyLayoutFor("flex_grid"), "nope", noValues)).toThrow(
      /no primitive called nope/,
    );
  });
});

describe("the container it stores", () => {
  it("is the one the registry composes for a pane picked by hand", () => {
    // Same function, so a pane added from the Dashboard and a pane picked in
    // the view are the same record — there is no second way to build one.
    const fromMenu = withPrimitivePane(emptyLayoutFor("flex_grid"), "chat_kanban", noValues);
    expect(Object.values(containers(fromMenu))[0]).toEqual(newContainerFor("chat_kanban"));

    const withSettings = withPrimitivePane(
      emptyLayoutFor("flex_grid"),
      "queue_lane",
      new Map([["slot", "2"]]),
    );
    expect(Object.values(containers(withSettings))[0]).toEqual(
      containerWithSettings("queue_lane", new Map([["slot", "2"]])),
    );
  });
});
