import { findUsabilityProblems } from "../usabilityCheck";

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
});
