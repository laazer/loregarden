/**
 * AC2 — "the primitive picker is generated from the registry; adding a
 * primitive requires no change to grid or canvas code."
 *
 * The second half is an architectural claim, and the grid (440) and canvas
 * (442) do not exist yet, so there is no grid module to diff. Two assertions
 * stand in for it, and both bite:
 *
 *   1. The picker's contents are *derived*, not authored. `PrimitivePicker`
 *      takes an optional `entries` prop defaulting to `CONTAINER_PRIMITIVES`;
 *      handing it a registry with one extra entry must make the picker offer
 *      that entry, with nothing else edited. A picker with a hardcoded list
 *      passes every "renders the three primitives" test and fails this one.
 *   2. The dispatcher is the only door. No module outside
 *      `views/primitives/` may import a concrete primitive component — a view
 *      that reached past `ContainerPrimitiveHost` to name `TerminalPrimitive`
 *      is exactly the coupling AC2 forbids, and it is the first thing an
 *      implementer does when the host is awkward.
 */

import fs from "fs";
import path from "path";

import { fireEvent, render, screen } from "@testing-library/react";

import { PrimitivePicker } from "../PrimitivePicker";
import { definePrimitive } from "../primitives/definePrimitive";
import { CONTAINER_PRIMITIVES } from "../primitives/registry";

const noop = () => {};

/** A display name is prose, and prose contains regex metacharacters. */
function nameMatcher(displayName: string): RegExp {
  return new RegExp(displayName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
}

const SYNTHETIC = definePrimitive<{ label: string }>({
  id: "synthetic_probe",
  displayName: "Synthetic Probe",
  icon: "🧪",
  category: "panel",
  containerKind: "panel",
  settingsFields: [{ key: "label", kind: "string", label: "Label", default: "" }],
  parseSettings: (raw) => ({ label: typeof raw.label === "string" ? raw.label : "" }),
  Component: ({ settings }) => <div>{settings.label}</div>,
});

describe("AC2 — the picker is generated from the registry", () => {
  it("offers exactly one entry per registered primitive", () => {
    // Options are identified by `data-primitive-id` — the same attribute the
    // host carries — so counting them is not thrown off by whatever chrome the
    // picker grows later.
    const { container } = render(<PrimitivePicker legend="Choose a primitive" onPick={noop} />);
    expect(container.querySelectorAll("[data-primitive-id]")).toHaveLength(
      CONTAINER_PRIMITIVES.length,
    );
    // Matched by id, and the *name* is then read off that option. A substring
    // role query stood here until 557 registered sixteen primitives: /Ticket/i
    // matches "Ticket", "Ticket List", "Ticket Workflow" and "Parent Ticket"
    // alike, so the query threw on ambiguity rather than checking anything.
    // Pinning the id-to-name pairing is what the criterion wanted anyway.
    for (const entry of CONTAINER_PRIMITIVES) {
      const option = container.querySelector(`[data-primitive-id="${entry.id}"]`);
      expect({ id: entry.id, found: option !== null }).toEqual({ id: entry.id, found: true });
      expect(option).toHaveTextContent(nameMatcher(entry.displayName));
    }
  });

  it("offers a primitive that was registered after the picker was written", () => {
    // The whole of AC2's second clause, expressed as something observable: the
    // only change is an added registry entry.
    const { container } = render(
      <PrimitivePicker
        entries={[...CONTAINER_PRIMITIVES, SYNTHETIC]}
        legend="Choose a primitive"
        onPick={noop}
      />,
    );
    expect(container.querySelectorAll("[data-primitive-id]")).toHaveLength(
      CONTAINER_PRIMITIVES.length + 1,
    );
    expect(container.querySelector("[data-primitive-id='synthetic_probe']")).not.toBeNull();
    expect(screen.getByRole("button", { name: /Synthetic Probe/i })).toBeInTheDocument();
  });

  it("hands back the picked primitive's id, not an index into a list", () => {
    const picked: string[] = [];
    render(
      <PrimitivePicker
        entries={[...CONTAINER_PRIMITIVES, SYNTHETIC]}
        legend="Choose a primitive"
        onPick={(id) => picked.push(id)}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Synthetic Probe/i }));
    expect(picked).toEqual(["synthetic_probe"]);
  });

  it("names no primitive id of its own", () => {
    const source = fs.readFileSync(
      path.resolve(__dirname, "../PrimitivePicker.tsx"),
      "utf8",
    );
    for (const entry of CONTAINER_PRIMITIVES) {
      const hardcoded = new RegExp(`["'\`]${entry.id}["'\`]`).test(source);
      expect({ id: entry.id, hardcoded }).toEqual({ id: entry.id, hardcoded: false });
    }
  });
});

describe("AC2 — the dispatcher is the only door to a concrete primitive", () => {
  it("is the only module importing a primitive component", () => {
    const primitivesDir = path.resolve(__dirname, "../primitives");

    // Which modules hold the concrete primitives is the implementer's layout
    // choice, so this does not demand a `*Primitive.tsx` naming scheme or a
    // flat directory — it takes every module under `primitives/` that is not
    // one of the shared pieces the rest of the app is *allowed* to import.
    // `primitiveHomes` joins the shared list on the same footing as
    // `excludedPanels`: metadata *about* primitives, importing no component and
    // holding no rendering. `AddToTabMenu` reads it to decide which surfaces
    // may offer a primitive at all, which is a question outside this directory.
    const SHARED = [
      "registry",
      "types",
      "definePrimitive",
      "excludedPanels",
      "embedUrl",
      "primitiveHomes",
      "index",
    ];
    const primitiveModules: string[] = [];
    const collect = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) collect(full);
        else if (/\.tsx?$/.test(entry.name) && !/\.d\.ts$/.test(entry.name)) {
          const stem = entry.name.replace(/\.tsx?$/, "");
          if (!SHARED.includes(stem)) primitiveModules.push(full.replace(/\.tsx?$/, ""));
        }
      }
    };
    collect(primitivesDir);
    expect(primitiveModules.length).toBeGreaterThanOrEqual(3);

    const srcRoot = path.resolve(__dirname, "../../..");
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__" && entry.name !== "node_modules") walk(full);
        } else if (/\.tsx?$/.test(entry.name)) {
          files.push(full);
        }
      }
    };
    walk(srcRoot);

    for (const file of files) {
      // A primitive importing its sibling is internal business; the rule is
      // about the rest of the app, so the whole subtree is exempt, not just
      // the one directory.
      if (file.startsWith(primitivesDir + path.sep)) continue;
      const source = fs.readFileSync(file, "utf8");
      // Resolved, not name-matched. `components/chat/primitives/` already has a
      // `TerminalPrimitive` of its own, and a bare-name regex calls that a
      // violation — a test that fails a correct implementation over a filename
      // it does not own.
      for (const [, specifier] of source.matchAll(/from\s*["']([^"']+)["']/g)) {
        if (!specifier.startsWith(".")) continue;
        const resolved = path.resolve(path.dirname(file), specifier).replace(/\.tsx?$/, "");
        const imports = primitiveModules.includes(resolved);
        expect({ file, specifier, imports }).toEqual({ file, specifier, imports: false });
      }
    }
  });
});
