/**
 * 557 AC4 — "the picker is a modal with grouping and search, driven off the
 * registry's own category and display metadata" — and AC5, "adding a primitive
 * still requires no change to the picker, the grid, or the canvas."
 *
 * The grouping and the search are asserted against the *registry*, never
 * against a list this file writes: a test that grouped three primitives it
 * invented would pass over a picker that hardcoded thirteen names. The one
 * synthetic entry below exists for the opposite purpose — it is the only way to
 * ask "does an unknown primitive in an unknown category appear, with nothing
 * else edited", which is precisely AC5.
 */

import fs from "fs";
import path from "path";

import { fireEvent, render, screen, within } from "@testing-library/react";

import {
  PrimitivePickerModal,
  groupByCategory,
} from "../PrimitivePickerModal";
import { definePrimitive } from "../views/primitives/definePrimitive";
import { CONTAINER_PRIMITIVES } from "../views/primitives/registry";

const noop = () => {};

const SYNTHETIC = definePrimitive<{ label: string }>({
  id: "synthetic_probe",
  displayName: "Synthetic Probe",
  icon: "🧪",
  category: "Laboratory",
  containerKind: "panel",
  settingsFields: [{ key: "label", kind: "string", label: "Label", default: "" }],
  parseSettings: (raw) => ({ label: typeof raw.label === "string" ? raw.label : "" }),
  Component: ({ settings }) => <div>{settings.label}</div>,
});

function open(props: Partial<React.ComponentProps<typeof PrimitivePickerModal>> = {}) {
  return render(
    <PrimitivePickerModal
      legend="Choose a primitive"
      onPick={noop}
      onClose={noop}
      {...props}
    />,
  );
}

function visibleOptionIds(): string[] {
  const dialog = screen.getByRole("dialog");
  return Array.from(dialog.querySelectorAll("[data-primitive-id]")).map(
    (el) => el.getAttribute("data-primitive-id") ?? "",
  );
}

describe("AC4 — the picker is a dialog", () => {
  it("names itself with what it is offering", () => {
    open({ legend: "Change contents to" });
    expect(screen.getByRole("dialog", { name: "Change contents to" })).toBeInTheDocument();
  });

  it("renders outside the tree it was opened from", () => {
    /**
     * Structural, and it says so: jsdom has no layout engine, so "the modal is
     * drawn at 60% scale and 300px to the left" cannot be asked here. What can
     * be asked is the cause.
     *
     * `.modal-panel` is `position: fixed`, which resolves against the viewport
     * *until* an ancestor carries a transform — and 442's canvas sets
     * `transform: scale()` at every zoom that is not 100%. Rendered in place,
     * the dialog would be laid out inside that scaled surface. The portal is
     * what puts it outside every transformed ancestor, so this asserts that the
     * dialog is not a descendant of the element it was rendered into.
     */
    const { container } = render(
      <div style={{ transform: "scale(0.6)" }}>
        <PrimitivePickerModal legend="Choose a primitive" onPick={noop} onClose={noop} />
      </div>,
    );
    const dialog = screen.getByRole("dialog");
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });

  it("scrolls its body rather than clipping a list too long for the panel", () => {
    // 554's review found a 167px form clipped inside a 149px pane with its Save
    // button unreachable and no scrollbar. Sixteen options is the same
    // arithmetic; the dialog answers it with the app's own `.modal-body`, so
    // the declaration is what is checked.
    const css = fs.readFileSync(
      path.resolve(__dirname, "../../index.css"),
      "utf8",
    );
    const declarations: Record<string, string> = {};
    for (const [, selector, body] of css
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!selector.split(",").some((part) => /^\s*\.modal-body\s*$/.test(part))) continue;
      for (const declaration of body.split(";")) {
        const [property, ...rest] = declaration.split(":");
        if (rest.length === 0) continue;
        declarations[property.trim().toLowerCase()] = rest.join(":").trim().toLowerCase();
      }
    }
    expect(["auto", "scroll"]).toContain(declarations["overflow-y"]);
    // And the dialog actually uses it.
    open();
    expect(screen.getByRole("dialog").querySelector(".modal-body")).not.toBeNull();
  });

  it("closes on Escape and on the overlay, without picking anything", () => {
    const closed: number[] = [];
    const picked: string[] = [];
    open({ onClose: () => closed.push(1), onPick: (id) => picked.push(id) });

    fireEvent.keyDown(document, { key: "Escape" });
    expect(closed).toHaveLength(1);

    fireEvent.click(document.querySelector(".modal-overlay") as HTMLElement);
    expect(closed).toHaveLength(2);
    expect(picked).toEqual([]);
  });

  it("hands back the picked primitive's id", () => {
    const picked: string[] = [];
    open({ entries: [...CONTAINER_PRIMITIVES, SYNTHETIC], onPick: (id) => picked.push(id) });
    fireEvent.click(screen.getByRole("button", { name: /Synthetic Probe/ }));
    expect(picked).toEqual(["synthetic_probe"]);
  });
});

describe("AC4 — grouping comes from the registry's own category", () => {
  it("puts every registered primitive in a list named by its category", () => {
    open();
    for (const entry of CONTAINER_PRIMITIVES) {
      const list = screen.getByRole("list", { name: entry.category });
      const option = list.querySelector(`[data-primitive-id="${entry.id}"]`);
      expect({ id: entry.id, category: entry.category, grouped: option !== null }).toEqual({
        id: entry.id,
        category: entry.category,
        grouped: true,
      });
    }
    // One list per distinct category, not one per primitive.
    const categories = new Set(CONTAINER_PRIMITIVES.map((entry) => entry.category));
    expect(screen.getAllByRole("list")).toHaveLength(categories.size);
    expect(visibleOptionIds()).toHaveLength(CONTAINER_PRIMITIVES.length);
  });

  it("keeps a category's entries out of every other category's list", () => {
    // Grouping that renders every entry in every group would satisfy the test
    // above. This is the half that says the groups partition.
    open();
    for (const group of groupByCategory(CONTAINER_PRIMITIVES)) {
      const list = screen.getByRole("list", { name: group.category });
      const ids = Array.from(list.querySelectorAll("[data-primitive-id]")).map((el) =>
        el.getAttribute("data-primitive-id"),
      );
      expect({ category: group.category, ids }).toEqual({
        category: group.category,
        ids: group.entries.map((entry) => entry.id),
      });
    }
  });

  it("groups in registration order, so 436's three are not scattered", () => {
    expect(groupByCategory(CONTAINER_PRIMITIVES).map((group) => group.category)).toEqual(
      [...new Set(CONTAINER_PRIMITIVES.map((entry) => entry.category))],
    );
  });
});

describe("AC5 — a primitive registered later needs no change here", () => {
  it("offers an unknown primitive, in a group for its unknown category", () => {
    open({ entries: [...CONTAINER_PRIMITIVES, SYNTHETIC] });
    const list = screen.getByRole("list", { name: "Laboratory" });
    expect(list.querySelector("[data-primitive-id='synthetic_probe']")).not.toBeNull();
    expect(visibleOptionIds()).toHaveLength(CONTAINER_PRIMITIVES.length + 1);
  });

  it("names no primitive id or category of its own", () => {
    const source = fs.readFileSync(
      path.resolve(__dirname, "../PrimitivePickerModal.tsx"),
      "utf8",
    );
    for (const entry of CONTAINER_PRIMITIVES) {
      expect({ id: entry.id, named: new RegExp(`["'\`]${entry.id}["'\`]`).test(source) }).toEqual({
        id: entry.id,
        named: false,
      });
      expect({
        category: entry.category,
        named: new RegExp(`["'\`]${entry.category}["'\`]`).test(source),
      }).toEqual({ category: entry.category, named: false });
    }
  });
});

describe("AC4 — search", () => {
  function search(text: string) {
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: text } });
  }

  it("narrows to the primitives whose name matches", () => {
    open();
    const named = CONTAINER_PRIMITIVES.filter((entry) =>
      entry.displayName.toLowerCase().includes("ticket"),
    );
    expect(named.length).toBeGreaterThan(1);

    search("ticket");
    const shown = visibleOptionIds();
    for (const entry of named) expect(shown).toContain(entry.id);
    expect(shown.length).toBeLessThan(CONTAINER_PRIMITIVES.length);
  });

  it("matches a category as well as a name, and hides the emptied groups", () => {
    open();
    const boards = CONTAINER_PRIMITIVES.filter((entry) => entry.category === "Boards");
    expect(boards.length).toBeGreaterThan(0);

    search("boards");
    expect(visibleOptionIds().sort()).toEqual(boards.map((entry) => entry.id).sort());
    // A group with nothing left in it must not stay behind as an empty heading.
    expect(screen.getAllByRole("list")).toHaveLength(1);
  });

  it("narrows on every term rather than widening on any", () => {
    open();
    search("ticket");
    const oneTerm = visibleOptionIds().length;
    search("ticket workflow");
    const twoTerms = visibleOptionIds();
    expect(twoTerms.length).toBeLessThan(oneTerm);
    expect(twoTerms).toContain("chat_ticket_workflow");
  });

  it("says so when nothing matches, rather than showing an empty dialog", () => {
    open();
    search("zzzzz");
    expect(visibleOptionIds()).toEqual([]);
    expect(screen.queryAllByRole("list")).toEqual([]);
    expect(within(screen.getByRole("dialog")).getByText(/Nothing matches/)).toBeInTheDocument();
  });

  it("offers everything again when the query is cleared", () => {
    open();
    search("zzzzz");
    search("");
    expect(visibleOptionIds()).toHaveLength(CONTAINER_PRIMITIVES.length);
  });

  it("gives its field a label in the accessibility tree, not a placeholder alone", () => {
    // 556's rule for every control on these surfaces, and 556's own
    // accessibility test failed to catch a version of this by asserting
    // against a button it had written itself. This one queries the real field.
    open();
    const field = screen.getByLabelText("Search");
    expect(field).toHaveClass("input");
  });
});

describe("two pickers open at once do not collide", () => {
  it("gives each dialog's search field its own id", () => {
    // A grid of panes is a document with several headers in it, and a constant
    // `id="picker-search"` makes the second dialog's label drive the first
    // dialog's input.
    render(
      <>
        <PrimitivePickerModal legend="One" onPick={noop} onClose={noop} />
        <PrimitivePickerModal legend="Two" onPick={noop} onClose={noop} />
      </>,
    );
    const ids = Array.from(document.querySelectorAll("[id]")).map((el) => el.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
  });
});
