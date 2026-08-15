/**
 * The layout a new view is seeded with, and the copy a duplicate posts.
 *
 * Written before `client/src/lib/viewLayouts.ts` exists, so every failure here
 * is currently a missing module. Two acceptance criteria live in these two pure
 * functions, and both are easier to get wrong than they look:
 *
 *   - **AC6** — "a newly created flex grid opens with one empty container
 *     prompting for a primitive, not an empty screen". The obvious seed is
 *     `containers: {}`, and the server refuses it: `FlexGridLayout` requires a
 *     `root`, a leaf root must name a container, and `_StructureWalk.finish`
 *     rejects any container nothing references. So "empty grid" is not an empty
 *     registry — it is exactly one container that has not chosen a primitive
 *     yet. A canvas genuinely may be empty; the two are not symmetric.
 *   - **AC7** — "duplicate works and persists through the API". There is no
 *     copy endpoint; duplicate is a client-side deep copy re-POSTed. Container
 *     ids are the keys of the layout's own registry, so a copy that reuses them
 *     is *accepted* by the server and only breaks later, when the two views
 *     turn out to alias each other in every cache keyed by container id. Fresh
 *     keys, with every reference rewritten to match, is the assertion.
 *
 * The structural checks come from `src/test/viewLayoutContract.ts`, which
 * transcribes `server/loregarden/models/domain/view_layout.py` once for every
 * suite that composes a layout. A layout it refuses is a 400 at runtime, and a
 * unit test is the cheapest place to find that out.
 */

import { assertServerAcceptableLayout, CONTAINER_KINDS } from "../../test/viewLayoutContract";
import { duplicateLayout, emptyLayoutFor } from "../viewLayouts";

type Json = Record<string, unknown>;

describe("AC6 — an empty flex grid is one unconfigured container, not an empty registry", () => {
  it("seeds exactly one container, referenced by a full-size root leaf", () => {
    const layout = emptyLayoutFor("flex_grid") as Json;

    expect(layout.kind).toBe("flex_grid");
    const containers = layout.containers as Record<string, Json>;
    expect(Object.keys(containers)).toHaveLength(1);

    const root = layout.root as Json;
    expect(root.node).toBe("leaf");
    expect(root.size).toBe(1);
    expect(root.container_id).toBe(Object.keys(containers)[0]);

    assertServerAcceptableLayout(layout);
  });

  it("leaves the seeded container without a primitive, so the view can prompt for one", () => {
    // The prompt is the point of AC6. A seed that already named a primitive
    // would open on somebody else's choice of pane; a seed stored under a kind
    // that disagrees with a primitive it later gains is refused by
    // `ContainerPrimitiveHost`, which is why the placeholder kind is `panel` —
    // the neutral one — and why the picker *replaces* the container rather than
    // merging settings into it (asserted in ViewPage.test.tsx).
    const layout = emptyLayoutFor("flex_grid") as Json;
    const container = Object.values(layout.containers as Record<string, Json>)[0];

    expect(CONTAINER_KINDS).toContain(container.kind);
    expect(container.kind).toBe("panel");
    // No `primitive_id` at all — not an empty string, which `getPrimitive`
    // would treat as a stored id it cannot resolve and render as the
    // "primitive this build does not have" placeholder.
    expect(container.settings).toEqual({});
  });

  it("gives every new grid its own container id", () => {
    // Two grids seeded from one frozen constant would share a container id, and
    // every cache keyed by container id would alias them.
    const first = emptyLayoutFor("flex_grid") as Json;
    const second = emptyLayoutFor("flex_grid") as Json;

    expect(Object.keys(first.containers as Json)).not.toEqual(
      Object.keys(second.containers as Json),
    );
    expect((first.root as Json).id).not.toBe((second.root as Json).id);
  });

  it("returns a fresh object each call, so one view's edits cannot reach another's seed", () => {
    // A module-level constant returned by reference passes the id test above if
    // the ids are generated once — and then the first `updateView` that edits
    // the seed in place changes what every later `New View` posts.
    const first = emptyLayoutFor("flex_grid") as Json;
    const second = emptyLayoutFor("flex_grid") as Json;
    expect(first).not.toBe(second);
    expect(first.containers).not.toBe(second.containers);
    expect(first.root).not.toBe(second.root);
  });

  it("seeds a canvas as a genuinely empty surface", () => {
    // Not symmetric with the grid, and deliberately so: `CanvasLayout` has no
    // required root, so `{containers: {}, items: []}` passes the same walk that
    // refuses an empty grid. Seeding a canvas with a container it did not ask
    // for would put a pane on a surface whose whole point is free placement.
    const layout = emptyLayoutFor("canvas") as Json;

    expect(layout.kind).toBe("canvas");
    expect(layout.containers).toEqual({});
    expect(layout.items).toEqual([]);
    assertServerAcceptableLayout(layout);
  });
});

describe("AC7 — duplicate deep-copies a layout under fresh container ids", () => {
  const gridLayout = () =>
    ({
      kind: "flex_grid",
      containers: {
        "c-left": { kind: "terminal", settings: { primitive_id: "terminal", cwd: "/tmp" } },
        "c-right": { kind: "panel", settings: { primitive_id: "run_ledger", ticket_id: "t-1" } },
      },
      root: {
        node: "split",
        id: "n-root",
        size: 1,
        orientation: "horizontal",
        children: [
          { node: "leaf", id: "n-left", size: 0.5, container_id: "c-left" },
          { node: "leaf", id: "n-right", size: 0.5, container_id: "c-right" },
        ],
      },
    }) as Json;

  const canvasLayout = () =>
    ({
      kind: "canvas",
      containers: {
        "c-a": { kind: "web_embed", settings: { primitive_id: "web_embed", url: "" } },
      },
      items: [{ id: "i-a", container_id: "c-a", x: 10, y: 20, width: 300, height: 200, z_index: 0 }],
    }) as Json;

  // The fixtures themselves have to be layouts the server accepts, or a copy
  // that faithfully reproduces a malformed source would "pass".
  it("the fixtures this suite copies are layouts the server accepts", () => {
    assertServerAcceptableLayout(gridLayout());
    assertServerAcceptableLayout(canvasLayout());
  });

  it("regenerates every container key and rewrites the references to match", () => {
    const source = gridLayout();
    const copy = duplicateLayout(source) as Json;

    const sourceKeys = Object.keys(source.containers as Json);
    const copyKeys = Object.keys(copy.containers as Json);

    expect(copyKeys).toHaveLength(sourceKeys.length);
    // Fresh, not renamed-in-place: no key survives.
    expect(copyKeys.filter((key) => sourceKeys.includes(key))).toEqual([]);
    // And the arrangement points at the new keys, not the old ones — the
    // failure mode where ids are regenerated but references are not is a layout
    // the server rejects outright ("Node references unknown container").
    assertServerAcceptableLayout(copy);
  });

  it("gives two duplicates of one source different container ids", () => {
    // A "fresh id" derived from the source (`${key}-copy`) passes the test
    // above and aliases every duplicate of the same view with every other.
    const source = gridLayout();
    const first = Object.keys((duplicateLayout(source) as Json).containers as Json);
    const second = Object.keys((duplicateLayout(source) as Json).containers as Json);
    expect(first.filter((key) => second.includes(key))).toEqual([]);
  });

  it("preserves the arrangement and every container's settings", () => {
    const copy = duplicateLayout(gridLayout()) as Json;
    const root = copy.root as Json;

    expect(root.node).toBe("split");
    expect(root.orientation).toBe("horizontal");
    expect((root.children as Json[]).map((child) => child.size)).toEqual([0.5, 0.5]);

    // Settings are copied verbatim: a duplicate of a configured terminal is a
    // configured terminal, not a fresh unconfigured pane.
    const copied = Object.values(copy.containers as Record<string, Json>);
    expect(copied.map((container) => container.kind).sort()).toEqual(["panel", "terminal"]);
    expect(copied.map((container) => container.settings)).toEqual(
      expect.arrayContaining([
        { primitive_id: "terminal", cwd: "/tmp" },
        { primitive_id: "run_ledger", ticket_id: "t-1" },
      ]),
    );
    // The pane that was on the left is still on the left: a copy that kept both
    // containers but swapped which leaf points at which is a different layout.
    const leaves = (root.children as Json[]).map((child) => child.container_id as string);
    const byKey = copy.containers as Record<string, Json>;
    expect((byKey[leaves[0]].settings as Json).primitive_id).toBe("terminal");
    expect((byKey[leaves[1]].settings as Json).primitive_id).toBe("run_ledger");
  });

  it("copies deeply, so editing the duplicate cannot reach the original", () => {
    const source = gridLayout();
    const snapshot = JSON.parse(JSON.stringify(source));
    const copy = duplicateLayout(source) as Json;

    for (const container of Object.values(copy.containers as Record<string, Json>)) {
      (container.settings as Json).cwd = "/somewhere-else";
    }
    (copy.root as Json).orientation = "vertical";
    ((copy.root as Json).children as Json[])[0].size = 0.9;

    expect(source).toEqual(snapshot);
  });

  it("rewrites canvas item references too", () => {
    const source = canvasLayout();
    const copy = duplicateLayout(source) as Json;

    const newKey = Object.keys(copy.containers as Json)[0];
    expect(newKey).not.toBe("c-a");
    expect((copy.items as Json[])[0].container_id).toBe(newKey);
    // Geometry is part of the layout the user arranged, so it survives.
    expect((copy.items as Json[])[0]).toMatchObject({ x: 10, y: 20, width: 300, height: 200 });
    assertServerAcceptableLayout(copy);
  });

  it("duplicating an empty grid still yields a grid the server accepts", () => {
    // The realistic duplicate: a view created a moment ago and never filled in.
    const copy = duplicateLayout(emptyLayoutFor("flex_grid")) as Json;
    expect(Object.keys(copy.containers as Json)).toHaveLength(1);
    assertServerAcceptableLayout(copy);
  });

  it("duplicating an empty canvas keeps it empty", () => {
    const copy = duplicateLayout(emptyLayoutFor("canvas")) as Json;
    expect(copy.kind).toBe("canvas");
    expect(copy.containers).toEqual({});
    expect(copy.items).toEqual([]);
    assertServerAcceptableLayout(copy);
  });
});
