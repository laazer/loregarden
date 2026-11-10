import { findUsabilityProblems } from "../usabilityCheck";
import * as usabilityCheck from "../usabilityCheck";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { tabbableWithin } from "../../hooks/useDialogFocusTrap";

function kinds(html: string): string[] {
  const root = document.createElement("div");
  root.innerHTML = html;
  return findUsabilityProblems(root).map((p) => p.kind);
}

function repeat(count: number, item: (index: number) => string): string {
  return Array.from({ length: count }, (_, index) => item(index)).join("");
}

const record = (i: number) =>
  `<li><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span><span class="agent">planner</span>` +
  `<span class="age">${i}d</span><span class="cost">$${i}</span></li>`;

describe("tabular-list", () => {
  it("flags list items that all repeat a name and four attributes", () => {
    expect(kinds(`<ul>${repeat(10, record)}</ul>`)).toContain("tabular-list");
  });

  it("lets a board lane hold same-shaped cards, nested lists included", () => {
    const lane = `<div data-board-lane><ul>${repeat(10, record)}<li><ul>${repeat(10, record)}</ul></li></ul></div>`;
    expect(kinds(lane)).not.toContain("tabular-list");
  });

  it("lets a tree hold same-shaped rows, and still asks a long one for a filter", () => {
    const node = (i: number) =>
      `<div class="tree-node"><div role="treeitem" tabindex="0"><span class="prio">P3</span>` +
      `<span class="title">Ticket ${i}</span><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span></div></div>`;
    expect(kinds(`<div role="tree">${repeat(10, node)}</div>`)).not.toContain("tabular-list");
    expect(kinds(`<div role="tree">${repeat(60, node)}</div>`)).toContain("unfiltered-long-list");
  });

  it("passes a name with three short tags", () => {
    const item = (i: number) =>
      `<li><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span></li>`;
    expect(kinds(`<ul>${repeat(10, item)}</ul>`)).not.toContain("tabular-list");
  });

  it("does not count a control's labels or choices as fields", () => {
    const item = (i: number) =>
      `<li><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span><button>Detach</button>` +
      `<select aria-label="Group"><option>A</option><option>B</option><option>C</option></select></li>`;
    expect(kinds(`<ul>${repeat(10, item)}</ul>`)).not.toContain("tabular-list");
  });

  it("passes a title with one detail line", () => {
    const item = (i: number) => `<li><a href="/t/${i}">Ticket ${i}</a><span>open · ${i}d</span></li>`;
    expect(kinds(`<ul>${repeat(10, item)}</ul>`)).not.toContain("tabular-list");
  });

  it("passes the same records when there are too few to compare", () => {
    expect(kinds(`<ul>${repeat(7, record)}</ul>`)).not.toContain("tabular-list");
  });

  it("passes items whose fields differ from one to the next", () => {
    const mixed = (i: number) =>
      i % 2 === 0 ? record(i) : `<li><a href="/n/${i}">Note ${i}</a><em>pinned</em><b>x</b><i>y</i><small>z</small></li>`;
    expect(kinds(`<ul>${repeat(10, mixed)}</ul>`)).not.toContain("tabular-list");
  });
});

describe("key-value-table", () => {
  const pair = (i: number) => `<tr><th scope="row">Field ${i}</th><td>value ${i}</td></tr>`;

  it("flags label/value rows with no column header", () => {
    expect(kinds(`<table><tbody>${repeat(4, pair)}</tbody></table>`)).toContain("key-value-table");
  });

  it("passes a two-column table with a header row", () => {
    const head = "<thead><tr><th>Name</th><th>Count</th></tr></thead>";
    expect(kinds(`<table>${head}<tbody>${repeat(4, pair)}</tbody></table>`)).not.toContain("key-value-table");
  });

  it("passes a table with more than two columns", () => {
    const row = (i: number) => `<tr><td>a${i}</td><td>b</td><td>c</td></tr>`;
    expect(kinds(`<table><tbody>${repeat(4, row)}</tbody></table>`)).not.toContain("key-value-table");
  });
});

describe("rendered lists (cards, not <ul>)", () => {
  // The blindness this covers: /branch-triage rendered 224 `div.branch-triage-card`
  // and the checks, reading `ul, ol, tbody`, saw the 11-item nav sidebar.
  const card = (i: number) =>
    `<div class="branch-triage-card"><span class="name">branch ${i}</span><span class="age">${i}d</span></div>`;

  it("flags a long run of cards with nothing to click", () => {
    expect(kinds(`<div>${repeat(16, card)}</div>`)).toContain("dead-end-list");
  });

  it("flags a run of cards past the long-list threshold with no way to narrow it", () => {
    expect(kinds(`<div>${repeat(51, card)}</div>`)).toContain("unfiltered-long-list");
  });

  it("passes the same run when a filter sits near it", () => {
    const html = `<section><input type="search" aria-label="Filter" /><div>${repeat(51, card)}</div></section>`;
    expect(kinds(html)).not.toContain("unfiltered-long-list");
  });

  it("counts a text field with no type attribute as the filter", () => {
    const html = `<div><input aria-label="Filter changed files" /><div>${repeat(51, card)}</div></div>`;
    expect(kinds(html)).not.toContain("unfiltered-long-list");
  });

  it("passes cards that each hold something to click", () => {
    const linked = (i: number) =>
      `<div class="branch-triage-card"><a href="/b/${i}">branch ${i}</a><span class="age">${i}d</span></div>`;
    expect(kinds(`<div>${repeat(16, linked)}</div>`)).not.toContain("dead-end-list");
  });

  it("groups a card with its selected variant, since the block class is the same", () => {
    const mixed = (i: number) =>
      i === 0
        ? `<div class="branch-triage-card is-selected"><span class="name">branch ${i}</span></div>`
        : card(i);
    expect(kinds(`<div>${repeat(16, mixed)}</div>`)).toContain("dead-end-list");
  });

  it("flags cards that repeat a name and four attributes", () => {
    const row = (i: number) =>
      `<div class="ticket-seg"><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span><span class="cost">$${i}</span></div>`;
    expect(kinds(`<div>${repeat(10, row)}</div>`)).toContain("tabular-list");
  });

  it("leaves an unclassed run alone, because that is layout and not a list", () => {
    const plain = (i: number) => `<div><span>row ${i}</span><span>${i}d</span></div>`;
    expect(kinds(`<div>${repeat(20, plain)}</div>`)).not.toContain("dead-end-list");
  });

  it("leaves a run of empty elements alone", () => {
    const blank = () => `<div class="spark-bar"></div>`;
    expect(kinds(`<div>${repeat(20, blank)}</div>`)).not.toContain("dead-end-list");
  });

  it("lets a board lane hold same-shaped cards", () => {
    const row = (i: number) =>
      `<div class="ticket-seg"><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span><span class="cost">$${i}</span></div>`;
    expect(kinds(`<div data-board-lane><div>${repeat(10, row)}</div></div>`)).not.toContain("tabular-list");
  });

  it("never asks a table to become a table", () => {
    const tr = (i: number) =>
      `<tr><td><a href="/t/${i}">Ticket ${i}</a></td><td>open</td><td>planner</td><td>${i}d</td><td>$${i}</td></tr>`;
    expect(kinds(`<table><tbody>${repeat(10, tr)}</tbody></table>`)).not.toContain("tabular-list");
  });

  it("does not read a graph's edges as a list of items with nothing to click", () => {
    // The memory map draws 50 `<line class="mm-inferred">` siblings. They repeat
    // one shape and none is clickable, but they are edges, not items.
    const edges = repeat(50, (i) => `<line class="mm-inferred" x1="${i}" y1="0" x2="0" y2="${i}">e${i}</line>`);
    expect(kinds(`<svg><g>${edges}</g></svg>`)).not.toContain("dead-end-list");
  });

  it("still flags a long card run that merely sits beside a drawing", () => {
    const html = `<div><svg><g><line class="e">x</line></g></svg><div>${repeat(16, card)}</div></div>`;
    expect(kinds(html)).toContain("dead-end-list");
  });

  it("counts a semantic list once, not again as a run of siblings", () => {
    const found = (() => {
      const root = document.createElement("div");
      root.innerHTML = `<ul>${repeat(16, (i) => `<li class="row"><span>row ${i}</span></li>`)}</ul>`;
      return findUsabilityProblems(root).filter((p) => p.kind === "dead-end-list");
    })();
    expect(found).toHaveLength(1);
  });
});

describe("nested-boxes", () => {
  // jsdom lays nothing out and reads no stylesheet. Give every element a
  // drawable size, and style the boxes inline, as a browser would compute them.
  const BOX = "border-top-left-radius: 8px; border-top: 1px solid red; border-right: 1px solid red; border-bottom: 1px solid red; border-left: 1px solid red;";
  const FILL = "border-top-left-radius: 8px; background-color: rgb(30, 40, 50);";

  function nested(html: string) {
    const root = document.createElement("div");
    root.innerHTML = html;
    document.body.appendChild(root);
    try {
      return findUsabilityProblems(root).filter((p) => p.kind === "nested-boxes");
    } finally {
      root.remove();
    }
  }

  beforeEach(() => {
    jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 400, height: 80 } as DOMRect);
  });
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("flags a box inside a box inside a box, naming the inner ones", () => {
    const html = `<section style="${BOX}"><div class="lane" style="${BOX}">${repeat(3, () => `<div class="stopped" style="${FILL}">blocked</div>`)}</div></section>`;
    const found = nested(html);
    expect(found).toHaveLength(1);
    expect(found[0].detail).toContain("3 boxes");
    expect(found[0].detail).toContain(".stopped ×3");
  });

  it("passes one card inside a panel: the region and the things in it", () => {
    expect(nested(`<section style="${BOX}">${repeat(4, () => `<div class="card" style="${BOX}">x</div>`)}</section>`)).toEqual([]);
  });

  it("passes rows split by rules inside a card", () => {
    const row = `<div class="row" style="border-top: 1px solid red;">x</div>`;
    expect(nested(`<section style="${BOX}"><div style="${BOX}">${repeat(3, () => row)}</div></section>`)).toEqual([]);
  });

  it("does not count controls or square blocks as boxes", () => {
    const control = `<button style="${BOX}">go</button><input style="${BOX}" />`;
    const square = `<div style="border-top: 1px solid red; border-right: 1px solid red; border-bottom: 1px solid red; border-left: 1px solid red;">sq</div>`;
    expect(nested(`<section style="${BOX}"><div style="${BOX}">${control}${square}</div></section>`)).toEqual([]);
  });

  it("stands aside where nothing is laid out, rather than calling everything flat", () => {
    jest.restoreAllMocks();
    const html = `<section style="${BOX}"><div style="${BOX}"><div style="${BOX}">x</div></div></section>`;
    expect(nested(html)).toEqual([]);
  });
});

/**
 * lg-milestone-that-1465 — a height-bounded `overflow: auto` region that is
 * neither tabbable nor holds anything tabbable. The scrollbar is visible and no
 * key moves it, which is what axe-core's `scrollable-region-focusable` rule and
 * WCAG 2.1.1 are about. A screenshot review passes it, because the reviewer
 * already has a mouse.
 *
 * The fixtures are styled inline for the same reason the `nested-boxes` block
 * above states: jsdom reads no stylesheet (jest.config maps `*.css` to
 * styleMock), but `getComputedStyle` reports style-attribute values faithfully
 * — probed here as `maxHeight: "9em"`, `overflow: "auto"` for the first fixture
 * and `""` for every property of an unstyled div. So an app component rendered
 * under jest gives the check nothing to measure, and standing aside there is
 * the correct silence rather than a reason to move the check to a gate.
 *
 * `KIND` is typed `string` deliberately: the test pins the exact kind name
 * while this file still typechecks before the kind exists.
 */
describe("scrollable-region-focusable", () => {
  const KIND: string = "scrollable-region-focusable";
  /** The bound and the overflow `.wm-error-reason` carries verbatim. */
  const SCROLLER = "max-height: 9em; overflow: auto;";
  const LONG = "502 Bad Gateway: upstream connect error or disconnect/reset before headers. ".repeat(20);

  function scrollable(html: string) {
    const root = document.createElement("div");
    root.innerHTML = html;
    document.body.appendChild(root);
    try {
      return findUsabilityProblems(root).filter((p) => p.kind === KIND);
    } finally {
      root.remove();
    }
  }

  it("flags the pre-fix markup, naming the element by its first class", () => {
    const found = scrollable(`<div class="wm-error-reason wm-muted" style="${SCROLLER}">${LONG}</div>`);
    expect(found).toHaveLength(1);
    expect(found[0].detail).toContain("wm-error-reason");
  });

  it("passes the fixed markup: the same region, now a focus stop", () => {
    expect(scrollable(`<div class="wm-error-reason" style="${SCROLLER}" tabindex="0">${LONG}</div>`)).toEqual([]);
  });

  it("passes a bounded scroll region whose subtree holds a focus stop", () => {
    const html = `<div class="log" style="${SCROLLER}"><p>${LONG}</p><button>Copy</button></div>`;
    expect(scrollable(html)).toEqual([]);
  });

  it("flags one whose only control cannot be reached", () => {
    // `disabled` and `tabindex="-1"` both take the element out of tab order,
    // so neither gives the region a way in.
    expect(scrollable(`<div class="log" style="${SCROLLER}"><button disabled>Copy</button>${LONG}</div>`)).toHaveLength(1);
    expect(scrollable(`<div class="log" style="${SCROLLER}"><span tabindex="-1">x</span>${LONG}</div>`)).toHaveLength(1);
  });

  /**
   * AC2 — the three false negatives the detector's own copy of the
   * tabbability test let through, plus the three the copy's `.disabled`
   * property read cannot see at all.
   *
   * Each region's only control reports `tabIndex` 0 and `.disabled` false, so
   * the predicate as landed by `8b3d3eb2c` concludes the region has a way in.
   * Chromium refuses `.focus()` on all six — measured in
   * `scripts/probe-scrollable-region.mjs`, whose `.n-pane`/`.o-pane`/`.p-pane`
   * rows record the browser's own refusal beside the finding count. A region
   * no key reaches, reported reachable, is the defect this detector exists to
   * find.
   */
  it("flags one whose only control sits in a disabled fieldset", () => {
    // `.disabled` is true on the <button> via its fieldset, but only as a
    // computed property in a browser: jsdom reports it false. The attribute is
    // on the <fieldset>, so `closest("fieldset[disabled]")` is the only term
    // that answers in both engines.
    const html = `<div class="log" style="${SCROLLER}"><fieldset disabled><button>Copy</button></fieldset>${LONG}</div>`;
    expect(scrollable(html)).toHaveLength(1);
  });

  it("flags one whose only control is [hidden]", () => {
    const html = `<div class="log" style="${SCROLLER}"><button hidden>Copy</button>${LONG}</div>`;
    expect(scrollable(html)).toHaveLength(1);
  });

  it("flags one whose only control is inside [inert]", () => {
    const html = `<div class="log" style="${SCROLLER}"><div inert><button>Copy</button></div>${LONG}</div>`;
    expect(scrollable(html)).toHaveLength(1);
  });

  it("flags one whose only control is aria-disabled", () => {
    const html = `<div class="log" style="${SCROLLER}"><button aria-disabled="true">Copy</button>${LONG}</div>`;
    expect(scrollable(html)).toHaveLength(1);
  });

  it("flags one whose only control is aria-hidden", () => {
    // Read on the element ITSELF, matching `useDialogFocusTrap`: widening this
    // to `closest("[aria-hidden='true']")` would be a new divergence in the
    // other direction, so the fixture puts the attribute on the control.
    const html = `<div class="log" style="${SCROLLER}"><button aria-hidden="true">Copy</button>${LONG}</div>`;
    expect(scrollable(html)).toHaveLength(1);
  });

  it("flags one whose only control is an <a> carrying a disabled attribute", () => {
    // The case the property read cannot reach in ANY engine: `HTMLAnchorElement`
    // has no `disabled` property, so `.disabled` is `undefined` and the copy
    // calls this anchor a way in. The attribute is what the platform honours.
    const html = `<div class="log" style="${SCROLLER}"><a href="/x" disabled>Copy</a>${LONG}</div>`;
    expect(scrollable(html)).toHaveLength(1);
  });

  it("reads a bound and an overflow on either axis", () => {
    expect(scrollable(`<div class="pane" style="height: 120px; overflow-y: scroll;">${LONG}</div>`)).toHaveLength(1);
    expect(scrollable(`<div class="pane" style="max-height: 120px; overflow-x: auto;">${LONG}</div>`)).toHaveLength(1);
  });

  it("stands aside where there is nothing to read, rather than guessing", () => {
    // No bound, a bound with no scrolling overflow, an overflow with no bound,
    // and an element with no computed style at all: none is this shape, and a
    // check that guessed here would fire on most of the tree.
    expect(scrollable(`<div class="prose">${LONG}</div>`)).toEqual([]);
    expect(scrollable(`<div class="prose" style="max-height: 9em;">${LONG}</div>`)).toEqual([]);
    expect(scrollable(`<div class="prose" style="overflow: auto;">${LONG}</div>`)).toEqual([]);
    expect(scrollable(`<div class="prose" style="max-height: none; overflow: auto;">${LONG}</div>`)).toEqual([]);
    expect(scrollable(`<div class="prose" style="height: auto; overflow: hidden;">${LONG}</div>`)).toEqual([]);
  });

  /**
   * test-break — the predicate AC4 states, in the places the three designed
   * cases above do not reach. Each holds against a check that passes all of
   * them and is still wrong:
   *
   * 1. `el.children` or `:scope > *` instead of a subtree query. The one
   *    passing fixture above puts its <button> directly inside the region, so
   *    a child-only walk is green — and the shape this is modelled on, a log
   *    pane with its Copy button inside a toolbar, is two levels down.
   * 2. `a` instead of `a[href]`, or no `disabled` filter on a field. Both make
   *    the check silent on a region whose only control cannot be tabbed to.
   * 3. Reading `tabIndex` only as an attribute, so a region that is itself an
   *    <a href> — already a stop, nothing to fix — is reported anyway.
   * 4. `overflow !== "visible"` as the scrolling test, which fires on every
   *    bounded `overflow: hidden` box in the tree. That is the failure mode
   *    AC5 predicts would turn the eight other callers red.
   * 5. One aggregate finding for the whole tree, the way `nested-boxes` above
   *    deliberately reports: a per-element kind that counts once cannot tell
   *    the operator which region to fix.
   * 6. A detail built from `class.split(" ")[0]` with no fallback, which names
   *    a classless region "undefined" or nothing at all.
   * 7. No `defaultView` guard. This is the mechanism AC5's prediction rests
   *    on — the check must stand aside where no style is readable rather than
   *    guess, and `visual-qa.mjs` evaluates this function in pages the app
   *    does not control.
   */

  it("stands aside when the focus stop is deep in the subtree, not a child", () => {
    const html = `<div class="log" style="${SCROLLER}"><div class="bar"><p><button>Copy</button></p></div>${LONG}</div>`;
    expect(scrollable(html)).toEqual([]);
  });

  it("flags a region whose only anchor has no href, or whose only field is disabled", () => {
    expect(
      scrollable(
        `<div class="log" style="${SCROLLER}"><a>Copy</a>${LONG}</div>`,
      ),
    ).toHaveLength(1);
    expect(
      scrollable(
        `<div class="log" style="${SCROLLER}"><input disabled>${LONG}</div>`,
      ),
    ).toHaveLength(1);
  });

  it("stands aside when the bounded region is itself natively tabbable", () => {
    // No `tabindex` attribute, and already a focus stop: `<a href>` is in
    // AC4's selector list and reports `tabIndex` 0. Nothing here is broken.
    expect(
      scrollable(`<a href="#log" class="log" style="${SCROLLER}">${LONG}</a>`),
    ).toEqual([]);
  });

  it("stands aside on a bound whose overflow does not scroll", () => {
    expect(
      scrollable(
        `<div class="box" style="max-height: 9em; overflow: hidden;">${LONG}</div>`,
      ),
    ).toEqual([]);
    expect(
      scrollable(
        `<div class="box" style="max-height: 9em; overflow: visible;">${LONG}</div>`,
      ),
    ).toEqual([]);
    expect(
      scrollable(
        `<div class="box" style="height: 120px; overflow-y: hidden;">${LONG}</div>`,
      ),
    ).toEqual([]);
  });

  it("reports each region, and names a classless one too", () => {
    const found = scrollable(
      `<div class="first" style="${SCROLLER}">${LONG}</div>` +
        `<div class="second" style="${SCROLLER}">${LONG}</div>` +
        `<section style="${SCROLLER}">${LONG}</section>`,
    );
    expect(found).toHaveLength(3);
    // Each by its own first class name, and the classless one by its tag, as
    // `nested-boxes` names a box with no class. A detail built from
    // `class.split(" ")[0]` with no fallback names that one nothing at all.
    expect(found[0]!.detail).toContain("first");
    expect(found[1]!.detail).toContain("second");
    expect(found[2]!.detail).toContain("section");
    for (const problem of found) {
      expect(problem.detail).not.toMatch(/undefined|null|\.\s/);
    }
  });

  it("stands aside in a document with no view, rather than throwing in one", () => {
    // `createHTMLDocument` has no `defaultView`, so there is no
    // `getComputedStyle` to call. That is the same silence an app component
    // rendered under jest produces, where no stylesheet is attached.
    const doc = document.implementation.createHTMLDocument("no view");
    expect(doc.defaultView).toBeNull();
    doc.body.innerHTML = `<div class="wm-error-reason" style="${SCROLLER}">${LONG}</div>`;

    expect(
      findUsabilityProblems(doc.body).filter((p) => p.kind === KIND),
    ).toEqual([]);
  });

  it("stands aside under a laid-out body in an engine with no checkVisibility", () => {
    // The one path the sweep-level layout probe makes live and nothing else
    // covers. `nested-boxes` already stubs this rect to 400x80 for its whole
    // describe block, so a laid-out body under jest is not hypothetical: it is
    // how that block runs today, and under it the hoisted "is there a layout
    // engine" fact is TRUE for the scrollable block too. jsdom 26.1.0 then
    // offers neither `clientHeight`/`scrollHeight` (both 0, so nothing clips)
    // nor `Element.prototype.checkVisibility`, so the rendered-ness predicate
    // has to be feature-detected rather than called. Nothing may fire, and
    // nothing may throw.
    const rect = jest
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 400, height: 80 } as DOMRect);
    try {
      expect(document.body.getBoundingClientRect().width).toBeGreaterThan(0);
      expect(Element.prototype.checkVisibility).toBeUndefined();
      const sweep = () =>
        scrollable(`<div class="wm-error-reason wm-muted" style="${SCROLLER}">${LONG}</div>`);
      expect(sweep).not.toThrow();
      expect(sweep()).toEqual([]);
    } finally {
      rect.mockRestore();
    }
  });

  /**
   * test-break — the two halves of AC2's predicate that neither oracle above
   * reaches. The case added at test-design stubs the body rect but leaves
   * jsdom's geometry at 0, so nothing clips and `[]` is the answer for three
   * different reasons at once: the region does not clip, the rendered-ness
   * predicate is absent, and a predicate that suppressed every region it
   * could not ask about would also print `[]`. Three implementations the
   * criteria forbid are green against it:
   *
   * 1. `typeof el.checkVisibility === "function" ? el.checkVisibility({…}) :
   *    false` — the feature detection inverted, so "cannot tell" suppresses.
   *    AC2 requires the absent case to yield TRUE: a browser without the API
   *    must still report the region, because the detector's whole job is to
   *    report. This is the mutant with no witness anywhere: the probe runs in
   *    a Chromium that HAS `checkVisibility`, and jest's only laid-out case
   *    does not clip.
   * 2. A laid-out branch that `continue`s unconditionally — the detector
   *    deleted for every engine that lays out, which is every engine an
   *    operator uses. `npm run visual-qa` would print zero findings forever
   *    and read as a clean tree.
   * 3. A laid-out branch that reports before testing tabbability, so AC4's
   *    fixed markup (`tabindex="0"`, the thing lg-milestone-that-1465
   *    shipped) is reported again as soon as an engine lays it out. Every
   *    existing case here takes the no-layout fallback, so the laid-out
   *    branch's focus-stop test is pinned only by the probe, and the probe
   *    needs playwright.
   *
   * The stub is the `nested-boxes` block's own 400x80 body rect plus the
   * geometry a real closed-or-open `.wm-log` pane reports in Chromium
   * (clientHeight 60 / scrollHeight 1440, measured by
   * `scripts/probe-scrollable-region.mjs`), which is the one combination
   * jsdom cannot produce on its own.
   */
  function laidOutAndClipping<T>(body: () => T): T {
    const rect = jest
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 400, height: 80 } as DOMRect);
    const clientHeight = jest.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(60);
    const scrollHeight = jest.spyOn(Element.prototype, "scrollHeight", "get").mockReturnValue(1440);
    try {
      return body();
    } finally {
      scrollHeight.mockRestore();
      clientHeight.mockRestore();
      rect.mockRestore();
    }
  }

  it("still reports a clipping region in a laid-out engine that cannot be asked whether it renders", () => {
    laidOutAndClipping(() => {
      expect(document.body.getBoundingClientRect().width).toBeGreaterThan(0);
      expect(Element.prototype.checkVisibility).toBeUndefined();
      const found = scrollable(
        `<div class="wm-error-reason wm-muted" style="${SCROLLER}">${LONG}</div>`,
      );
      expect(found).toHaveLength(1);
      expect(found[0]!.detail).toContain("wm-error-reason");
    });
  });

  it("still passes the fixed markup once the engine lays it out and it clips", () => {
    laidOutAndClipping(() => {
      expect(
        scrollable(`<div class="wm-error-reason" style="${SCROLLER}" tabindex="0">${LONG}</div>`),
      ).toEqual([]);
      expect(
        scrollable(
          `<div class="log" style="${SCROLLER}"><p>${LONG}</p><button>Copy</button></div>`,
        ),
      ).toEqual([]);
    });
  });

  /**
   * lg-milestone-that-2230 — the laid-out arm measures ONE axis. `overflowX:
   * auto` admits an element to the check and then only `scrollHeight >
   * clientHeight` is asked, so a region that clips horizontally and not
   * vertically is dropped as "does not clip" though no key reaches it. Measured
   * in Chromium (`scripts/probe-scrollable-region.mjs`): a
   * `width:200px; height:40px; overflow-x:auto; overflow-y:hidden` pane holding
   * ~3,000 characters of nowrap text reports clientWidth 200 / scrollWidth
   * 20023 with clientHeight === scrollHeight === 40, and the detector is
   * silent.
   *
   * `laidOut` is a document-level fact, so in a browser EVERY element takes
   * this arm and none takes the declared-bound fallback below it. The cases
   * here are therefore the only jest coverage of the branch that runs on every
   * real surface, and they need both axes' geometry stubbed rather than only
   * the vertical pair `laidOutAndClipping` supplies.
   */
  function laidOutMeasuring<T>(
    geometry: {
      clientHeight: number;
      scrollHeight: number;
      clientWidth: number;
      scrollWidth: number;
    },
    body: () => T,
  ): T {
    const spies: { mockRestore: () => void }[] = [
      jest
        .spyOn(HTMLElement.prototype, "getBoundingClientRect")
        .mockReturnValue({ width: 400, height: 80 } as DOMRect),
      jest.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(geometry.clientHeight),
      jest.spyOn(Element.prototype, "scrollHeight", "get").mockReturnValue(geometry.scrollHeight),
      jest.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(geometry.clientWidth),
      jest.spyOn(Element.prototype, "scrollWidth", "get").mockReturnValue(geometry.scrollWidth),
    ];
    try {
      return body();
    } finally {
      for (const spy of spies.reverse()) spy.mockRestore();
    }
  }

  /** 24 lines of 60px in a 60px box: clips vertically, nothing to scroll sideways. */
  const CLIPS_Y = { clientHeight: 60, scrollHeight: 1440, clientWidth: 200, scrollWidth: 200 };
  /** The Chromium numbers for the nowrap fixture: clips horizontally and NOT vertically. */
  const CLIPS_X = { clientHeight: 40, scrollHeight: 40, clientWidth: 200, scrollWidth: 20023 };
  const CLIPS_BOTH = { clientHeight: 60, scrollHeight: 1440, clientWidth: 200, scrollWidth: 20023 };
  const CLIPS_NEITHER = { clientHeight: 60, scrollHeight: 60, clientWidth: 200, scrollWidth: 200 };

  /**
   * Declarations chosen for what jsdom 26.1.0 *computes* from them, which is
   * what the detector reads. Pinned by the premise case below, because every
   * axis assertion in this block is about one of these three shapes and a
   * jsdom that started expanding the shorthand would quietly move them all to
   * a different branch.
   */
  /** computes to `overflow: "auto"`, overflowX `""`, overflowY `""`. */
  const BOTH_AXES = "max-height: 9em; overflow: auto;";
  /** computes to `overflow: ""`, overflowX `"auto"`, overflowY `"hidden"`. */
  const X_ONLY = "max-height: 9em; overflow-x: auto; overflow-y: hidden;";
  /** computes to `overflow: ""`, overflowX `"hidden"`, overflowY `"auto"`. */
  const Y_ONLY = "max-height: 9em; overflow-x: hidden; overflow-y: auto;";

  it("computes the axis properties this block's cases are built on", () => {
    // Not a tautology and not a test of jsdom for its own sake: the fallback
    // in AC2 exists BECAUSE jsdom leaves both axis properties empty for a
    // shorthand, and three of the cases below only reach the branch they are
    // written for while that is true. If a later jsdom expands the shorthand,
    // this is the case that says so, instead of the axis assertions going
    // green for a reason that is no longer the one under test.
    const read = (style: string) => {
      const el = document.createElement("div");
      el.setAttribute("style", style);
      document.body.appendChild(el);
      try {
        const computed = window.getComputedStyle(el);
        return { overflow: computed.overflow, x: computed.overflowX, y: computed.overflowY };
      } finally {
        el.remove();
      }
    };
    expect(read(BOTH_AXES)).toEqual({ overflow: "auto", x: "", y: "" });
    expect(read(X_ONLY)).toEqual({ overflow: "", x: "auto", y: "hidden" });
    expect(read(Y_ONLY)).toEqual({ overflow: "", x: "hidden", y: "auto" });
  });

  it("flags a region that clips only horizontally", () => {
    // The defect, at its narrowest. Both axes scroll, nothing is clipped
    // vertically, and 20023px of content sits behind a 200px box that no key
    // reaches. axe-core's `scrollable-region-focusable` covers both axes.
    laidOutMeasuring(CLIPS_X, () => {
      const found = scrollable(`<div class="hscroll" style="${BOTH_AXES}">${LONG}</div>`);
      expect(found).toHaveLength(1);
      expect(found[0]!.detail).toContain("hscroll");
    });
  });

  it("flags a region that clips only vertically, and one that clips on both axes", () => {
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="vscroll" style="${BOTH_AXES}">${LONG}</div>`)).toHaveLength(1);
    });
    laidOutMeasuring(CLIPS_BOTH, () => {
      expect(scrollable(`<div class="bscroll" style="${BOTH_AXES}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  it("stands aside where a scrolling region clips on neither axis", () => {
    // The silent control for the two above: a check that fired on every
    // admitted element would pass them and fail here.
    laidOutMeasuring(CLIPS_NEITHER, () => {
      expect(scrollable(`<div class="fits" style="${BOTH_AXES}">${LONG}</div>`)).toEqual([]);
    });
  });

  it("does not read a large scrollHeight as a vertical clip when overflow-y cannot scroll", () => {
    // `overflow-x: auto` admits the element; the vertical comparison must then
    // be gated on the vertical axis actually scrolling. `hidden`, `visible`
    // and `clip` are the three spellings of "no scrollbar here".
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="x" style="${X_ONLY}">${LONG}</div>`)).toEqual([]);
      expect(
        scrollable(`<div class="x" style="max-height: 9em; overflow-x: auto; overflow-y: visible;">${LONG}</div>`),
      ).toEqual([]);
      expect(
        scrollable(`<div class="x" style="max-height: 9em; overflow-x: auto; overflow-y: clip;">${LONG}</div>`),
      ).toEqual([]);
    });
  });

  it("does not read a large scrollWidth as a horizontal clip when overflow-x cannot scroll", () => {
    laidOutMeasuring(CLIPS_X, () => {
      expect(scrollable(`<div class="y" style="${Y_ONLY}">${LONG}</div>`)).toEqual([]);
      expect(
        scrollable(`<div class="y" style="max-height: 9em; overflow-x: clip; overflow-y: auto;">${LONG}</div>`),
      ).toEqual([]);
    });
  });

  it("flags each axis on the axis it actually clips on", () => {
    // The pair that makes the gate per-axis rather than a single either/or:
    // the same two fixtures swap verdicts when the geometry swaps axes.
    laidOutMeasuring(CLIPS_X, () => {
      expect(scrollable(`<div class="x" style="${X_ONLY}">${LONG}</div>`)).toHaveLength(1);
    });
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="y" style="${Y_ONLY}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  it("passes a horizontally clipping region that can be reached by keyboard", () => {
    // AC1 keeps the focus-stop tests after the clip test and in their order,
    // so the fix lg-milestone-that-1465 shipped still answers the horizontal
    // case: a `tabindex="0"` on the region, or a control inside it.
    laidOutMeasuring(CLIPS_X, () => {
      expect(
        scrollable(`<div class="hscroll" style="${BOTH_AXES}" tabindex="0">${LONG}</div>`),
      ).toEqual([]);
      expect(
        scrollable(`<div class="hscroll" style="${BOTH_AXES}"><button>Copy</button>${LONG}</div>`),
      ).toEqual([]);
      expect(
        scrollable(`<div class="hscroll" style="${BOTH_AXES}"><button disabled>Copy</button>${LONG}</div>`),
      ).toHaveLength(1);
    });
  });

  /**
   * AC2 — the axis is decided per-axis, and the `overflow` shorthand is never
   * tested as one value. These four declarations are the ones where those two
   * rules disagree, and each computes in jsdom 26.1.0 to a shape no amount of
   * reading the shorthand whole gets right:
   *
   * | declaration                              | overflow      | X     | Y     |
   * |------------------------------------------|---------------|-------|-------|
   * | `overflow: auto`                         | `auto`        | `""`  | `""`  |
   * | `overflow: auto; overflow-y: hidden`     | `auto`        | `""`  | `hidden` |
   * | `overflow: hidden; overflow-x: auto`     | `hidden`      | `auto`| `""`  |
   * | `overflow: auto hidden; overflow-y: scroll` | `auto hidden` | `""` | `scroll` |
   * | `overflow: hidden auto; overflow-x: auto`   | `hidden auto` | `auto` | `""` |
   *
   * Chromium expands every one of them, so these rows are jsdom's and the
   * fallback is inert in a browser — which is exactly why it has no witness
   * anywhere else and why dropping it turns the two `laidOutAndClipping` cases
   * above red rather than these.
   */
  it("takes the per-axis property over the shorthand when both are present", () => {
    // `overflow: auto; overflow-y: hidden` computes overflow `"auto"` with
    // overflowY `"hidden"`. Reading the shorthand as one value would call the
    // vertical axis scrollable and fire on the first assertion.
    const SHORTHAND_THEN_Y = "max-height: 9em; overflow: auto; overflow-y: hidden;";
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="pane" style="${SHORTHAND_THEN_Y}">${LONG}</div>`)).toEqual([]);
    });
    laidOutMeasuring(CLIPS_X, () => {
      // ...and the empty overflowX still resolves, from the shorthand's word.
      expect(scrollable(`<div class="pane" style="${SHORTHAND_THEN_Y}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  it("applies a one-word shorthand to the axis whose property is empty", () => {
    // `overflow: hidden; overflow-x: auto` computes overflow `"hidden"` with
    // overflowY `""`. One word applies to both axes, so the vertical axis is
    // hidden and a 1440px scrollHeight is not a clip. An implementation that
    // treated an empty axis property as "scrolls" fires on the first
    // assertion; one with no fallback at all is silent on the second.
    const SHORTHAND_THEN_X = "max-height: 9em; overflow: hidden; overflow-x: auto;";
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="pane" style="${SHORTHAND_THEN_X}">${LONG}</div>`)).toEqual([]);
    });
    laidOutMeasuring(CLIPS_X, () => {
      expect(scrollable(`<div class="pane" style="${SHORTHAND_THEN_X}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  it("splits a two-word shorthand into [x, y] rather than testing it whole", () => {
    // The row that makes the word split necessary instead of merely tidy.
    // jsdom keeps `overflow: auto hidden` as two words with both axis
    // properties empty, so `SCROLLS.has(style.overflow)` reads `"auto hidden"`
    // as NEITHER axis scrolling — wrong, in the one engine the fallback exists
    // for. The first word is the horizontal axis and the second the vertical.
    const X_WORD = "max-height: 9em; overflow: auto hidden; overflow-y: scroll;";
    const Y_WORD = "max-height: 9em; overflow: hidden auto; overflow-x: auto;";
    laidOutMeasuring(CLIPS_X, () => {
      // overflow `"auto hidden"`, overflowX `""` → the first word scrolls.
      expect(scrollable(`<div class="pane" style="${X_WORD}">${LONG}</div>`)).toHaveLength(1);
      // overflow `"hidden auto"`, overflowX `"auto"` → the property, so it fires too;
      // reading the shorthand whole would make this one silent.
      expect(scrollable(`<div class="pane" style="${Y_WORD}">${LONG}</div>`)).toHaveLength(1);
    });
    laidOutMeasuring(CLIPS_Y, () => {
      // overflow `"hidden auto"`, overflowY `""` → the SECOND word scrolls.
      expect(scrollable(`<div class="pane" style="${Y_WORD}">${LONG}</div>`)).toHaveLength(1);
      // overflow `"auto hidden"`, overflowY `"scroll"` → the property scrolls.
      expect(scrollable(`<div class="pane" style="${X_WORD}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  /**
   * test-break — three mutants the cases above leave alive. Each is an
   * implementation of AC1/AC2 that reads as correct, passes every assertion
   * written at test-design, and is wrong in markup this tree can actually
   * produce. The declarations are measured against jsdom 26.1.0 the same way
   * the premise case above measures its three.
   */
  it("reads `scroll` as a scrolling word of the shorthand, not only `auto`", () => {
    // AC2's fallback resolves the axis from the shorthand's WORD, and the
    // vocabulary of a scrolling value is `SCROLLS` — `auto` AND `scroll`. A
    // fallback that tests the word against `"auto"` alone (rather than the
    // same set the per-axis property is tested against) passes every case
    // above, because all of them spell the fallback axis `auto`.
    //
    // Measured in jsdom 26.1.0: `overflow: scroll hidden; overflow-y: auto`
    // computes overflow `"scroll hidden"`, overflowX `""`, overflowY
    // `"auto"` — admitted on the Y property, with the X axis resolvable only
    // from the first word.
    const X_SCROLL_WORD = "max-height: 9em; overflow: scroll hidden; overflow-y: auto;";
    const Y_SCROLL_WORD = "max-height: 9em; overflow: hidden scroll; overflow-x: auto;";
    laidOutMeasuring(CLIPS_X, () => {
      expect(scrollable(`<div class="pane" style="${X_SCROLL_WORD}">${LONG}</div>`)).toHaveLength(1);
    });
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="pane" style="${Y_SCROLL_WORD}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  it("splits the shorthand on runs of whitespace, not on a single space", () => {
    // AC2 prescribes `style.overflow.trim().split(/\s+/)`, and the regex is
    // load-bearing rather than tidy: measured in jsdom 26.1.0, the computed
    // shorthand keeps its author's internal whitespace verbatim, so
    // `overflow: hidden  auto` computes to the two-space string `"hidden
    // auto"`. A `split(" ")` yields `["hidden", "", "auto"]`, whose second
    // element is the empty string, and the vertical axis then resolves to "no
    // scrollbar" for a pane that has one. Two spaces in a stylesheet is
    // ordinary formatting, not a corrupted input.
    const read = (style: string) => {
      const el = document.createElement("div");
      el.setAttribute("style", style);
      document.body.appendChild(el);
      try {
        return window.getComputedStyle(el).overflow;
      } finally {
        el.remove();
      }
    };
    const DOUBLE_SPACED = "max-height: 9em; overflow: hidden  auto; overflow-x: auto;";
    expect(read(DOUBLE_SPACED)).toBe("hidden  auto");
    laidOutMeasuring(CLIPS_Y, () => {
      expect(scrollable(`<div class="pane" style="${DOUBLE_SPACED}">${LONG}</div>`)).toHaveLength(1);
    });
  });

  /**
   * jsdom 26.1.0 implements no `checkVisibility`, so under jest the
   * rendered-ness predicate always answers `true` and the whole of it — the
   * call, the `visibilityProperty` option, and its position after the clip
   * test — has no witness in the only suite CI runs. The probe covers it in
   * Chromium, and the probe needs playwright and is run by hand.
   *
   * This stub is the browser's documented behaviour rather than a recording of
   * calls: `checkVisibility()` reports a `visibility: hidden` element as
   * visible, and only `{ visibilityProperty: true }` reports it hidden. So a
   * detector that drops the predicate, and one that calls it without the
   * option, are both wrong against it for the reason they are wrong in a
   * browser.
   */
  function withCheckVisibility<T>(body: () => T): T {
    const impl = function (this: Element, options?: { visibilityProperty?: boolean }): boolean {
      if (options?.visibilityProperty !== true) return true;
      return window.getComputedStyle(this).visibility !== "hidden";
    };
    Object.defineProperty(Element.prototype, "checkVisibility", {
      value: impl,
      configurable: true,
      writable: true,
    });
    try {
      return body();
    } finally {
      delete (Element.prototype as { checkVisibility?: unknown }).checkVisibility;
    }
  }

  it("asks the engine whether a horizontally clipping region renders at all", () => {
    // AC1 keeps `rendered(el)` in its position and order, after the clip test,
    // on the horizontal path as much as the vertical one. A region 20023px
    // wide behind a 200px box is not a finding when the browser does not draw
    // it: there is no scrollbar for a key to miss. Both halves are asserted,
    // because a predicate that suppressed everything would also print `[]`.
    withCheckVisibility(() => {
      expect(typeof Element.prototype.checkVisibility).toBe("function");
      laidOutMeasuring(CLIPS_X, () => {
        expect(
          scrollable(`<div class="hscroll" style="${BOTH_AXES} visibility: hidden;">${LONG}</div>`),
        ).toEqual([]);
        expect(scrollable(`<div class="hscroll" style="${BOTH_AXES}">${LONG}</div>`)).toHaveLength(1);
      });
      laidOutMeasuring(CLIPS_Y, () => {
        expect(
          scrollable(`<div class="vscroll" style="${BOTH_AXES} visibility: hidden;">${LONG}</div>`),
        ).toEqual([]);
        expect(scrollable(`<div class="vscroll" style="${BOTH_AXES}">${LONG}</div>`)).toHaveLength(1);
      });
    });
    expect(Element.prototype.checkVisibility).toBeUndefined();
  });

  it("leaves the no-layout arm axis-agnostic", () => {
    // AC1 touches only the laid-out arm. With no layout engine every geometry
    // is 0, there is nothing to compare on either axis, and the declared bound
    // is all there is — which is why `reads a bound and an overflow on either
    // axis` above stays green without an axis gate reaching it.
    expect(document.body.getBoundingClientRect().width).toBe(0);
    expect(scrollable(`<div class="pane" style="${X_ONLY}">${LONG}</div>`)).toHaveLength(1);
    expect(scrollable(`<div class="pane" style="${Y_ONLY}">${LONG}</div>`)).toHaveLength(1);
  });

  /**
   * test-break — every AC2 case above asserts `toHaveLength(1)`, so the
   * designed table is one-sided: an `isTabbable` that is too STRICT satisfies
   * all six, and its one control (a plain `<button>`) rules out only the
   * constant `false`. Each case below is a region a key DOES reach, three of
   * them measured reachable in Chromium (`.u-pane`, `.t-pane`, `.s-pane`).
   */

  it("stands aside where the only control sits under a non-fieldset [disabled] ancestor", () => {
    // `disabled` means nothing on a `<div>`; Chromium focuses the button.
    const html = `<div class="log" style="${SCROLLER}"><div disabled><button>Copy</button></div>${LONG}</div>`;
    expect(scrollable(html)).toEqual([]);
  });

  it("stands aside where the only control sits under an aria-hidden ancestor", () => {
    // Term 5 is element-only, and the platform hands this a tab stop.
    const html = `<div class="log" style="${SCROLLER}"><div aria-hidden="true"><button>Copy</button></div>${LONG}</div>`;
    expect(scrollable(html)).toEqual([]);
  });

  it("reads the aria attributes by value, so `false` is not `true`", () => {
    expect(
      scrollable(`<div class="log" style="${SCROLLER}"><button aria-disabled="false">Copy</button>${LONG}</div>`),
    ).toEqual([]);
    expect(
      scrollable(`<div class="log" style="${SCROLLER}"><button aria-hidden="false">Copy</button>${LONG}</div>`),
    ).toEqual([]);
    expect(
      scrollable(`<div class="log" style="${SCROLLER}"><div aria-hidden="false"><button>Copy</button></div>${LONG}</div>`),
    ).toEqual([]);
  });

  it("asks whether ANY control is reachable, not whether every one is", () => {
    const toolbar =
      `<div class="log" style="${SCROLLER}">` +
      `<div class="bar"><button disabled>Stop</button><button>Copy</button></div>${LONG}</div>`;
    expect(scrollable(toolbar)).toEqual([]);
    // Reversed, so the answer cannot come from the order of the two controls.
    const reversed =
      `<div class="log" style="${SCROLLER}">` +
      `<div class="bar"><button>Copy</button><button disabled>Stop</button></div>${LONG}</div>`;
    expect(scrollable(reversed)).toEqual([]);
  });

  it("reports a toolbar whose every control is refused once, not once per control", () => {
    // RED before the fix, for the `hidden` control: two refused controls are
    // still one region an operator has to fix.
    const dead =
      `<div class="log" style="${SCROLLER}">` +
      `<div class="bar"><button disabled>Stop</button><button hidden>Copy</button></div>${LONG}</div>`;
    expect(scrollable(dead)).toHaveLength(1);
  });
});

/**
 * AC3 — the drift guard, because the duplication is permanent.
 *
 * `src/lib/usabilityCheck.ts` may not import anything:
 * `scripts/visual-qa.mjs:33` transpiles that one file, strips its exports and
 * evaluates the whole module in a page where nothing else from the app exists,
 * and `scripts/probe-scrollable-region.mjs:349-353` does the same. So the
 * tabbability predicate there is REQUIRED to be a copy of
 * `useDialogFocusTrap`'s, and this describe block is what makes it a copy that
 * is CHECKED rather than one that is hoped for. The test file carries none of
 * that constraint: it imports both sides and compares them directly.
 *
 * `useDialogFocusTrap.ts` is the reference definition. Its own comments record
 * two deliberate refinements to the selector list — `[contenteditable]` left
 * plain, `[tabindex]` matched broadly and narrowed by the resolved `tabIndex`
 * — and the table below carries a row for each, so the next such refinement
 * lands red here instead of on one copy only.
 *
 * `tabbableWithin` scans `root.querySelectorAll`, so it never returns `root`
 * itself: the comparison is `isTabbable(el)` against
 * `tabbableWithin(wrapper).includes(el)` with `el` a DESCENDANT of `wrapper`.
 * A comparison against `tabbableWithin(el)` would be vacuously false on every
 * row and the table would assert nothing.
 *
 * `isTabbable` is read off the module namespace rather than named-imported for
 * the same reason `KIND` above is typed `string`: the export does not exist
 * until the implement stage, and this file has to typecheck while it is RED.
 * The first case below is what turns its absence into a failure rather than a
 * silence.
 */
describe("isTabbable agrees with tabbableWithin", () => {
  const isTabbable = (usabilityCheck as { isTabbable?: (el: Element) => boolean }).isTabbable;

  /**
   * One row per shape the two definitions could disagree on. `target` names
   * the element under test, always a descendant of the wrapper.
   *
   * There is deliberately NO `input[type="radio"]` carrying a `name` here:
   * `tabbableWithin` applies `isSkippedRadio` and `isTabbable` deliberately
   * does not (a radio group is never a bounded region's only way in, and this
   * check measures reachability rather than tab order), so a named radio group
   * is the one shape where disagreement is CORRECT and a row for it would
   * assert a falsehood.
   */
  const ROWS: ReadonlyArray<{ name: string; html: string; target: string }> = [
    { name: "an enabled <button>", html: `<button id="t">Copy</button>`, target: "#t" },
    { name: "<button disabled>", html: `<button id="t" disabled>Copy</button>`, target: "#t" },
    {
      name: '<button aria-disabled="true">',
      html: `<button id="t" aria-disabled="true">Copy</button>`,
      target: "#t",
    },
    {
      name: "a <button> inside <fieldset disabled>",
      html: `<fieldset disabled><button id="t">Copy</button></fieldset>`,
      target: "#t",
    },
    { name: "<button hidden>", html: `<button id="t" hidden>Copy</button>`, target: "#t" },
    {
      name: "a <button> inside [inert]",
      html: `<div inert><button id="t">Copy</button></div>`,
      target: "#t",
    },
    {
      name: '<button aria-hidden="true">',
      html: `<button id="t" aria-hidden="true">Copy</button>`,
      target: "#t",
    },
    {
      name: '<button tabindex="-1">',
      html: `<button id="t" tabindex="-1">Copy</button>`,
      target: "#t",
    },
    { name: '<a href="/x">', html: `<a id="t" href="/x">Copy</a>`, target: "#t" },
    { name: "<a> with no href", html: `<a id="t">Copy</a>`, target: "#t" },
    {
      name: '<a href="/x" disabled>',
      html: `<a id="t" href="/x" disabled>Copy</a>`,
      target: "#t",
    },
    { name: "<div contenteditable>", html: `<div id="t" contenteditable>Copy</div>`, target: "#t" },
    {
      name: '<div contenteditable tabindex="0">',
      html: `<div id="t" contenteditable tabindex="0">Copy</div>`,
      target: "#t",
    },
    // test-break — the rows that tell AC1's terms apart from the
    // over-corrections every row above accepts. Expected is
    // `tabbableWithin`'s own answer, measured, not read off the spec.
    {
      name: "the <summary> of an open <details>",
      html: `<details open><summary id="t">Reason</summary>body</details>`,
      target: "#t",
    },
    {
      name: "a <button> under a non-fieldset [disabled] ancestor",
      html: `<div disabled><button id="t">Copy</button></div>`,
      target: "#t",
    },
    {
      name: 'a <button> under an aria-hidden="true" ancestor',
      html: `<div aria-hidden="true"><button id="t">Copy</button></div>`,
      target: "#t",
    },
    {
      name: '<button aria-disabled="false">',
      html: `<button id="t" aria-disabled="false">Copy</button>`,
      target: "#t",
    },
    {
      name: '<button aria-hidden="false">',
      html: `<button id="t" aria-hidden="false">Copy</button>`,
      target: "#t",
    },
    {
      // An UNNAMED radio is a group of one, so the two must agree even in
      // the one family where they may differ.
      name: "an unnamed <input type=radio>",
      html: `<input type="radio" id="t">`,
      target: "#t",
    },
    {
      // The inaccuracy both sides SHARE, so nobody repairs one of them: the
      // HTML spec leaves a `<legend>` control of a disabled fieldset enabled.
      name: "a <button> in the <legend> of a <fieldset disabled>",
      html: `<fieldset disabled><legend><button id="t">Copy</button></legend></fieldset>`,
      target: "#t",
    },
  ];

  function fixture(html: string, target: string): { wrapper: HTMLElement; el: HTMLElement } {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    const el = wrapper.querySelector<HTMLElement>(target);
    if (!el) throw new Error(`${target} is not in this row's markup`);
    return { wrapper, el };
  }

  it("exports isTabbable from the detector", () => {
    // Without the export every row below would throw rather than compare, and
    // a table that cannot run is not a guard. This case names the reason.
    expect(typeof isTabbable).toBe("function");
  });

  it.each(ROWS.map((row) => [row.name, row] as const))(
    "answers %s the way tabbableWithin does",
    (_name, row) => {
      const { wrapper, el } = fixture(row.html, row.target);
      expect(isTabbable!(el)).toBe(tabbableWithin(wrapper).includes(el));
    },
  );

  it("covers both outcomes, so the agreement above cannot be vacuous", () => {
    // A table on which both definitions returned false for every row would
    // satisfy every case above while checking nothing. At least one row must
    // be tabbable and at least one must not.
    const answers = ROWS.map((row) => {
      const { el } = fixture(row.html, row.target);
      return isTabbable!(el);
    });
    expect(answers).toContain(true);
    expect(answers).toContain(false);
  });

  it("agrees over the same table with the wrapper in the document", () => {
    // Every row above runs DETACHED, so a term consulting layout would
    // differ only once rendered, which the both-outcomes case cannot see.
    for (const row of ROWS) {
      const { wrapper, el } = fixture(row.html, row.target);
      const detached = isTabbable!(el);
      document.body.appendChild(wrapper);
      try {
        expect(isTabbable!(el)).toBe(detached);
        expect(isTabbable!(el)).toBe(tabbableWithin(wrapper).includes(el));
      } finally {
        wrapper.remove();
      }
    }
  });

  it("diverges from tabbableWithin on a named radio group, and only there", () => {
    // The one disagreement AC3 declares CORRECT, asserted rather than left
    // to a comment: a named group's second radio is skipped by the trap and
    // reachable to the detector. Copy `isSkippedRadio` across and every row
    // above passes while this turns red.
    const wrapper = document.createElement("div");
    wrapper.innerHTML =
      `<form><input type="radio" name="g" id="first"><input type="radio" name="g" id="second"></form>`;
    const first = wrapper.querySelector<HTMLElement>("#first")!;
    const second = wrapper.querySelector<HTMLElement>("#second")!;
    const trapped = tabbableWithin(wrapper);

    expect(trapped).toContain(first);
    expect(trapped).not.toContain(second);
    expect(isTabbable!(first)).toBe(true);
    expect(isTabbable!(second)).toBe(true);
  });
});

/**
 * test-break — AC1's "stays import-free" is a `grep` a human runs once, and
 * the drift test above is GREEN against the one change that breaks the
 * browser: importing `tabbableWithin` here and deleting the copy makes the two
 * agree by construction and throws at runtime in `scripts/visual-qa.mjs`,
 * which `new Function`s this module in a page holding nothing else from the
 * app. Neither jest nor `tsc` sees it. Read from disk, because an import graph
 * cannot see the absence of an edge.
 */
describe("usabilityCheck.ts is inlinable", () => {
  const SOURCE = readFileSync(join(__dirname, "..", "usabilityCheck.ts"), "utf8");

  it("carries no import statement, and is still what both inliners strip", () => {
    expect(SOURCE.split("\n").filter((line) => /^\s*import[\s{*]/.test(line))).toEqual([]);
    // The premise: the case above is worth asserting only because something
    // transpiles this module whole. If that stops being true, delete this
    // block rather than leave it asserting a constraint nothing imposes.
    for (const script of ["visual-qa.mjs", "probe-scrollable-region.mjs"]) {
      const inliner = readFileSync(join(__dirname, "..", "..", "..", "scripts", script), "utf8");
      expect(inliner).toContain("usabilityCheck.ts");
      expect(inliner).toContain("/^export /gm");
    }
  });

  it("reads `disabled` as an attribute, never as a property", () => {
    // AC1's second grep. The property is `undefined` on an `<a>` and false
    // in jsdom inside a `<fieldset disabled>`: wrong in both engines.
    const reads = SOURCE.split("\n").filter((line) => line.includes(".disabled"));
    expect(reads).toEqual([]);
  });
});
