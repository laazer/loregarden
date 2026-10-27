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
 * Deliberately self-contained — one function, no imports, nothing from module
 * scope: `scripts/visual-qa.mjs` transpiles this file and evaluates the
 * function inside the browser page, where nothing else from the app exists.
 */

export type UsabilityProblemKind =
  | "repeated-text"
  | "dead-end-list"
  | "edgeless-graph"
  | "unfiltered-long-list"
  | "tabular-list"
  | "key-value-table"
  | "nested-boxes";

export interface UsabilityProblem {
  kind: UsabilityProblemKind;
  detail: string;
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
  const NARROWING = "input[type='search'], input[type='text'], select, [role='searchbox']";
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
  // A board lane (`data-board-lane`) is the one exception: its cards share a
  // shape by design, and the board, which answers "what stage is each item
  // in?", is the layout CLAUDE.md prescribes for them. The other list checks
  // still apply to it.
  for (const { el: list, items, kind } of itemLists) {
    // A tbody is already the table this check would ask for.
    if (kind === "tbody") continue;
    if (list.closest("[data-board-lane]")) continue;
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

  // Boxes inside boxes inside boxes. A box is a block that draws its own edge:
  // rounded, with a border on three sides or a fill of its own. Controls draw
  // edges too but are not regions, so they are left out, as are blocks too small
  // to be one. It needs computed styles and layout, which only a browser has;
  // under jsdom nothing is laid out, so there is nothing to measure and the
  // check stands aside rather than call every element flat.
  const body = root instanceof Document ? root.body : root instanceof Element ? root.ownerDocument.body : null;
  const view = body?.ownerDocument.defaultView;
  if (body && view && body.getBoundingClientRect().width > 0) {
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
