/**
 * The map from primitives to the pages they already live on.
 *
 * Its whole value is being complete, so the test is the completeness check —
 * both directions. A primitive added without an entry has no way into a view
 * from the app, and an entry left behind by a deleted primitive is a menu
 * pointing at nothing.
 */

import { PRIMITIVE_HOMES, PRIMITIVE_HOME_IDS, homeOf } from "../primitives/primitiveHomes";
import { CONTAINER_PRIMITIVES } from "../primitives/registry";

describe("PRIMITIVE_HOMES", () => {
  it("names every registered primitive, and nothing else", () => {
    expect([...PRIMITIVE_HOME_IDS].sort()).toEqual(
      CONTAINER_PRIMITIVES.map((entry) => entry.id).sort(),
    );
  });

  it("agrees with its own vocabulary", () => {
    // The keys are typed from `PRIMITIVE_HOME_IDS`, so this can only fail if
    // the list and the record are edited apart at runtime — which a JSON import
    // or a spread would do without TypeScript noticing.
    expect(Object.keys(PRIMITIVE_HOMES).sort()).toEqual([...PRIMITIVE_HOME_IDS].sort());
  });

  it("gives every home a route and a name in the app's own words", () => {
    for (const [id, home] of Object.entries(PRIMITIVE_HOMES)) {
      if (home === null) continue;
      expect({ id, path: home.path.startsWith("/") }).toEqual({ id, path: true });
      expect({ id, surface: home.surface.length > 0 }).toEqual({ id, surface: true });
    }
  });

  it("records the homeless as homeless rather than missing", () => {
    // `web_embed` points at an arbitrary URL; no page here is the page about it.
    // `null` and "not in the map" are different answers and `homeOf` keeps them
    // apart, because only one of them is a bug.
    expect(homeOf("web_embed")).toBeNull();
    expect(homeOf("not_a_primitive")).toBeUndefined();
    expect(homeOf("queue_lane")).toMatchObject({ path: "/queue" });
  });

  it("cannot be tricked into answering for a prototype key", () => {
    // The id reaching `homeOf` can come from stored settings text.
    expect(homeOf("constructor")).toBeUndefined();
    expect(homeOf("__proto__")).toBeUndefined();
  });
});
