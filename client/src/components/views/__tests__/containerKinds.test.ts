/**
 * The container-kind vocabulary, and the one reader that checks a stored string
 * against it.
 *
 * `kind` arrives from storage as `unknown`, and `ContainerPrimitiveHost` treats
 * a kind it was handed as a claim to *check* — so the narrowing has to reject
 * everything outside the vocabulary rather than pass a plausible string through.
 */

import { CONTAINER_KINDS, containerKindOf } from "../primitives/types";
import { CONTAINER_PRIMITIVES } from "../primitives/registry";

describe("CONTAINER_KINDS", () => {
  it("is the server's ContainerKind enum, and the registry stays inside it", () => {
    expect([...CONTAINER_KINDS]).toEqual(["terminal", "panel", "web_embed"]);
    for (const entry of CONTAINER_PRIMITIVES) {
      expect(CONTAINER_KINDS).toContain(entry.containerKind);
    }
  });
});

describe("containerKindOf", () => {
  it("answers with every kind in the vocabulary", () => {
    for (const kind of CONTAINER_KINDS) {
      expect(containerKindOf(kind)).toBe(kind);
    }
  });

  it("refuses anything outside it", () => {
    // A kind the host cannot check is not a kind: passing an unknown string
    // through would make the mismatch check compare two strings neither of
    // which the registry owns.
    for (const value of ["", "Terminal", "webembed", "panel ", 0, null, undefined, {}, ["panel"]]) {
      expect(containerKindOf(value)).toBeUndefined();
    }
  });
});
