/**
 * Find the shapes an unusable surface takes, in rendered DOM.
 *
 * Every one of these shipped past the five-state rules and the UX gate, which
 * see only shape: the Monitor printed 62 copies of "Stage '…' ran # times…" in
 * a list with no link in it; the Memory map drew 33 records and no edge. A
 * static gate cannot see them because they appear only at production volume —
 * so this runs where production-shaped data is rendered: jest over the recorded
 * `prod-shape` fixtures (in CI), and `npm run visual-qa` inside each page.
 *
 * It flags shapes, not taste. A hit is a question for a person ("should 94 rows
 * of this be here?"); a pass is not a verdict that the surface is good.
 *
 * A list here is a run of sibling elements repeating one shape, not a `<ul>`.
 * The list checks once read `ul, ol, tbody` only, and this app renders its
 * cards as `<div>`: they saw 11 nav items on /branch-triage while the surface
 * held 224 unfilterable cards, and reported zero problems on all 19 surfaces.
 * A check that cannot see the house style is not evidence of anything.
 *
 * Deliberately import-free: `scripts/visual-qa.mjs` transpiles this file,
 * strips `^export ` and evaluates the whole module in one scope inside a
 * browser page where nothing else from the app exists. Module-scope
 * declarations survive that; an `import` does not. So the rule is no imports,
 * which is why the one predicate shared with the app is a copy (`isTabbable`
 * below) rather than a reuse.
 */

export type UsabilityProblemKind =
  | "repeated-text"
  | "dead-end-list"
  | "edgeless-graph"
  | "unfiltered-long-list"
  | "tabular-list"
  | "key-value-table"
  | "nested-boxes"
  | "scrollable-region-focusable";

export interface UsabilityProblem {
  kind: UsabilityProblemKind;
  detail: string;
}

/**
 * What the platform will hand a Tab press to, before per-element filtering.
 *
 * A corrected copy of `useDialogFocusTrap`'s `FOCUSABLE_SELECTOR`, including
 * its two recorded refinements: `[contenteditable]` is matched plain because
 * the `tabIndex >= 0` term below already excludes a non-editable one, and
 * `[tabindex]` is matched broadly and narrowed by the resolved `tabIndex`,
 * since `tabindex="-1"` is programmatically focusable but not tabbable.
 */
const FOCUSABLE_SELECTOR =
  "a[href], area[href], button, input, select, textarea, iframe, audio[controls], video[controls], [contenteditable], [tabindex]";

/**
 * Whether the platform will stop on `el` for a Tab press.
 *
 * A **copy** of `tabbableWithin`'s filter (`hooks/useDialogFocusTrap.ts`), not
 * a reuse, and deliberately so: `scripts/visual-qa.mjs` transpiles this file
 * and evaluates `findUsabilityProblems` inside a browser page where nothing
 * else from the app exists, so this module may not import. That hook is the
 * reference definition; this is the checked copy, and
 * `__tests__/usabilityCheck.test.ts` imports both and asserts they agree over
 * a fixture table so the next refinement cannot land on one side only.
 *
 * `disabled` is read as the **attribute**, never the property, because one
 * filter has to cover every tag: `<a disabled>` has no such property, and a
 * `<button>` inside `<fieldset disabled>` has it true only by way of the
 * `fieldset[disabled]` term. Reading the property reported all three of
 * `fieldset[disabled]`, `[hidden]` and `[inert]` as reachable while Chromium
 * refused focus to each, which is the whole defect this predicate fixes.
 *
 * `aria-hidden` is read on the element ITSELF rather than through `closest`,
 * matching the reference exactly; widening it here would be a new divergence
 * in the other direction.
 *
 * `isSkippedRadio` is NOT copied, and that is a decision rather than an
 * omission: a radio group is never a bounded region's only way in, and this
 * check measures whether any key reaches the region at all, not tab order.
 */
export function isTabbable(el: Element): boolean {
  return (
    el.matches(FOCUSABLE_SELECTOR) &&
    (el as HTMLElement).tabIndex >= 0 &&
    !el.hasAttribute("disabled") &&
    el.getAttribute("aria-disabled") !== "true" &&
    el.getAttribute("aria-hidden") !== "true" &&
    el.closest("fieldset[disabled]") === null &&
    el.closest("[hidden],[inert]") === null
  );
}

export function findUsabilityProblems(root: ParentNode): UsabilityProblem[] {
  /** The same sentence this many times is a wall, whatever varies inside it. */
  const REPEATED = 12;
  /** A list this long with nothing to click is a dead end. */
  const DEAD_END_ITEMS = 15;
  /** A list this long needs a way to narrow it. */
  const LONG_LIST = 50;
  const ACTIONABLE =
    "a[href], button, input, select, textarea, summary, [role='button'], [role='link'], [tabindex]";
  // A text field with no type attribute is a text field: the app's Input
  // primitive renders one, and missing it called a filtered list unfiltered.
  const NARROWING =
    "input:not([type]), input[type='search'], input[type='text'], select, [role='searchbox']";
  /**
   * Items that each repeat this many text fields are records, and records are
   * rows. A name and three short tags is still a list (an issue tracker's); a
   * name and four attributes is a row read across.
   */
  const TABULAR_FIELDS = 5;
  /** Controls hold labels and choices, not the item's fields. */
  const CONTROL = "button, select, textarea, option";
  const TABULAR_ITEMS = 8;
  /** Two-cell rows this many deep, with no column header, are one record's fields. */
  const KEY_VALUE_ROWS = 3;
  /**
   * A box inside this many other boxes is too deep. One card in a panel is a
   * region holding things you act on; a box inside that card is the third
   * level, where everything is a card and nothing is the hierarchy.
   */
  const NESTED_BOX_DEPTH = 2;

  const problems: UsabilityProblem[] = [];

  // A template repeated with different numbers or quoted names is still one
  // sentence to the reader, so digits and quoted words are folded.
  const counts = new Map<string, number>();
  for (const el of Array.from(root.querySelectorAll("li, p, td, [role='listitem']"))) {
    const text = (el.textContent ?? "")
      .replace(/\s+/g, " ")
      .replace(/(['"`])[^'"`]{1,60}\1/g, "'…'")
      .replace(/\d+(\.\d+)?/g, "#")
      .trim();
    if (text.length < 20) continue;
    counts.set(text, (counts.get(text) ?? 0) + 1);
  }
  for (const [text, count] of counts) {
    if (count > REPEATED) {
      problems.push({
        kind: "repeated-text",
        detail: `${count} elements say "${text.slice(0, 80)}" — group or fold them`,
      });
    }
  }

  // Every run of repeated siblings on the page, however it is marked up. A
  // semantic list is one; so is a container whose children repeat a tag and a
  // class and each carry text. `kind` is kept because the tabular check asks a
  // question ("compare them in a table") that is already answered for a tbody.
  type ItemList = { el: Element; items: Element[]; kind: "list" | "tbody" | "group" };
  const itemLists: ItemList[] = [];

  for (const list of Array.from(root.querySelectorAll("ul, ol, tbody"))) {
    itemLists.push({
      el: list,
      items: Array.from(list.children).filter((c) => c.tagName === "LI" || c.tagName === "TR"),
      kind: list.tagName === "TBODY" ? "tbody" : "list",
    });
  }

  // The smallest run any check below cares about; under it there is nothing to
  // find and the grouping is not worth doing.
  const MIN_RUN = TABULAR_ITEMS;
  for (const container of Array.from(root.querySelectorAll("*"))) {
    // Not inside a drawing. A graph's 50 edges are siblings repeating one shape
    // and none of them is clickable, which reads as a dead-end list of 50 items
    // — the memory map tripped exactly this. What an `<svg>` is missing is a
    // different question, and `edgeless-graph` below already asks it.
    if (container.closest("svg")) continue;
    const children = Array.from(container.children);
    if (children.length < MIN_RUN) continue;
    const runs = new Map<string, Element[]>();
    for (const child of children) {
      // The pass above owns these; counting them again would double-report.
      if (child.tagName === "LI" || child.tagName === "TR") continue;
      // The first class is the block in this tree's naming, so a card and a
      // selected card group together while a card and a toolbar do not. An
      // unclassed run is layout, not a list.
      const block = (child.getAttribute("class") ?? "").trim().split(/\s+/)[0];
      if (!block) continue;
      if (((child.textContent ?? "").trim()) === "") continue;
      const signature = `${child.tagName}.${block}`;
      const run = runs.get(signature);
      if (run) run.push(child);
      else runs.set(signature, [child]);
    }
    for (const items of runs.values()) {
      if (items.length >= MIN_RUN) itemLists.push({ el: container, items, kind: "group" });
    }
  }

  for (const { el: list, items } of itemLists) {
    if (items.length > DEAD_END_ITEMS && !items.some((item) => item.matches(ACTIONABLE) || item.querySelector(ACTIONABLE))) {
      problems.push({
        kind: "dead-end-list",
        detail: `a list of ${items.length} items with nothing to click in any of them`,
      });
    }
    if (items.length > LONG_LIST) {
      let scope: Element | null = list;
      let narrowed = false;
      for (let depth = 0; scope && depth < 4 && !narrowed; depth++) {
        narrowed = scope.querySelector(NARROWING) !== null;
        scope = scope.parentElement;
      }
      if (!narrowed) {
        problems.push({
          kind: "unfiltered-long-list",
          detail: `a list of ${items.length} items with no search or filter near it`,
        });
      }
    }
  }

  // A list item's shape is the sequence of elements that hold its text. When
  // most items share one shape of several fields, the reader is comparing the
  // same attributes down the page — that is a table without its columns.
  //
  // A board lane (`data-board-lane`) and a tree (`role="tree"`) are the
  // exceptions: their items share a shape by design, and each is the layout
  // CLAUDE.md prescribes for its question, "what stage is each item in?" for
  // the board and "what belongs under what?" for the tree. The other list
  // checks still apply to both.
  for (const { el: list, items, kind } of itemLists) {
    // A tbody is already the table this check would ask for.
    if (kind === "tbody") continue;
    if (list.closest("[data-board-lane], [role='tree']")) continue;
    if (items.length < TABULAR_ITEMS) continue;
    const shapes = new Map<string, number>();
    for (const item of items) {
      if (item.querySelector("ul, ol, table")) continue;
      const fields = [item, ...Array.from(item.querySelectorAll("*"))].filter(
        (el) =>
          !el.closest(CONTROL) &&
          Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? "").trim() !== ""),
      );
      if (fields.length < TABULAR_FIELDS) continue;
      const shape = fields.map((el) => `${el.tagName}.${el.getAttribute("class") ?? ""}`).join("|");
      shapes.set(shape, (shapes.get(shape) ?? 0) + 1);
    }
    const common = Math.max(0, ...shapes.values());
    if (common >= TABULAR_ITEMS && common >= items.length * 0.8) {
      problems.push({
        kind: "tabular-list",
        detail: `${common} list items repeat the same ${TABULAR_FIELDS}+ fields — compare them in a table`,
      });
    }
  }

  // Label/value rows with no column header describe one record: a <dl>.
  for (const table of Array.from(root.querySelectorAll("table"))) {
    const rows = Array.from(table.querySelectorAll("tr"));
    const headed = rows.some((row) => Array.from(row.children).every((cell) => cell.tagName === "TH"));
    if (rows.length >= KEY_VALUE_ROWS && !headed && rows.every((row) => row.children.length === 2)) {
      problems.push({
        kind: "key-value-table",
        detail: `a ${rows.length}-row table of label/value pairs — one record's fields belong in a <dl>`,
      });
    }
  }

  // One fact about the document, asked once: is there a layout engine here at
  // all? Not "does this element measure zero", which cannot tell a browser
  // that lays nothing out from a browser that lays out a hidden element — the
  // body is laid out whatever any one region reports. Both checks below need
  // it, and both need the same answer: a sweep where half the elements are
  // measured and half are read off declarations is two checks, not one.
  const doc = root instanceof Document ? root : root instanceof Element ? root.ownerDocument : null;
  const view = doc?.defaultView ?? null;
  const body = doc?.body ?? null;
  const laidOut = Boolean(body && view && body.getBoundingClientRect().width > 0);

  // Boxes inside boxes inside boxes. A box is a block that draws its own edge:
  // rounded, with a border on three sides or a fill of its own. Controls draw
  // edges too but are not regions, so they are left out, as are blocks too small
  // to be one. It needs computed styles and layout, which only a browser has;
  // under jsdom nothing is laid out, so there is nothing to measure and the
  // check stands aside rather than call every element flat.
  // (`view` again because `laidOut` is a boolean and does not narrow it.)
  if (laidOut && view) {
    const CLEAR = /^(transparent|rgba\([^)]*,\s*0\))$/;
    const NOT_A_REGION = "button, input, select, textarea, a, img, code, pre, kbd, label, svg, svg *";
    const isBox = (el: Element): boolean => {
      if (el.matches(NOT_A_REGION)) return false;
      const style = view.getComputedStyle(el);
      if (style.display === "inline" || style.display === "contents" || style.visibility === "hidden") return false;
      // Written as "not at least 4" so an unset radius (NaN) is square, not a box.
      if (!(parseFloat(style.borderTopLeftRadius) >= 4)) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width < 120 || rect.height < 24) return false;
      const edges = (["Top", "Right", "Bottom", "Left"] as const).filter(
        (side) =>
          parseFloat(style.getPropertyValue(`border-${side.toLowerCase()}-width`)) >= 1 &&
          style.getPropertyValue(`border-${side.toLowerCase()}-style`) !== "none" &&
          !CLEAR.test(style.getPropertyValue(`border-${side.toLowerCase()}-color`)),
      ).length;
      return edges >= 3 || (style.backgroundColor !== "" && !CLEAR.test(style.backgroundColor));
    };
    const boxes = new Set(Array.from(root.querySelectorAll("*")).filter(isBox));
    const deep = new Map<string, number>();
    for (const box of boxes) {
      let depth = 0;
      for (let up = box.parentElement; up; up = up.parentElement) if (boxes.has(up)) depth++;
      if (depth < NESTED_BOX_DEPTH) continue;
      const name = (box.getAttribute("class") ?? "").trim().split(/\s+/)[0] || box.tagName.toLowerCase();
      deep.set(name, (deep.get(name) ?? 0) + 1);
    }
    if (deep.size > 0) {
      const total = Array.from(deep.values()).reduce((sum, n) => sum + n, 0);
      const names = Array.from(deep.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([name, n]) => `.${name} ×${n}`)
        .join(", ");
      problems.push({
        kind: "nested-boxes",
        detail: `${total} ${total === 1 ? "box sits" : "boxes sit"} inside two or more others (${names}) — keep one bordered level per region; make the inner ones rows`,
      });
    }
  }

  // A height-bounded region with a scrolling overflow that neither takes focus
  // nor holds anything that can: the scrollbar is visible and no key moves it,
  // because the arrow keys, Page Up/Down and Home/End scroll whichever
  // scrollable ancestor *has* focus. axe-core's `scrollable-region-focusable`
  // rule and WCAG 2.1.1 are about this, and a screenshot review passes it
  // because the reviewer already has a mouse.
  //
  // The branch below turns on whether a layout engine exists, not on what one
  // element measures. Where there is one, two things are asked of the region
  // and both are the engine's answer rather than the stylesheet's: does it
  // actually clip, on an axis that actually scrolls (axe-core's own test,
  // asked per axis — `scrollHeight > clientHeight` where the vertical axis has
  // a scrollbar, `scrollWidth > clientWidth` where the horizontal one does,
  // since a region clipped sideways is reachable by no key either, which
  // `nested-boxes` above precedents by reading geometry rather than
  // declarations), and does the browser render it at all. A declared bound is
  // no evidence there: `getComputedStyle().height` is the *used* height in px
  // for every laid-out element, so "height is not none/auto" is true almost
  // everywhere, and reading it as a bound reported 51 findings over 24 sandbox
  // surfaces every one of which had `scrollHeight === clientHeight`.
  //
  // The other side is "no layout engine (jsdom)", not "this element happens to
  // measure zero". There nothing is laid out and no stylesheet is read, both
  // geometries are 0 and there is no overflow to measure, so the declared
  // bound is all there is: take it, but require the region to hold something
  // rendered, since an empty 0-height box clips nothing. Like `nested-boxes`
  // it stands aside where there is nothing to read at all.
  if (view) {
    /** No height at all to exceed, so nothing is clipped and nothing scrolls. */
    const UNBOUND = new Set(["", "none", "auto"]);
    const SCROLLS = new Set(["auto", "scroll"]);
    /** An empty box carries no text a key would be needed to reach. */
    const renders = (el: Element): boolean =>
      el.children.length > 0 || (el.textContent ?? "").trim() !== "";
    /**
     * Does the browser render this region — asked of the browser, because
     * geometry cannot answer it. A **closed** `<details>` holding a bounded
     * pane reports clientHeight 60 / scrollHeight 1440, laid out and clipping,
     * the same numbers as the open one
     * (`scripts/probe-scrollable-region.mjs` measures both). `display: none`
     * on an ancestor is the opposite trap: geometry is 0 while
     * `getComputedStyle` still reports the *computed* `max-height` and
     * `overflow`, so a declaration-reading check names a region that clips
     * nothing.
     *
     * `visibilityProperty` is load-bearing rather than decorative: the bare
     * call returns `true` for a `visibility: hidden` pane and the option makes
     * it `false`. Feature-detected because jsdom 26.1.0 implements no
     * `checkVisibility`, and where it cannot be asked the answer is `true`: a
     * detector whose job is to report must never go silent on "cannot tell".
     */
    const rendered = (el: Element): boolean =>
      typeof el.checkVisibility !== "function" || el.checkVisibility({ visibilityProperty: true });
    /**
     * Does *this* axis have a scrollbar — the question each geometry
     * comparison below has to be gated on, because the admission test above
     * takes either axis and a region bounded sideways is not thereby bounded
     * downwards. `hidden`, `clip` and `visible` are the three spellings of
     * "no scrollbar here", so a 1440px `scrollHeight` under `overflow-y:
     * hidden` is content cut off rather than content a key could reach.
     *
     * The per-axis computed property answers it wherever there is one, which
     * in a browser is always: Chromium expands the shorthand, so
     * `overflow-x: auto; overflow-y: hidden` computes `overflowX` `"auto"`
     * and `overflowY` `"hidden"` and the fallback below never runs.
     */
    const axisScrolls = (style: CSSStyleDeclaration, axis: "x" | "y"): boolean => {
      const own = axis === "x" ? style.overflowX : style.overflowY;
      if (own !== "") return SCROLLS.has(own);
      // Only reachable under jsdom 26.1.0, which expands nothing: `overflow:
      // auto hidden` computes the shorthand to `"auto hidden"` with BOTH axis
      // properties `""`. Hence the per-axis *word* rather than the shorthand
      // as one value — `SCROLLS.has("auto hidden")` is false, which would read
      // both axes as unscrollable in the one engine this arm exists for. One
      // word applies to both axes; two are `[x, y]`.
      const words = style.overflow.trim().split(/\s+/);
      return SCROLLS.has((axis === "x" ? words[0] : (words[1] ?? words[0])) ?? "");
    };
    for (const el of Array.from(root.querySelectorAll("*"))) {
      const style = view.getComputedStyle(el);
      if (![style.overflow, style.overflowX, style.overflowY].some((value) => SCROLLS.has(value))) continue;
      if (laidOut) {
        const clipsVertically = axisScrolls(style, "y") && el.scrollHeight > el.clientHeight;
        const clipsHorizontally = axisScrolls(style, "x") && el.scrollWidth > el.clientWidth;
        if (!clipsVertically && !clipsHorizontally) continue;
        if (!rendered(el)) continue;
      } else {
        if (UNBOUND.has(style.maxHeight) && UNBOUND.has(style.height)) continue;
        if (!renders(el)) continue;
      }
      if (isTabbable(el)) continue;
      if (Array.from(el.querySelectorAll(FOCUSABLE_SELECTOR)).some(isTabbable)) continue;
      const name = (el.getAttribute("class") ?? "").trim().split(/\s+/)[0] || el.tagName.toLowerCase();
      problems.push({
        kind: "scrollable-region-focusable",
        detail: `.${name} clips its content and scrolls, but neither it nor anything inside it can take focus — no key reaches it`,
      });
    }
  }

  for (const svg of Array.from(root.querySelectorAll("svg"))) {
    const nodes = svg.querySelectorAll("[role='button']").length;
    const edges = svg.querySelectorAll("line, path").length;
    if (nodes >= 5 && edges === 0) {
      problems.push({
        kind: "edgeless-graph",
        detail: `a graph of ${nodes} nodes with no edges — it shows nothing a list would not`,
      });
    }
  }

  return problems;
}
