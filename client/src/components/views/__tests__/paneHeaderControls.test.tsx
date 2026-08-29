/**
 * 556 AC3 — "every pane header control carries an accessible name, and the icons
 * are not the only thing carrying meaning."
 *
 * Two separate claims, and they need two separate assertions:
 *
 *   1. *An accessible name exists.* `getByRole("button", {name})` resolves the
 *      real accessible name, so a control whose only name was its glyph fails —
 *      the glyphs are `aria-hidden`, which leaves such a button nameless.
 *   2. *The icon is not the only carrier.* An `aria-label` satisfies (1) while
 *      leaving a sighted pointer user with nothing but `⇄`. So every icon-only
 *      control must also carry a `title`, which is the carrier that reaches
 *      that user. The pairing is the point: 434's rule is that a name is never
 *      `title`-*only*, and this asserts `aria-label` **and** `title`, never
 *      `title` alone.
 *
 * What these tests deliberately do **not** claim: anything about how the header
 * *looks*. jsdom has no layout engine and reports every element at 0px, so
 * spacing, alignment and visual weight are unmeasurable here. The design pass
 * that produced this header is verified in a browser, not in this file.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";

import { installGridHarness, leafLayout, renderGrid } from "../../../test/gridHarness";
import { PaneHeader } from "../PaneHeader";

// The harness drives these two seams; they have to be mocked in this file's own
// module registry, not the harness's.
jest.mock("../../../lib/viewsApi", () => ({
  ...jest.requireActual("../../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

/** A real terminal opens a websocket and an xterm instance; the header does not care. */
jest.mock("../../TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: () => <div data-testid="fake-terminal" />,
}));

installGridHarness();

const TERMINAL_CONTAINER = { kind: "terminal", settings: { primitive_id: "terminal" } };

function renderHeader(container: unknown = TERMINAL_CONTAINER) {
  return render(
    <PaneHeader
      container={container}
      actionAttribute="data-grid-action"
      containerId="c1"
      onPickPrimitive={() => {}}
      buttons={
        <button type="button" className="btn-secondary" aria-label="Close this pane" title="Close this pane">
          <span aria-hidden="true">✕</span>
        </button>
      }
    />,
  );
}

describe("556 AC3 — pane header controls name themselves", () => {
  it("gives every header button an accessible name that is not its glyph", () => {
    const { container } = renderHeader();
    const buttons = Array.from(container.querySelectorAll("button"));
    expect(buttons.length).toBeGreaterThan(0);

    for (const button of buttons) {
      // The glyph is aria-hidden, so any name found here came from a label.
      const named = screen.getByRole("button", {
        name: button.getAttribute("aria-label") ?? "",
      });
      expect(named).toBe(button);
    }
  });

  it("backs each icon-only control with a title as well as an aria-label", async () => {
    // The specific failure this catches: a control that is perfectly legible to
    // a screen reader and completely opaque to an eye, because the only visible
    // thing in it is a Unicode glyph with no shared metrics.
    //
    // Rendered through the *real* grid rather than through `renderHeader`'s
    // fixture, and that is the whole point of the test. `PaneHeader` draws only
    // the picker button itself; the rest of the row — split, split, close — is
    // passed in by the arrangement. A version of this test that inspected a
    // button the test itself had written would assert nothing about the buttons
    // that actually ship, and would pass while every real control in the header
    // carried a glyph and no title. It did, briefly, which is why this renders
    // the surface instead.
    const { container } = renderGrid(leafLayout());
    await screen.findByTestId("view-host");

    const header = container.querySelector(".pane-header");
    expect(header).not.toBeNull();
    const buttons = Array.from((header as HTMLElement).querySelectorAll("button"));
    // The grid header ships four: change contents, split ×2, close.
    expect(buttons).toHaveLength(4);

    for (const button of buttons) {
      const label = button.getAttribute("aria-label");
      const title = button.getAttribute("title");
      // Both present, and agreeing — a title that says something else is a
      // second name, not a second carrier of the same one.
      expect({ label, title }).toEqual({ label: expect.any(String), title: label });
    }
  });

  it("keeps the picker control's name in the accessibility tree, never in a title alone", () => {
    // 434's headline fix, restated for the control this ticket restyled.
    renderHeader();
    const picker = screen.getByRole("button", { name: "Change contents" });
    expect(picker).toHaveAttribute("aria-label", "Change contents");
  });
});

describe("556 — the header's zone order, which 554 builds against", () => {
  it("orders the row as name, then contents controls, then arrangement controls", () => {
    // This is the decision handed to the settings-editor ticket: a settings
    // control joins the *contents* zone, before the picker and before the
    // separator. Asserting the order here is what stops the two tickets from
    // landing and disagreeing — 554 adding its button to the arrangement zone
    // fails this test rather than merging quietly.
    const { container } = renderHeader();
    const row = container.querySelector(".pane-header");
    expect(row).not.toBeNull();

    const children = Array.from((row as HTMLElement).children);
    const titleIndex = children.findIndex((el) => el.classList.contains("pane-header-title"));
    const pickerIndex = children.findIndex((el) => el.getAttribute("aria-label") === "Change contents");
    const ruleIndex = children.findIndex((el) => el.classList.contains("pane-header-zone-rule"));
    const closeIndex = children.findIndex((el) => el.getAttribute("aria-label") === "Close this pane");

    expect(titleIndex).toBe(0);
    // Contents zone sits between the name and the separator; arrangement after.
    expect(pickerIndex).toBeGreaterThan(titleIndex);
    expect(ruleIndex).toBeGreaterThan(pickerIndex);
    expect(closeIndex).toBeGreaterThan(ruleIndex);
  });

  it("marks an unconfigured pane in its title rather than leaving it unnamed", () => {
    const { container } = renderHeader({ kind: "panel", settings: {} });
    const title = container.querySelector(".pane-header-title");
    expect(title).toHaveTextContent("Empty pane");
    expect(title).toHaveClass("is-empty");
  });
});

describe("556 — the primitive picker says what it is offering", () => {
  it("labels the picker list, so its purpose is not carried by position alone", () => {
    // Before this pass the panel opened as a bare strip of buttons: what the
    // list was for was inferable only from which button had been pressed.
    const { container } = renderHeader();
    fireEvent.click(screen.getByRole("button", { name: "Change contents" }));

    const list = container.querySelector(".primitive-picker");
    expect(list).not.toBeNull();
    expect(list).toHaveAttribute("aria-label", "Change contents to");
    // Each option names its primitive in text, not only in a glyph.
    expect(within(list as HTMLElement).getByText("Terminal")).toBeInTheDocument();
  });
});
